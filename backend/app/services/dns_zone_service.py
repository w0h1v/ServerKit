import json
import logging
from datetime import datetime
from app import db
from app.models.dns_zone import DNSZone, DNSRecord

logger = logging.getLogger(__name__)


class DNSZoneService:
    """Service for DNS zone and record management."""

    RECORD_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'SRV', 'CAA', 'NS']

    DNS_PRESETS = {
        'web-hosting': {
            'label': 'Standard Web Hosting',
            'records': [
                {'record_type': 'A', 'name': '@', 'content': '{{server_ip}}', 'ttl': 3600},
                {'record_type': 'A', 'name': 'www', 'content': '{{server_ip}}', 'ttl': 3600},
                {'record_type': 'CNAME', 'name': 'mail', 'content': '{{domain}}', 'ttl': 3600},
                # Authorize only Let's Encrypt (what ServerKit issues with) to mint
                # certs for this domain. Satisfies CAA scanners and blocks rogue CAs.
                {'record_type': 'CAA', 'name': '@', 'content': '0 issue "letsencrypt.org"', 'ttl': 3600},
            ],
        },
        'email-hosting': {
            'label': 'Email Hosting',
            'records': [
                {'record_type': 'MX', 'name': '@', 'content': 'mail.{{domain}}', 'priority': 10, 'ttl': 3600},
                {'record_type': 'TXT', 'name': '@', 'content': 'v=spf1 mx -all', 'ttl': 3600},
                {'record_type': 'TXT', 'name': '_dmarc', 'content': 'v=DMARC1; p=quarantine; rua=mailto:dmarc@{{domain}}', 'ttl': 3600},
            ],
        },
    }

    @staticmethod
    def list_zones():
        return DNSZone.query.order_by(DNSZone.domain).all()

    @staticmethod
    def list_portfolio():
        """Every domain visible across connected DNS providers, merged with the
        locally-adopted zones — the data behind the Domains-page portfolio.

        For each connected ``DNSProviderConfig`` we read its account's zones live and
        tag each with whether ServerKit has already adopted it (so the UI can deep
        link straight to records / Cloudflare ops). A provider whose token can't
        enumerate zones (e.g. a single-zone scoped token) is reported under
        ``errors`` instead of silently showing nothing, so the UI can prompt for
        broader access.

        Returns ``{domains, providers, errors}``. Note: the underlying Cloudflare
        client lists the first page only, so accounts with very many zones may be
        partial — adoption still works for any domain typed in elsewhere.
        """
        from app.models.email import DNSProviderConfig
        from app.services.dns_provider_service import DNSProviderService

        # Adopted zones, keyed by bare domain, so a provider zone resolves its row.
        local_by_domain = {}
        for z in DNSZone.query.all():
            local_by_domain.setdefault((z.domain or '').lower().rstrip('.'), z)

        domains, errors, providers_out = [], [], []
        seen = set()  # (provider, domain) — dedupe a domain shared across configs
        for cfg in DNSProviderConfig.query.order_by(DNSProviderConfig.name).all():
            providers_out.append({'id': cfg.id, 'name': cfg.name, 'provider': cfg.provider})
            zres = DNSProviderService.list_zones(cfg.id)
            if not zres.get('success'):
                errors.append({
                    'config_id': cfg.id, 'config_name': cfg.name,
                    'provider': cfg.provider, 'error': zres.get('error', 'unknown error'),
                })
                continue
            zones = zres.get('zones', [])
            registrar = DNSZoneService._cf_registrar_map(cfg, zones)
            for z in zones:
                name = (z.get('name') or '').strip().lower().rstrip('.')
                if not name or (cfg.provider, name) in seen:
                    continue
                seen.add((cfg.provider, name))
                local = local_by_domain.get(name)
                reg = registrar.get(name) or {}
                domains.append({
                    'domain': name,
                    'provider': cfg.provider,
                    'provider_zone_id': z.get('id'),
                    'status': z.get('status'),
                    'config_id': cfg.id,
                    'config_name': cfg.name,
                    'adopted': local is not None,
                    'zone_id': local.id if local else None,
                    'record_count': local.records.count() if local else None,
                    # Registration expiry / auto-renew — present only when the domain is
                    # registered at Cloudflare and the token can read Registrar.
                    'expires_at': reg.get('expires_at'),
                    'auto_renew': reg.get('auto_renew'),
                    'registrar': reg.get('registrar'),
                })

        # Fill expiry/registrar from the persisted RDAP cache for any domain a
        # provider didn't supply (most domains are registered outside their DNS
        # provider) — so a lookup done in the drawer shows up in the list after.
        from app.models.domain_registration import DomainRegistration
        cache = {r.domain: r for r in DomainRegistration.query.all()}
        for d in domains:
            if not d.get('expires_at'):
                cached = cache.get(d['domain'])
                if cached and cached.expires_at:
                    d['expires_at'] = cached.expires_at.isoformat()
                    if not d.get('registrar'):
                        d['registrar'] = cached.registrar

        domains.sort(key=lambda d: d['domain'])
        return {'domains': domains, 'providers': providers_out, 'errors': errors}

    @staticmethod
    def _cf_registrar_map(config, zones):
        """Map ``domain -> {expires_at, auto_renew, registrar}`` from Cloudflare
        Registrar for the accounts that own ``zones``. Best-effort: returns ``{}`` for
        non-Cloudflare providers, or when the token can't read Registrar (a DNS-only
        scope) — the portfolio then simply shows no expiry for those domains."""
        if config.provider != 'cloudflare':
            return {}
        try:
            from app.services.dns import CloudflareClient
            from app.services.dns.base import DnsCredential
            client = CloudflareClient(DnsCredential.from_provider_config(config))
            out = {}
            for account_id in {z.get('account_id') for z in zones if z.get('account_id')}:
                res = client.list_registrar_domains(account_id)
                if res.get('success'):
                    for d in res.get('domains', []):
                        out[d['name']] = d
            return out
        except Exception:
            return {}

    @staticmethod
    def get_zone(zone_id):
        return DNSZone.query.get(zone_id)

    @staticmethod
    def create_zone(data):
        domain = data.get('domain', '').strip().lower()
        if not domain:
            raise ValueError('Domain required')
        if DNSZone.query.filter_by(domain=domain).first():
            raise ValueError(f'Zone for {domain} already exists')

        zone = DNSZone(
            domain=domain,
            provider=data.get('provider', 'manual'),
            provider_zone_id=data.get('provider_zone_id'),
        )

        # Preferred path: link an existing connection (Settings -> Connections).
        # The zone adopts its provider, and we look up the provider-side zone id
        # for this domain so records sync without a second token.
        config_id = data.get('dns_provider_config_id')
        if config_id:
            from app.models.email import DNSProviderConfig
            from app.services.dns_provider_service import DNSProviderService
            config = DNSProviderConfig.query.get(int(config_id))
            if not config:
                raise ValueError('Selected DNS connection not found')
            zone.provider = config.provider
            zone.dns_provider_config_id = config.id
            if not zone.provider_zone_id:
                zres = DNSProviderService.list_zones(config.id)
                if not zres.get('success'):
                    raise ValueError(
                        f"Couldn't reach {config.name}: {zres.get('error', 'unknown error')}")
                match = next((z for z in zres.get('zones', [])
                              if (z.get('name') or '').lower().rstrip('.') == domain), None)
                if not match:
                    raise ValueError(f'{config.name} does not manage {domain}')
                zone.provider_zone_id = match['id']
        elif data.get('provider_config'):
            # Legacy inline-token path, kept for backward compatibility.
            zone.provider_config = data['provider_config']

        db.session.add(zone)
        db.session.commit()
        return zone

    @staticmethod
    def adopt_zone(domain, config_id=None):
        """Idempotently materialize a local zone row for a provider domain so it can
        be managed (records, Cloudflare ops). Returns the existing row when the zone
        is already adopted — safe to call on every "Manage" click. Backfills the
        connection link on a pre-existing manual row when a ``config_id`` is given.
        """
        domain = (domain or '').strip().lower().rstrip('.')
        if not domain:
            raise ValueError('Domain required')
        existing = DNSZone.query.filter_by(domain=domain).first()
        if existing:
            if config_id and not existing.dns_provider_config_id:
                from app.models.email import DNSProviderConfig
                config = DNSProviderConfig.query.get(int(config_id))
                if config:
                    existing.provider = config.provider
                    existing.dns_provider_config_id = config.id
                    db.session.commit()
            return existing
        return DNSZoneService.create_zone({
            'domain': domain, 'dns_provider_config_id': config_id,
        })

    @classmethod
    def link_legacy_zones(cls):
        """One-time, idempotent: migrate Cloudflare zones that still carry an inline
        token in ``provider_config_json`` onto the canonical ``DNSProviderConfig``
        store, so every zone resolves its credentials the same way.

        For each such zone: reuse an existing connection whose decrypted key matches
        the token, else mint one (encrypted at rest), link it, and strip the now
        redundant plaintext token from the zone. API-free — uses the token already
        on the zone. Returns the number of zones migrated."""
        from app.models.email import DNSProviderConfig
        from app.services.dns_provider_service import DNSProviderService
        from app.utils.crypto import encrypt_secret

        migrated = 0
        zones = DNSZone.query.filter(
            DNSZone.provider == 'cloudflare',
            DNSZone.dns_provider_config_id.is_(None),
        ).all()
        for zone in zones:
            token = (zone.provider_config or {}).get('api_token')
            if not token:
                continue
            match = None
            for cfg in DNSProviderConfig.query.filter_by(provider='cloudflare').all():
                if DNSProviderService._api_key(cfg) == token:
                    match = cfg
                    break
            if match is None:
                match = DNSProviderConfig(
                    name=f'Cloudflare ({zone.domain})',
                    provider='cloudflare',
                    api_key=encrypt_secret(token),
                )
                db.session.add(match)
                db.session.flush()  # assign id
            zone.dns_provider_config_id = match.id
            cfg_json = dict(zone.provider_config or {})
            cfg_json.pop('api_token', None)
            zone.provider_config = cfg_json
            migrated += 1
        if migrated:
            db.session.commit()
        return migrated

    @staticmethod
    def delete_zone(zone_id):
        zone = DNSZone.query.get(zone_id)
        if not zone:
            return False
        db.session.delete(zone)
        db.session.commit()
        return True

    # --- Records ---

    @staticmethod
    def get_records(zone_id):
        return DNSRecord.query.filter_by(zone_id=zone_id).order_by(
            DNSRecord.record_type, DNSRecord.name
        ).all()

    @staticmethod
    def list_provider_records(zone):
        """The live provider record list for a zone, each tagged ``serverkit`` or
        ``external`` — so the UI can show everything in the user's zone while making
        clear which records ServerKit owns (and may touch) vs the user's own."""
        if zone.provider != 'cloudflare':
            return {'success': False, 'error': 'Mirror is only available for Cloudflare zones'}
        credential = DNSZoneService._resolve_credential(zone)
        if not credential:
            return {'success': False, 'error': 'No connected credential resolves for this zone'}

        from app.services.dns import CloudflareClient
        from app.services.dns_ownership_service import DnsOwnershipService

        res = CloudflareClient(credential).list_records(zone.provider_zone_id)
        if not res.get('success'):
            return res

        owned_ids, owned_keys = DnsOwnershipService.owned_keys(zone.provider_zone_id)
        records = []
        for r in res['records']:
            owned = (r['id'] in owned_ids) or \
                ((r['type'], (r['name'] or '').lower().rstrip('.')) in owned_keys)
            records.append({**r, 'managed_by': 'serverkit' if owned else 'external'})
        return {
            'success': True,
            'records': records,
            'counts': {
                'serverkit': sum(1 for x in records if x['managed_by'] == 'serverkit'),
                'external': sum(1 for x in records if x['managed_by'] == 'external'),
            },
        }

    @staticmethod
    def _provider_client_by_ref(config_id):
        """(client, provider) for a connected DNS config, or (None, error string)."""
        from app.models.email import DNSProviderConfig
        config = DNSProviderConfig.query.get(int(config_id)) if config_id else None
        if not config:
            return None, 'Connection not found'
        if config.provider != 'cloudflare':
            return None, 'Editing live records is only available for Cloudflare'
        from app.services.dns import CloudflareClient
        from app.services.dns.base import DnsCredential
        return CloudflareClient(DnsCredential.from_provider_config(config)), None

    @staticmethod
    def write_provider_record(config_id, provider_zone_id, spec_data, *,
                              provider_record_id=None, allow_foreign=False,
                              source='manual'):
        """Create or update ONE record in a live provider zone.

        This is the write half of ``list_provider_records_by_ref`` — the drawer could
        show every record in a zone but change none of them, so DNS was effectively
        read-plus-append: createDNSRecord went through the local-zone path while
        editing an existing record had no route at all.

        Ownership still decides what happens without an explicit instruction:
        ``allow_foreign`` false keeps the automation guarantee (never silently
        overwrite a record a human wrote), true is the operator saying "yes, that
        record, change it". Either way the write is recorded in the ownership ledger
        and the change log, so a record ServerKit edits becomes one it tracks.
        """
        from app.services.dns.base import DnsRecordSpec
        from app.services.dns_ownership_service import DnsOwnershipService

        client, error = DNSZoneService._provider_client_by_ref(config_id)
        if error:
            return {'success': False, 'error': error}
        if not provider_zone_id:
            return {'success': False, 'error': 'No provider zone id for this domain'}

        record_type = (spec_data.get('record_type') or spec_data.get('type') or '').upper()
        name = (spec_data.get('name') or '').strip()
        content = (spec_data.get('content') or '').strip()
        if not record_type or not name or not content:
            return {'success': False, 'error': 'record_type, name and content are required'}

        spec = DnsRecordSpec(
            record_type=record_type,
            name=name,
            content=content,
            ttl=int(spec_data.get('ttl') or 3600),
            priority=(int(spec_data['priority'])
                      if spec_data.get('priority') not in (None, '') else None),
            proxied=bool(spec_data.get('proxied')),
        )
        return DnsOwnershipService.guarded_upsert(
            client, provider='cloudflare', provider_zone_id=provider_zone_id, spec=spec,
            source=source, config_id=int(config_id), known_record_id=provider_record_id,
            allow_foreign=allow_foreign)

    @staticmethod
    def delete_provider_record(config_id, provider_zone_id, *, provider_record_id=None,
                               record_type=None, name=None, allow_foreign=False,
                               source='manual'):
        """Delete ONE record from a live provider zone."""
        from app.services.dns_ownership_service import DnsOwnershipService

        client, error = DNSZoneService._provider_client_by_ref(config_id)
        if error:
            return {'success': False, 'error': error}
        if not provider_zone_id:
            return {'success': False, 'error': 'No provider zone id for this domain'}
        if not provider_record_id and not (record_type and name):
            return {'success': False,
                    'error': 'provider_record_id, or record_type and name, are required'}

        return DnsOwnershipService.guarded_delete(
            client, provider_zone_id=provider_zone_id,
            record_type=(record_type or '').upper() or None, name=name,
            provider_record_id=provider_record_id, provider='cloudflare',
            source=source, config_id=int(config_id), allow_foreign=allow_foreign)

    @staticmethod
    def list_provider_records_by_ref(config_id, provider_zone_id):
        """Live records for a Cloudflare zone addressed directly by connection +
        provider zone id — so the Domains drawer can show a domain's real DNS without
        first adopting it into a local zone row. Each record is tagged ``serverkit``
        (owned) or ``external`` like the zone mirror."""
        from app.models.email import DNSProviderConfig
        config = DNSProviderConfig.query.get(int(config_id)) if config_id else None
        if not config:
            return {'success': False, 'error': 'Connection not found'}
        if config.provider != 'cloudflare':
            return {'success': False, 'error': 'Live records are only available for Cloudflare'}
        if not provider_zone_id:
            return {'success': False, 'error': 'No provider zone id for this domain'}

        from app.services.dns import CloudflareClient
        from app.services.dns.base import DnsCredential
        from app.services.dns_ownership_service import DnsOwnershipService

        res = CloudflareClient(DnsCredential.from_provider_config(config)).list_records(provider_zone_id)
        if not res.get('success'):
            return res
        owned_ids, owned_keys = DnsOwnershipService.owned_keys(provider_zone_id)
        records = []
        for r in res['records']:
            owned = (r['id'] in owned_ids) or \
                ((r['type'], (r['name'] or '').lower().rstrip('.')) in owned_keys)
            records.append({**r, 'managed_by': 'serverkit' if owned else 'external'})
        return {
            'success': True,
            'records': records,
            'counts': {
                'serverkit': sum(1 for x in records if x['managed_by'] == 'serverkit'),
                'external': sum(1 for x in records if x['managed_by'] == 'external'),
            },
        }

    @staticmethod
    def _rdap_entity_name(entity):
        """Pull a display name out of an RDAP entity's jCard (vcardArray)."""
        try:
            for item in entity.get('vcardArray', [])[1]:
                if item[0] == 'fn':
                    return item[3]
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_rdap_date(s):
        """Parse an RDAP eventDate (ISO 8601, often Zulu) into a naive-UTC datetime."""
        if not s:
            return None
        try:
            from datetime import timezone
            s = s.strip()
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            dt = datetime.fromisoformat(s)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            return None

    @staticmethod
    def _registration_is_fresh(row):
        if not row.checked_at:
            return False
        age = (datetime.utcnow() - row.checked_at).total_seconds()
        # Expiry dates change rarely → cache 30d; negative results re-checked in 3d.
        ttl = 30 * 86400 if row.expires_at else 3 * 86400
        return age < ttl

    @staticmethod
    def _rdap_query(domain):
        """One RDAP HTTP lookup. Returns {success, expires_at(iso str), registrar}."""
        import requests
        try:
            resp = requests.get(
                f'https://rdap.org/domain/{domain}',
                headers={'Accept': 'application/rdap+json'}, timeout=8)
            if resp.status_code == 404:
                return {'success': False, 'error': 'Domain not found in RDAP'}
            if resp.status_code != 200:
                return {'success': False, 'error': f'RDAP returned {resp.status_code}'}
            data = resp.json()
        except Exception as e:
            return {'success': False, 'error': str(e)}
        expires_at = None
        for ev in data.get('events') or []:
            if ev.get('eventAction') in ('expiration', 'expiry'):
                expires_at = ev.get('eventDate')
                break
        registrar = None
        for ent in data.get('entities') or []:
            if 'registrar' in (ent.get('roles') or []):
                registrar = DNSZoneService._rdap_entity_name(ent)
                break
        return {'success': True, 'expires_at': expires_at, 'registrar': registrar}

    @staticmethod
    def lookup_domain_registration(domain, force=False):
        """Registration expiry + registrar for a domain, **cached** in the
        ``domain_registrations`` table. Looks up via RDAP (the JSON-over-HTTPS WHOIS
        successor; rdap.org bootstraps the right registry — no `whois` binary) only
        when there's no fresh cached row, then persists the result so the Domains
        list shows it without re-querying on every load. ``force`` bypasses the cache.
        """
        from app.models.domain_registration import DomainRegistration
        domain = (domain or '').strip().lower().rstrip('.')
        if not domain or '.' not in domain:
            return {'success': False, 'error': 'Invalid domain'}

        row = DomainRegistration.query.filter_by(domain=domain).first()
        if row and not force and DNSZoneService._registration_is_fresh(row):
            return {'success': True, 'cached': True, **row.to_dict()}

        result = DNSZoneService._rdap_query(domain)

        if row is None:
            row = DomainRegistration(domain=domain)
            db.session.add(row)
        if result.get('success'):
            row.expires_at = DNSZoneService._parse_rdap_date(result.get('expires_at'))
            row.registrar = result.get('registrar')
            row.source = 'rdap'
        row.checked_at = datetime.utcnow()
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()

        if result.get('success'):
            return {'success': True, **row.to_dict()}
        # Transient failure — surface previously cached data if we have it.
        if row.expires_at:
            return {'success': True, 'stale': True, **row.to_dict()}
        return {'success': False, 'error': result.get('error', 'lookup failed')}

    @staticmethod
    def create_record(zone_id, data):
        zone = DNSZone.query.get(zone_id)
        if not zone:
            raise ValueError('Zone not found')

        record_type = data.get('record_type', '').upper()
        if record_type not in DNSZoneService.RECORD_TYPES:
            raise ValueError(f'Invalid record type: {record_type}')

        record = DNSRecord(
            zone_id=zone_id,
            record_type=record_type,
            name=data.get('name', '@'),
            content=data.get('content', ''),
            ttl=data.get('ttl', 3600),
            priority=data.get('priority'),
            proxied=data.get('proxied', False),
        )
        db.session.add(record)
        db.session.commit()

        # Sync to provider if configured
        if zone.provider != 'manual':
            DNSZoneService._sync_record_to_provider(zone, record, 'create')

        return record

    @staticmethod
    def update_record(record_id, data):
        record = DNSRecord.query.get(record_id)
        if not record:
            return None
        for field in ['name', 'content', 'ttl', 'priority', 'proxied']:
            if field in data:
                setattr(record, field, data[field])
        db.session.commit()

        zone = record.zone
        if zone.provider != 'manual':
            DNSZoneService._sync_record_to_provider(zone, record, 'update')

        return record

    @staticmethod
    def delete_record(record_id):
        record = DNSRecord.query.get(record_id)
        if not record:
            return False
        zone = record.zone
        if zone.provider != 'manual' and record.provider_record_id:
            DNSZoneService._sync_record_to_provider(zone, record, 'delete')
        db.session.delete(record)
        db.session.commit()
        return True

    @staticmethod
    def apply_preset(zone_id, preset_key, variables=None):
        if preset_key not in DNSZoneService.DNS_PRESETS:
            raise ValueError(f'Unknown preset: {preset_key}')

        zone = DNSZone.query.get(zone_id)
        if not zone:
            raise ValueError('Zone not found')

        preset = DNSZoneService.DNS_PRESETS[preset_key]
        variables = variables or {}
        variables.setdefault('domain', zone.domain)

        records = []
        for rec_data in preset['records']:
            data = dict(rec_data)
            for field in ['name', 'content']:
                for var_name, var_val in variables.items():
                    data[field] = data[field].replace('{{' + var_name + '}}', var_val)
            record = DNSZoneService.create_record(zone_id, data)
            records.append(record)

        return records

    @staticmethod
    def check_propagation(domain, record_type='A'):
        """Check DNS propagation across multiple nameservers."""
        import socket

        nameservers = [
            ('Google', '8.8.8.8'),
            ('Cloudflare', '1.1.1.1'),
            ('OpenDNS', '208.67.222.222'),
            ('Quad9', '9.9.9.9'),
        ]

        results = []
        for ns_name, ns_ip in nameservers:
            try:
                from app.utils.system import run_command
                result = run_command(['dig', f'@{ns_ip}', domain, record_type, '+short'], timeout=5)
                stdout = result.get('stdout', '').strip()
                results.append({
                    'nameserver': ns_name,
                    'ip': ns_ip,
                    'result': stdout.split('\n') if stdout else [],
                    'propagated': bool(stdout),
                })
            except Exception:
                results.append({
                    'nameserver': ns_name,
                    'ip': ns_ip,
                    'result': [],
                    'propagated': False,
                    'error': 'Query failed',
                })

        return results

    @staticmethod
    def export_zone(zone_id):
        """Export zone in BIND format."""
        zone = DNSZone.query.get(zone_id)
        if not zone:
            return None

        records = DNSZoneService.get_records(zone_id)
        lines = [f'; Zone file for {zone.domain}', f'$ORIGIN {zone.domain}.', f'$TTL 3600', '']

        for rec in records:
            name = rec.name if rec.name != '@' else zone.domain + '.'
            if rec.record_type == 'MX':
                lines.append(f'{name}\t{rec.ttl}\tIN\t{rec.record_type}\t{rec.priority or 10}\t{rec.content}')
            elif rec.record_type == 'SRV':
                lines.append(f'{name}\t{rec.ttl}\tIN\t{rec.record_type}\t{rec.priority or 0}\t{rec.content}')
            else:
                lines.append(f'{name}\t{rec.ttl}\tIN\t{rec.record_type}\t{rec.content}')

        return '\n'.join(lines)

    @staticmethod
    def import_zone(zone_id, bind_content):
        """Import records from BIND zone file format."""
        zone = DNSZone.query.get(zone_id)
        if not zone:
            raise ValueError('Zone not found')

        records_created = []
        for line in bind_content.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith(';') or line.startswith('$'):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            # Try to parse: name ttl IN type content
            try:
                if parts[2] == 'IN':
                    name = parts[0].rstrip('.')
                    ttl = int(parts[1])
                    rtype = parts[3]
                    content = ' '.join(parts[4:])
                    if name == zone.domain:
                        name = '@'
                    record = DNSZoneService.create_record(zone_id, {
                        'record_type': rtype, 'name': name,
                        'content': content, 'ttl': ttl,
                    })
                    records_created.append(record)
            except (ValueError, IndexError):
                continue

        return records_created

    @staticmethod
    def get_presets():
        return DNSZoneService.DNS_PRESETS

    @staticmethod
    def _sync_record_to_provider(zone, record, action):
        """Sync a DNS record change to Cloudflare (the only provider the zone layer
        syncs — Route53/DigitalOcean/GoDaddy are managed via DNSProviderService)."""
        if zone.provider != 'cloudflare':
            return
        credential = DNSZoneService._resolve_credential(zone)
        if not credential:
            return
        try:
            DNSZoneService._cloudflare_sync(zone, record, action, credential)
        except Exception as e:
            logger.error(f'DNS provider sync failed: {e}')

    @staticmethod
    def _resolve_credential(zone):
        """Resolve the Cloudflare credential for a zone, preferring the canonical
        connection store and persisting the discovered link.

        Order:
          1. The linked DNSProviderConfig (``zone.dns_provider_config_id``).
          2. Auto-discovery — the connected provider whose account contains this
             domain; the link + ``provider_zone_id`` are backfilled so step 1 wins
             next time (no repeat API call).
          3. Legacy fallback — a token still stored inline on the zone.
        Returns a :class:`DnsCredential`, or ``None`` when nothing manages the zone.
        """
        from app.models.email import DNSProviderConfig
        from app.services.dns_provider_service import DNSProviderService
        from app.services.dns.base import DnsCredential

        if zone.dns_provider_config_id:
            config = DNSProviderConfig.query.get(zone.dns_provider_config_id)
            if config:
                return DnsCredential.from_provider_config(config)

        try:
            config, zinfo = DNSProviderService.find_zone_for_domain(zone.domain)
        except Exception:
            config, zinfo = None, None
        if config:
            zone.dns_provider_config_id = config.id
            if zinfo and not zone.provider_zone_id:
                zone.provider_zone_id = zinfo.get('id')
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            return DnsCredential.from_provider_config(config)

        token = (zone.provider_config or {}).get('api_token')
        if token:
            return DnsCredential.cloudflare_token(token)
        return None

    @staticmethod
    def _cloudflare_sync(zone, record, action, credential):
        """Push a single record change to Cloudflare via the shared client, gated by
        the ownership ledger.

        ``upsert`` is idempotent (updates by ``provider_record_id`` when known, else
        by name). The Zones page is explicit zone management, so it adopts a matching
        record and records ServerKit ownership (``allow_foreign=True``)."""
        from app.services.dns import CloudflareClient
        from app.services.dns.base import DnsRecordSpec
        from app.services.dns_ownership_service import DnsOwnershipService

        client = CloudflareClient(credential)
        zone_id = zone.provider_zone_id

        if action in ('create', 'update'):
            res = DnsOwnershipService.guarded_upsert(
                client, provider='cloudflare', provider_zone_id=zone_id,
                spec=DnsRecordSpec.from_record(record), source='zone',
                config_id=zone.dns_provider_config_id,
                known_record_id=record.provider_record_id, allow_foreign=True)
            if res.get('success'):
                rid = res.get('record_id')
                if rid and rid != record.provider_record_id:
                    record.provider_record_id = rid
                    db.session.commit()
            else:
                logger.error('Cloudflare sync %s failed for %s: %s',
                             action, record.name, res.get('error'))
        elif action == 'delete' and record.provider_record_id:
            DnsOwnershipService.guarded_delete(
                client, provider_zone_id=zone_id, record_type=record.record_type,
                name=record.name, provider_record_id=record.provider_record_id,
                source='zone', config_id=zone.dns_provider_config_id)
