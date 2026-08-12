import logging
from datetime import datetime
from app import db
from app.models.cloud_server import CloudProvider, CloudServer, CloudSnapshot
from app.utils.crypto import encrypt_secret, decrypt_secret_safe, is_encrypted

logger = logging.getLogger(__name__)


class CloudProvisioningService:
    """Service for provisioning cloud servers via provider APIs."""

    SUPPORTED_PROVIDERS = {
        'digitalocean': {
            'name': 'DigitalOcean',
            'regions': ['nyc1', 'nyc3', 'sfo3', 'ams3', 'lon1', 'fra1', 'sgp1', 'blr1', 'tor1', 'syd1'],
            'sizes': ['s-1vcpu-1gb', 's-1vcpu-2gb', 's-2vcpu-2gb', 's-2vcpu-4gb', 's-4vcpu-8gb', 's-8vcpu-16gb'],
            'images': ['ubuntu-22-04-x64', 'ubuntu-24-04-x64', 'debian-12-x64', 'centos-stream-9-x64', 'rocky-9-x64'],
        },
        'hetzner': {
            'name': 'Hetzner Cloud',
            'regions': ['nbg1', 'fsn1', 'hel1', 'ash', 'hil'],
            'sizes': ['cx22', 'cx32', 'cx42', 'cx52', 'cpx11', 'cpx21', 'cpx31'],
            'images': ['ubuntu-22.04', 'ubuntu-24.04', 'debian-12', 'centos-stream-9', 'rocky-9'],
        },
        'vultr': {
            'name': 'Vultr',
            'regions': ['ewr', 'ord', 'dfw', 'sea', 'lax', 'atl', 'ams', 'lhr', 'fra', 'nrt', 'icn', 'sgp', 'syd'],
            'sizes': ['vc2-1c-1gb', 'vc2-1c-2gb', 'vc2-2c-4gb', 'vc2-4c-8gb', 'vc2-6c-16gb'],
            'images': ['Ubuntu 22.04', 'Ubuntu 24.04', 'Debian 12', 'CentOS Stream 9'],
        },
        'linode': {
            'name': 'Linode (Akamai)',
            'regions': ['us-east', 'us-central', 'us-west', 'eu-west', 'eu-central', 'ap-south', 'ap-northeast', 'ap-southeast'],
            'sizes': ['g6-nanode-1', 'g6-standard-1', 'g6-standard-2', 'g6-standard-4', 'g6-standard-6'],
            'images': ['linode/ubuntu22.04', 'linode/ubuntu24.04', 'linode/debian12'],
        },
    }

    # --- Providers ---

    @staticmethod
    def list_providers():
        return CloudProvider.query.filter_by(is_active=True).all()

    @staticmethod
    def get_provider(provider_id):
        return CloudProvider.query.get(provider_id)

    @staticmethod
    def create_provider(data, user_id=None):
        ptype = data.get('provider_type')
        if ptype not in CloudProvisioningService.SUPPORTED_PROVIDERS:
            raise ValueError(f'Unsupported provider: {ptype}')

        provider = CloudProvider(
            name=data.get('name', CloudProvisioningService.SUPPORTED_PROVIDERS[ptype]['name']),
            provider_type=ptype,
            api_key_encrypted=encrypt_secret(data['api_key']) if data.get('api_key') else '',
            created_by=user_id,
        )
        db.session.add(provider)
        db.session.commit()
        return provider

    @staticmethod
    def delete_provider(provider_id):
        provider = CloudProvider.query.get(provider_id)
        if not provider:
            return False
        provider.is_active = False
        db.session.commit()
        return True

    @staticmethod
    def get_provider_options(provider_type):
        return CloudProvisioningService.SUPPORTED_PROVIDERS.get(provider_type, {})

    @staticmethod
    def encrypt_legacy_secrets():
        """One-time, idempotent: encrypt any cloud-provider API tokens still stored
        in plaintext (the column predates encryption-at-rest)."""
        changed = 0
        for provider in CloudProvider.query.all():
            if provider.api_key_encrypted and not is_encrypted(provider.api_key_encrypted):
                provider.api_key_encrypted = encrypt_secret(provider.api_key_encrypted)
                changed += 1
        if changed:
            db.session.commit()
        return changed

    # --- Servers ---

    @staticmethod
    def list_servers(provider_id=None):
        query = CloudServer.query.filter(CloudServer.status != CloudServer.STATUS_DESTROYED)
        if provider_id:
            query = query.filter_by(provider_id=provider_id)
        return query.order_by(CloudServer.created_at.desc()).all()

    @staticmethod
    def get_server(server_id):
        return CloudServer.query.get(server_id)

    @staticmethod
    def create_server(data, user_id=None):
        """Provision a new cloud server."""
        provider = CloudProvider.query.get(data['provider_id'])
        if not provider:
            raise ValueError('Provider not found')

        server = CloudServer(
            provider_id=provider.id,
            name=data['name'],
            region=data.get('region'),
            size=data.get('size'),
            image=data.get('image'),
            ssh_key_id=data.get('ssh_key_id'),
            created_by=user_id,
        )
        db.session.add(server)
        db.session.commit()

        # Call provider API to create server
        try:
            result = CloudProvisioningService._provider_create(provider, server, data)
            server.external_id = result.get('id')
            server.ip_address = result.get('ip_address')
            server.ipv6_address = result.get('ipv6_address')
            server.monthly_cost = result.get('monthly_cost', 0)
            server.status = CloudServer.STATUS_ACTIVE
            server.hostname = result.get('hostname', server.name)
            db.session.commit()
        except Exception as e:
            server.status = CloudServer.STATUS_ERROR
            server.server_metadata = {'error': str(e)}
            db.session.commit()
            raise

        # Auto-install agent if requested
        if data.get('install_agent') and server.ip_address:
            try:
                CloudProvisioningService._install_agent(server)
                server.agent_installed = True
                db.session.commit()
            except Exception as e:
                logger.warning(f'Agent install failed for {server.name}: {e}')

        return server

    @staticmethod
    def destroy_server(server_id):
        """Destroy a server at its provider and record it locally.

        Returns False when there is no such server. Propagates the provider error
        when the remote delete is refused — the local row is NOT marked destroyed
        in that case. Recording a destroy that did not happen is the more dangerous
        of the two failure modes: the operator stops seeing the server while it
        keeps running and billing, and nothing in the panel ever mentions it again.
        """
        server = CloudServer.query.get(server_id)
        if not server:
            return False

        # Already destroyed: converge without touching the provider again.
        if server.status == CloudServer.STATUS_DESTROYED:
            return True

        # Deliberately unguarded — a refused delete must reach the caller so the
        # API can report it and the row stays visible for a retry.
        CloudProvisioningService._provider_destroy(server.provider, server)

        server.status = CloudServer.STATUS_DESTROYED
        server.destroyed_at = datetime.utcnow()
        db.session.commit()
        return True

    @staticmethod
    def resize_server(server_id, new_size):
        server = CloudServer.query.get(server_id)
        if not server:
            return None
        try:
            CloudProvisioningService._provider_resize(server.provider, server, new_size)
            server.size = new_size
            db.session.commit()
            return server
        except Exception as e:
            raise ValueError(f'Resize failed: {e}')

    # --- Snapshots ---

    @staticmethod
    def create_snapshot(server_id, name):
        server = CloudServer.query.get(server_id)
        if not server:
            raise ValueError('Server not found')

        snapshot = CloudSnapshot(
            server_id=server_id,
            name=name,
        )
        db.session.add(snapshot)
        db.session.commit()

        try:
            result = CloudProvisioningService._provider_snapshot(server.provider, server, name)
            snapshot.external_id = result.get('id')
            snapshot.size_gb = result.get('size_gb')
            snapshot.status = 'available'
            db.session.commit()
        except Exception as e:
            snapshot.status = 'error'
            db.session.commit()
            raise

        return snapshot

    @staticmethod
    def get_snapshots(server_id):
        return CloudSnapshot.query.filter_by(server_id=server_id).order_by(CloudSnapshot.created_at.desc()).all()

    @staticmethod
    def delete_snapshot(snapshot_id):
        snapshot = CloudSnapshot.query.get(snapshot_id)
        if not snapshot:
            return False
        # Delete from cloud provider first
        try:
            server = snapshot.server
            if server and server.provider:
                CloudProvisioningService._provider_delete_snapshot(
                    server.provider, server, snapshot)
        except Exception as e:
            logger.warning(f'Provider snapshot delete failed: {e}')
        db.session.delete(snapshot)
        db.session.commit()
        return True

    @staticmethod
    def get_cost_summary():
        """Get total monthly cost across all active servers."""
        servers = CloudServer.query.filter(
            CloudServer.status.in_([CloudServer.STATUS_ACTIVE, CloudServer.STATUS_OFF])
        ).all()

        by_provider = {}
        total = 0
        for s in servers:
            key = s.provider.name if s.provider else 'Unknown'
            by_provider.setdefault(key, {'count': 0, 'cost': 0})
            by_provider[key]['count'] += 1
            by_provider[key]['cost'] += s.monthly_cost or 0
            total += s.monthly_cost or 0

        return {
            'total_monthly': round(total, 2),
            'server_count': len(servers),
            'by_provider': by_provider,
        }

    # --- Provider API helpers ---

    @staticmethod
    def _auth_headers(provider):
        return {
            'Authorization': f'Bearer {decrypt_secret_safe(provider.api_key_encrypted)}',
            'Content-Type': 'application/json',
        }

    # Vultr uses os_id integers instead of image name strings
    VULTR_OS_MAP = {
        'Ubuntu 22.04': 1743,
        'Ubuntu 24.04': 2284,
        'Debian 12': 2136,
        'CentOS Stream 9': 2072,
    }

    # --- Provider API calls ---

    @staticmethod
    def _provider_create(provider, server, data):
        """Call provider API to create server. Returns dict with id, ip_address, etc."""
        import requests
        ptype = provider.provider_type
        headers = CloudProvisioningService._auth_headers(provider)

        if ptype == 'digitalocean':
            resp = requests.post('https://api.digitalocean.com/v2/droplets', json={
                'name': server.name,
                'region': server.region,
                'size': server.size,
                'image': server.image,
                'ssh_keys': [data.get('ssh_key_id')] if data.get('ssh_key_id') else [],
            }, headers=headers, timeout=30)
            resp.raise_for_status()
            droplet = resp.json().get('droplet', {})
            networks = droplet.get('networks', {})
            ipv4 = next((n['ip_address'] for n in networks.get('v4', []) if n['type'] == 'public'), None)
            return {
                'id': str(droplet.get('id')),
                'ip_address': ipv4,
                'monthly_cost': droplet.get('size', {}).get('price_monthly', 0),
            }

        elif ptype == 'hetzner':
            resp = requests.post('https://api.hetzner.cloud/v1/servers', json={
                'name': server.name,
                'server_type': server.size,
                'image': server.image,
                'location': server.region,
            }, headers=headers, timeout=30)
            resp.raise_for_status()
            srv = resp.json().get('server', {})
            return {
                'id': str(srv.get('id')),
                'ip_address': srv.get('public_net', {}).get('ipv4', {}).get('ip'),
            }

        elif ptype == 'vultr':
            os_id = CloudProvisioningService.VULTR_OS_MAP.get(server.image, 1743)
            payload = {
                'region': server.region,
                'plan': server.size,
                'os_id': os_id,
                'label': server.name,
                'hostname': server.name,
            }
            if data.get('ssh_key_id'):
                payload['sshkey_id'] = [data['ssh_key_id']]
            resp = requests.post('https://api.vultr.com/v2/instances', json=payload,
                                 headers=headers, timeout=30)
            resp.raise_for_status()
            instance = resp.json().get('instance', {})
            return {
                'id': instance.get('id', ''),
                'ip_address': instance.get('main_ip'),
                'ipv6_address': instance.get('v6_main_ip'),
                'monthly_cost': instance.get('monthly_cost', 0),
            }

        elif ptype == 'linode':
            payload = {
                'region': server.region,
                'type': server.size,
                'image': server.image,
                'label': server.name,
                'booted': True,
            }
            if data.get('root_pass'):
                payload['root_pass'] = data['root_pass']
            if data.get('ssh_key_id'):
                payload['authorized_keys'] = [data['ssh_key_id']]
            resp = requests.post('https://api.linode.com/v4/linode/instances', json=payload,
                                 headers=headers, timeout=30)
            resp.raise_for_status()
            linode = resp.json()
            ipv4_list = linode.get('ipv4', [])
            # Fetch monthly cost from the type endpoint
            monthly_cost = 0
            try:
                type_resp = requests.get(
                    f'https://api.linode.com/v4/linode/types/{server.size}',
                    headers=headers, timeout=10)
                if type_resp.ok:
                    monthly_cost = type_resp.json().get('price', {}).get('monthly', 0)
            except Exception:
                pass
            return {
                'id': str(linode.get('id', '')),
                'ip_address': ipv4_list[0] if ipv4_list else None,
                'ipv6_address': linode.get('ipv6'),
                'monthly_cost': monthly_cost,
            }

        raise ValueError(f'Unsupported provider type: {ptype}')

    @staticmethod
    def _provider_destroy(provider, server):
        """Delete the server at the cloud provider.

        Raises on a refused delete, matching every sibling ``_provider_*`` helper
        (create/resize/snapshot all call ``raise_for_status``). This one used to
        discard the response entirely, so a 401/403/429/5xx read exactly like a
        success and :meth:`destroy_server` recorded the server as destroyed while
        it kept running — and kept billing.
        """
        if not server.external_id:
            # Never reached the provider (creation failed before it got an ID), so
            # there is nothing remote to delete; the local row is the whole story.
            return
        import requests
        ptype = provider.provider_type
        headers = CloudProvisioningService._auth_headers(provider)

        def _check(resp):
            # 404 means it is already gone remotely, which IS the desired end
            # state — treat it as success, or a retry after a partial failure
            # could never converge.
            if resp.status_code == 404:
                return
            resp.raise_for_status()

        if ptype == 'digitalocean':
            _check(requests.delete(
                f'https://api.digitalocean.com/v2/droplets/{server.external_id}',
                headers=headers, timeout=30))

        elif ptype == 'hetzner':
            _check(requests.delete(
                f'https://api.hetzner.cloud/v1/servers/{server.external_id}',
                headers=headers, timeout=30))

        elif ptype == 'vultr':
            _check(requests.delete(
                f'https://api.vultr.com/v2/instances/{server.external_id}',
                headers=headers, timeout=30))

        elif ptype == 'linode':
            _check(requests.delete(
                f'https://api.linode.com/v4/linode/instances/{server.external_id}',
                headers=headers, timeout=30))

        else:
            raise ValueError(f'Unsupported provider type: {ptype}')

    @staticmethod
    def _provider_resize(provider, server, new_size):
        """Resize a server to a new plan/type."""
        if not server.external_id:
            raise ValueError('Server has no external ID')
        import requests
        ptype = provider.provider_type
        headers = CloudProvisioningService._auth_headers(provider)

        if ptype == 'digitalocean':
            resp = requests.post(
                f'https://api.digitalocean.com/v2/droplets/{server.external_id}/actions',
                json={'type': 'resize', 'size': new_size, 'disk': True},
                headers=headers, timeout=30)
            resp.raise_for_status()

        elif ptype == 'hetzner':
            resp = requests.post(
                f'https://api.hetzner.cloud/v1/servers/{server.external_id}/actions/change_type',
                json={'server_type': new_size, 'upgrade_disk': True},
                headers=headers, timeout=30)
            resp.raise_for_status()

        elif ptype == 'vultr':
            resp = requests.patch(
                f'https://api.vultr.com/v2/instances/{server.external_id}',
                json={'plan': new_size},
                headers=headers, timeout=30)
            resp.raise_for_status()

        elif ptype == 'linode':
            resp = requests.post(
                f'https://api.linode.com/v4/linode/instances/{server.external_id}/resize',
                json={'type': new_size, 'allow_auto_disk_resize': True},
                headers=headers, timeout=30)
            resp.raise_for_status()

        else:
            raise ValueError(f'Unsupported provider type: {ptype}')

    @staticmethod
    def _provider_snapshot(provider, server, name):
        """Create a snapshot via the provider API. Returns dict with id, size_gb."""
        if not server.external_id:
            raise ValueError('Server has no external ID')
        import requests
        ptype = provider.provider_type
        headers = CloudProvisioningService._auth_headers(provider)

        if ptype == 'digitalocean':
            resp = requests.post(
                f'https://api.digitalocean.com/v2/droplets/{server.external_id}/actions',
                json={'type': 'snapshot', 'name': name},
                headers=headers, timeout=30)
            resp.raise_for_status()
            # Snapshot ID isn't in the action response; fetch from droplet snapshots
            try:
                snap_resp = requests.get(
                    f'https://api.digitalocean.com/v2/droplets/{server.external_id}/snapshots',
                    headers=headers, timeout=15)
                if snap_resp.ok:
                    snapshots = snap_resp.json().get('snapshots', [])
                    if snapshots:
                        latest = snapshots[-1]
                        return {
                            'id': str(latest.get('id')),
                            'size_gb': latest.get('size_gigabytes', 0),
                        }
            except Exception:
                pass
            return {'id': None, 'size_gb': 0}

        elif ptype == 'hetzner':
            resp = requests.post(
                f'https://api.hetzner.cloud/v1/servers/{server.external_id}/actions/create_image',
                json={'type': 'snapshot', 'description': name},
                headers=headers, timeout=30)
            resp.raise_for_status()
            image = resp.json().get('image', {})
            return {
                'id': str(image.get('id', '')),
                'size_gb': image.get('disk_size', 0),
            }

        elif ptype == 'vultr':
            resp = requests.post('https://api.vultr.com/v2/snapshots', json={
                'instance_id': server.external_id,
                'description': name,
            }, headers=headers, timeout=30)
            resp.raise_for_status()
            snap = resp.json().get('snapshot', {})
            return {
                'id': snap.get('id', ''),
                'size_gb': round(snap.get('size', 0) / (1024 ** 3), 2) if snap.get('size') else 0,
            }

        elif ptype == 'linode':
            resp = requests.post(
                f'https://api.linode.com/v4/linode/instances/{server.external_id}/backups',
                json={'label': name},
                headers=headers, timeout=30)
            resp.raise_for_status()
            backup = resp.json()
            return {
                'id': str(backup.get('id', '')),
                'size_gb': 0,
            }

        raise ValueError(f'Unsupported provider type: {ptype}')

    @staticmethod
    def _provider_delete_snapshot(provider, server, snapshot):
        """Delete a snapshot from the provider."""
        if not snapshot.external_id:
            return
        import requests
        ptype = provider.provider_type
        headers = CloudProvisioningService._auth_headers(provider)

        if ptype == 'digitalocean':
            requests.delete(
                f'https://api.digitalocean.com/v2/snapshots/{snapshot.external_id}',
                headers=headers, timeout=30)

        elif ptype == 'hetzner':
            requests.delete(
                f'https://api.hetzner.cloud/v1/images/{snapshot.external_id}',
                headers=headers, timeout=30)

        elif ptype == 'vultr':
            requests.delete(
                f'https://api.vultr.com/v2/snapshots/{snapshot.external_id}',
                headers=headers, timeout=30)

        elif ptype == 'linode':
            requests.delete(
                f'https://api.linode.com/v4/linode/instances/{server.external_id}/backups/{snapshot.external_id}',
                headers=headers, timeout=30)

    @staticmethod
    def _install_agent(server):
        """SSH into server and run the ServerKit agent install script."""
        import subprocess
        if not server.ip_address:
            raise ValueError('Server has no IP address')
        # Use ssh with strict host key checking disabled for fresh servers
        install_cmd = 'curl -fsSL https://get.serverkit.dev/agent.sh | bash'
        result = subprocess.run(
            ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=30',
             f'root@{server.ip_address}', install_cmd],
            capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f'Agent install failed: {result.stderr}')
