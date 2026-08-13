"""Prove the Cloud Provisioning extraction (plan 47 Phase 2).

A fresh panel no longer carries the cloud-provider API in core; installing the
serverkit-cloud-provision builtin registers `/api/v1/cloud`. The CloudProvider
model stays core (G2). Also covers the one-shot backend re-acquisition that keeps
upgraded panels (which installed the extension frontend-only) from losing the API.
"""
import importlib
import sys

import pytest

from app import db
from app.models.plugin import InstalledPlugin
from app.services import plugin_service

SLUG = 'serverkit-cloud-provision'
_PKG = f'app.plugins.{SLUG}'


def test_core_has_no_cloud_routes(app):
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert [r for r in rules if r.startswith('/api/v1/cloud/')] == []


def test_core_import_of_cloud_service_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module('app.services.cloud_provisioning_service')


def test_cloud_in_converted_builtin_slugs():
    from app.services.extension_migration import CONVERTED_BUILTIN_SLUGS
    assert SLUG in CONVERTED_BUILTIN_SLUGS


def test_cloud_model_stays_core():
    """G2: the CloudProvider/CloudServer models remain importable from core."""
    from app.models.cloud_server import CloudProvider, CloudServer  # noqa: F401


@pytest.fixture
def install_dirs(tmp_path, monkeypatch):
    backend = tmp_path / 'plugins_backend'
    frontend = tmp_path / 'plugins_frontend'
    backend.mkdir()
    frontend.mkdir()
    monkeypatch.setattr(plugin_service, 'BACKEND_PLUGINS_DIR', str(backend))
    monkeypatch.setattr(plugin_service, 'FRONTEND_PLUGINS_DIR', str(frontend))

    added = str(backend)
    app_pkg_plugins = importlib.import_module('app.plugins')
    if added not in app_pkg_plugins.__path__:
        # insert(0), NOT append: on a machine where this extension is genuinely
        # installed, backend/app/plugins/<slug>/ already exists and — being the
        # real package directory — wins the import. Appending therefore made
        # these tests exercise the INSTALLED copy instead of the freshly-copied
        # source, so a source change could look untested (or a stale installed
        # copy could fail tests that are actually fine). Take precedence.
        app_pkg_plugins.__path__.insert(0, added)

    yield {'backend': backend, 'frontend': frontend}

    if added in app_pkg_plugins.__path__:
        app_pkg_plugins.__path__.remove(added)
    for name in list(sys.modules):
        if name == _PKG or name.startswith(_PKG + '.'):
            del sys.modules[name]


def test_install_cloud_extension_registers_routes(app, client, auth_headers, install_dirs):
    available = {e['slug'] for e in plugin_service.list_builtin_extensions()}
    assert SLUG in available

    plugin = plugin_service.install_builtin_extension(SLUG)
    assert plugin.status == InstalledPlugin.STATUS_ACTIVE
    assert plugin.has_backend is True
    assert plugin.url_prefix == '/api/v1/cloud'

    resp = client.get('/api/v1/cloud/providers', headers=auth_headers)
    assert resp.status_code not in (404, 503), resp.status_code


def test_cloud_provider_secret_encrypted_at_rest(app, install_dirs):
    """The extracted service still encrypts provider secrets at rest (moved here
    from test_provider_secret_encryption.py)."""
    plugin_service.install_builtin_extension(SLUG)
    from app.models.cloud_server import CloudProvider
    from app.utils.crypto import is_encrypted, decrypt_secret_safe

    svc_mod = importlib.import_module(f'{_PKG}.cloud_provisioning_service')
    CloudProvisioningService = svc_mod.CloudProvisioningService

    p = CloudProvisioningService.create_provider(
        {'provider_type': 'digitalocean', 'name': 'do', 'api_key': 'do-token-xyz'})
    row = CloudProvider.query.get(p.id)
    assert row.api_key_encrypted != 'do-token-xyz'
    assert is_encrypted(row.api_key_encrypted) is True
    assert decrypt_secret_safe(row.api_key_encrypted) == 'do-token-xyz'
    assert CloudProvisioningService._auth_headers(row)['Authorization'] == 'Bearer do-token-xyz'


def test_backend_acquisition_upgrades_frontend_only_install(app, install_dirs):
    """An upgraded panel that installed the extension frontend-only re-acquires
    the now-extracted backend (plan 47 Phase 2 migration)."""
    from app.services import extension_migration

    # Simulate the pre-plan-47 state: a frontend-only install (API came from core)
    row = InstalledPlugin(
        name=SLUG, display_name='Cloud Provisioning', slug=SLUG, version='1.0.0',
        source_type='builtin', status=InstalledPlugin.STATUS_ACTIVE,
    )
    row.has_backend = False
    row.has_frontend = True
    db.session.add(row)
    db.session.commit()

    extension_migration.run_backend_acquisition()

    refreshed = InstalledPlugin.query.filter_by(slug=SLUG).first()
    assert refreshed.has_backend is True
    rules = [r.rule for r in app.url_map.iter_rules()]
    assert any(r.startswith('/api/v1/cloud/') for r in rules)


# --------------------------------------------------------------------------- #
# Destroy safety. `_provider_destroy` used to discard the provider's response
# entirely, and `destroy_server` marked the row destroyed even when an exception
# escaped — so a refused delete (bad key, rate limit, provider 5xx) was recorded
# as a successful destroy. The server kept running and billing while the operator
# stopped seeing it, and nothing in the panel ever mentioned it again.
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f'{self.status_code} error')


@pytest.fixture
def cloud_server(app, install_dirs):
    """An active Vultr server row with the extension installed."""
    plugin_service.install_builtin_extension(SLUG)
    from app.models.cloud_server import CloudProvider, CloudServer

    provider = CloudProvider(name='Vultr', provider_type='vultr',
                             api_key_encrypted='', is_active=True)
    db.session.add(provider)
    db.session.commit()

    server = CloudServer(provider_id=provider.id, external_id='vultr-abc-123',
                         name='doomed', status=CloudServer.STATUS_ACTIVE)
    db.session.add(server)
    db.session.commit()

    svc = importlib.import_module(f'{_PKG}.cloud_provisioning_service').CloudProvisioningService
    return {'svc': svc, 'server': server, 'provider': provider}


def test_refused_destroy_does_not_mark_server_destroyed(cloud_server, monkeypatch):
    """The regression that matters: provider says no, row must stay active."""
    import requests
    from app.models.cloud_server import CloudServer

    calls = []
    monkeypatch.setattr(requests, 'delete',
                        lambda *a, **k: (calls.append(a), _Resp(500))[1])

    svc, server = cloud_server['svc'], cloud_server['server']
    with pytest.raises(requests.HTTPError):
        svc.destroy_server(server.id)

    assert len(calls) == 1
    row = CloudServer.query.get(server.id)
    assert row.status == CloudServer.STATUS_ACTIVE, 'a refused delete must not record a destroy'
    assert row.destroyed_at is None


def test_unauthorized_destroy_does_not_mark_server_destroyed(cloud_server, monkeypatch):
    """A revoked or wrong API key is the likeliest cause in practice."""
    import requests
    from app.models.cloud_server import CloudServer

    monkeypatch.setattr(requests, 'delete', lambda *a, **k: _Resp(401))
    svc, server = cloud_server['svc'], cloud_server['server']
    with pytest.raises(requests.HTTPError):
        svc.destroy_server(server.id)
    assert CloudServer.query.get(server.id).status == CloudServer.STATUS_ACTIVE


def test_provider_404_counts_as_already_gone(cloud_server, monkeypatch):
    """Already deleted remotely IS the desired end state, so it converges."""
    import requests
    from app.models.cloud_server import CloudServer

    monkeypatch.setattr(requests, 'delete', lambda *a, **k: _Resp(404))
    svc, server = cloud_server['svc'], cloud_server['server']
    assert svc.destroy_server(server.id) is True
    assert CloudServer.query.get(server.id).status == CloudServer.STATUS_DESTROYED


def test_successful_destroy_marks_destroyed(cloud_server, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer

    monkeypatch.setattr(requests, 'delete', lambda *a, **k: _Resp(204))
    svc, server = cloud_server['svc'], cloud_server['server']
    assert svc.destroy_server(server.id) is True
    row = CloudServer.query.get(server.id)
    assert row.status == CloudServer.STATUS_DESTROYED
    assert row.destroyed_at is not None


def test_destroy_is_idempotent_without_recontacting_provider(cloud_server, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer

    calls = []
    monkeypatch.setattr(requests, 'delete',
                        lambda *a, **k: (calls.append(a), _Resp(204))[1])
    svc, server = cloud_server['svc'], cloud_server['server']

    assert svc.destroy_server(server.id) is True
    assert svc.destroy_server(server.id) is True
    assert len(calls) == 1, 'second destroy should converge locally, not re-delete'
    assert CloudServer.query.get(server.id).status == CloudServer.STATUS_DESTROYED


def test_destroy_endpoint_reports_502_not_404_when_provider_refuses(
        cloud_server, client, auth_headers, monkeypatch):
    """404 would read as 'already gone' — the opposite of what happened."""
    import requests
    from app.models.cloud_server import CloudServer

    monkeypatch.setattr(requests, 'delete', lambda *a, **k: _Resp(500))
    server = cloud_server['server']

    resp = client.delete(f'/api/v1/cloud/servers/{server.id}', headers=auth_headers)
    assert resp.status_code == 502, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body['destroyed'] is False
    assert 'refused' in body['error'].lower()
    assert CloudServer.query.get(server.id).status == CloudServer.STATUS_ACTIVE


def test_destroy_endpoint_still_404s_for_unknown_server(
        cloud_server, client, auth_headers):
    resp = client.delete('/api/v1/cloud/servers/999999', headers=auth_headers)
    assert resp.status_code == 404


def test_unsupported_provider_type_raises(cloud_server, monkeypatch):
    """Matches _provider_resize, which already guards this."""
    svc = cloud_server['svc']
    provider = cloud_server['provider']
    provider.provider_type = 'nephelo-cloud'
    db.session.commit()
    with pytest.raises(ValueError, match='Unsupported provider type'):
        svc.destroy_server(cloud_server['server'].id)


# --------------------------------------------------------------------------- #
# Discovery (adoption step 3). Read-only preview of the provider's inventory:
# cloud_servers only ever held servers ServerKit created, so a panel whose
# provider account was full of running instances showed an empty Cloud page with
# no failure anywhere to explain it.
# --------------------------------------------------------------------------- #

def _vultr_instance(iid, label='', region='ewr', plan='vc2-1c-1gb',
                    status='active', power='running', ip='192.0.2.10'):
    return {'id': iid, 'label': label, 'region': region, 'plan': plan,
            'os': 'Ubuntu 24.04', 'main_ip': ip, 'v6_main_ip': '',
            'status': status, 'power_status': power}


class _GetResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f'{self.status_code} error')


def test_discover_reports_remote_servers_without_writing(cloud_server, monkeypatch):
    """The whole point of discover: it must adopt nothing."""
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider, tracked = (cloud_server['svc'], cloud_server['provider'],
                              cloud_server['server'])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({
        'instances': [
            _vultr_instance('vultr-abc-123', label='doomed'),   # already tracked
            _vultr_instance('new-1', label='labs', region='dfw'),
            _vultr_instance('new-2'),                            # unlabelled
        ],
        'meta': {'links': {'next': ''}},
    }))

    before = CloudServer.query.count()
    result = svc.discover_provider(provider.id)

    assert result['remote_total'] == 3
    assert {e['external_id'] for e in result['new']} == {'new-1', 'new-2'}
    assert [e['server_id'] for e in result['tracked']] == [tracked.id]
    assert result['missing_remote'] == []
    assert CloudServer.query.count() == before, 'discover must not write rows'


def test_discover_names_unlabelled_instances(cloud_server, monkeypatch):
    """name is NOT NULL, and '(no label)' repeated N times is unusable."""
    import requests
    svc, provider = cloud_server['svc'], cloud_server['provider']
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        {'instances': [_vultr_instance('abcdef123456', label='', region='ams')]}))

    entry = svc.discover_provider(provider.id)['new'][0]
    assert entry['name'] == 'vultr-ams-abcdef12'
    assert entry['labelled'] is False


def test_discover_flags_servers_missing_remotely_without_destroying(
        cloud_server, monkeypatch):
    """We did not observe a destroy, so we must not claim one."""
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider, tracked = (cloud_server['svc'], cloud_server['provider'],
                              cloud_server['server'])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({'instances': []}))

    result = svc.discover_provider(provider.id)
    assert [m['server_id'] for m in result['missing_remote']] == [tracked.id]
    row = CloudServer.query.get(tracked.id)
    assert row.status == CloudServer.STATUS_ACTIVE, 'must not mark destroyed'


def test_discover_maps_stopped_instance_to_off(cloud_server, monkeypatch):
    """A stopped instance is 'off', not 'active' — and it still bills."""
    import requests
    from app.models.cloud_server import CloudServer
    svc, provider = cloud_server['svc'], cloud_server['provider']
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        {'instances': [_vultr_instance('s1', label='halted', power='stopped')]}))
    assert svc.discover_provider(provider.id)['new'][0]['status'] == CloudServer.STATUS_OFF


def test_discover_follows_cursor_pagination(cloud_server, monkeypatch):
    import requests
    svc, provider = cloud_server['svc'], cloud_server['provider']
    pages = [
        _GetResp({'instances': [_vultr_instance('p1', label='one')],
                  'meta': {'links': {'next': 'CURSOR2'}}}),
        _GetResp({'instances': [_vultr_instance('p2', label='two')],
                  'meta': {'links': {'next': ''}}}),
    ]
    seen_cursors = []

    def fake_get(url, **kw):
        seen_cursors.append((kw.get('params') or {}).get('cursor'))
        return pages[len(seen_cursors) - 1]

    monkeypatch.setattr(requests, 'get', fake_get)
    result = svc.discover_provider(provider.id)
    assert result['remote_total'] == 2
    assert seen_cursors == [None, 'CURSOR2']


def test_discover_unsupported_provider_raises_not_implemented(cloud_server):
    svc, provider = cloud_server['svc'], cloud_server['provider']
    provider.provider_type = 'hetzner'
    db.session.commit()
    with pytest.raises(NotImplementedError):
        svc.discover_provider(provider.id)


def test_discover_endpoint_returns_501_for_unsupported_provider(
        cloud_server, client, auth_headers):
    provider = cloud_server['provider']
    provider.provider_type = 'linode'
    db.session.commit()
    resp = client.get(f'/api/v1/cloud/providers/{provider.id}/discover',
                      headers=auth_headers)
    assert resp.status_code == 501
    assert resp.get_json()['supported'] is False


def test_discover_endpoint_502s_when_provider_unreachable(
        cloud_server, client, auth_headers, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({}, status_code=500))
    resp = client.get(
        f'/api/v1/cloud/providers/{cloud_server["provider"].id}/discover',
        headers=auth_headers)
    assert resp.status_code == 502


def test_providers_list_advertises_discovery_support(
        cloud_server, client, auth_headers):
    resp = client.get('/api/v1/cloud/providers', headers=auth_headers)
    assert resp.status_code == 200
    vultr = [p for p in resp.get_json()['providers'] if p['provider_type'] == 'vultr']
    assert vultr and vultr[0]['supports_discovery'] is True


def test_adopted_server_reports_it_cannot_be_destroyed(cloud_server):
    """The guardrail the UI keys off: an adopted server is not ours to destroy."""
    from app.models.cloud_server import CloudServer
    server = cloud_server['server']
    assert server.to_dict()['can_destroy'] is True
    server.origin = CloudServer.ORIGIN_ADOPTED
    db.session.commit()
    assert server.to_dict()['can_destroy'] is False
    assert server.to_dict()['origin'] == 'adopted'


# --------------------------------------------------------------------------- #
# Sync / adopt (step 4) and account-level cost (step 6).
# --------------------------------------------------------------------------- #

def test_sync_adopts_untracked_servers(cloud_server, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider = cloud_server['svc'], cloud_server['provider']
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({'instances': [
        _vultr_instance('vultr-abc-123', label='doomed'),          # already tracked
        _vultr_instance('new-1', label='labs', region='dfw'),
        _vultr_instance('new-2', power='stopped'),
    ]}))

    result = svc.sync_provider(provider.id, user_id=None)
    assert len(result['adopted']) == 2
    assert result['missing_remote'] == []

    adopted = CloudServer.query.filter_by(origin=CloudServer.ORIGIN_ADOPTED).all()
    assert {s.external_id for s in adopted} == {'new-1', 'new-2'}
    for s in adopted:
        assert s.sync_state == CloudServer.SYNC_IN_SYNC
        assert s.last_synced_at is not None
        assert s.to_dict()['can_destroy'] is False
    # The stopped one keeps its real power state rather than defaulting to active.
    assert CloudServer.query.filter_by(external_id='new-2').first().status == CloudServer.STATUS_OFF


def test_sync_is_idempotent(cloud_server, monkeypatch):
    """Re-running must not duplicate rows — the unique constraint's whole point."""
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider = cloud_server['svc'], cloud_server['provider']
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        {'instances': [_vultr_instance('new-1', label='labs')]}))

    first = svc.sync_provider(provider.id)
    second = svc.sync_provider(provider.id)
    assert len(first['adopted']) == 1
    assert second['adopted'] == []
    assert CloudServer.query.filter_by(external_id='new-1').count() == 1


def test_sync_refreshes_changed_fields_but_keeps_local_name(cloud_server, monkeypatch):
    """A rename in the panel must survive a sync; drifted specs must not."""
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider, tracked = (cloud_server['svc'], cloud_server['provider'],
                              cloud_server['server'])
    tracked.name = 'renamed-by-operator'
    db.session.commit()

    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({'instances': [
        _vultr_instance('vultr-abc-123', label='provider-label',
                        region='lax', plan='vc2-4c-8gb', ip='203.0.113.9'),
    ]}))
    svc.sync_provider(provider.id)

    row = CloudServer.query.get(tracked.id)
    assert row.name == 'renamed-by-operator', 'sync must not clobber a local rename'
    assert row.region == 'lax'
    assert row.ip_address == '203.0.113.9'
    assert row.sync_state == CloudServer.SYNC_IN_SYNC


def test_sync_flags_missing_without_destroying(cloud_server, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider, tracked = (cloud_server['svc'], cloud_server['provider'],
                              cloud_server['server'])
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({'instances': []}))

    result = svc.sync_provider(provider.id)
    assert len(result['missing_remote']) == 1
    row = CloudServer.query.get(tracked.id)
    assert row.sync_state == CloudServer.SYNC_MISSING_REMOTE
    assert row.status == CloudServer.STATUS_ACTIVE, 'must never auto-destroy'
    assert row.destroyed_at is None


def test_destroy_refuses_adopted_server(cloud_server, monkeypatch):
    """The guardrail: an adopted server is not ours to delete."""
    import requests
    from app.models.cloud_server import CloudServer

    svc, server = cloud_server['svc'], cloud_server['server']
    server.origin = CloudServer.ORIGIN_ADOPTED
    db.session.commit()

    called = []
    monkeypatch.setattr(requests, 'delete',
                        lambda *a, **k: (called.append(a), _Resp(204))[1])

    svc_mod = importlib.import_module(f'{_PKG}.cloud_provisioning_service')
    with pytest.raises(svc_mod.AdoptedServerError):
        svc.destroy_server(server.id)

    assert called == [], 'must refuse BEFORE contacting the provider'
    assert CloudServer.query.get(server.id).status == CloudServer.STATUS_ACTIVE


def test_destroy_endpoint_409s_for_adopted_server(
        cloud_server, client, auth_headers, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer
    server = cloud_server['server']
    server.origin = CloudServer.ORIGIN_ADOPTED
    db.session.commit()
    monkeypatch.setattr(requests, 'delete', lambda *a, **k: _Resp(204))

    resp = client.delete(f'/api/v1/cloud/servers/{server.id}', headers=auth_headers)
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['adopted'] is True and body['destroyed'] is False
    assert CloudServer.query.get(server.id).status == CloudServer.STATUS_ACTIVE


def test_sync_endpoint_requires_admin_and_returns_summary(
        cloud_server, client, auth_headers, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        {'instances': [_vultr_instance('new-9', label='fresh')]}))
    resp = client.post(
        f'/api/v1/cloud/providers/{cloud_server["provider"].id}/sync',
        headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.get_json()['adopted']) == 1


def test_cost_summary_reports_provider_charges_and_flags_partial(
        cloud_server, monkeypatch):
    """A local sum that silently excludes adopted servers is a wrong bill."""
    import requests
    from app.models.cloud_server import CloudServer

    svc, provider, tracked = (cloud_server['svc'], cloud_server['provider'],
                              cloud_server['server'])
    tracked.origin = CloudServer.ORIGIN_ADOPTED
    db.session.commit()

    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        {'account': {'pending_charges': 70.17, 'balance': 0}}))

    summary = svc.get_cost_summary()
    assert summary['local_total_is_partial'] is True
    assert summary['account_charges'][0]['pending_charges'] == 70.17
    assert summary['account_charges'][0]['provider_name'] == provider.name


def test_cost_summary_survives_an_unreachable_provider(cloud_server, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError('no route to host')

    monkeypatch.setattr(requests, 'get', boom)
    summary = cloud_server['svc'].get_cost_summary()
    assert summary['account_charges'] == []
    assert 'total_monthly' in summary


# --------------------------------------------------------------------------- #
# In-tree source beats install's copy. A builtin install copies
# builtin-extensions/<slug>/backend to app/plugins/<slug>; both then exist, and
# the copy won the import because it sits in the real package directory. Editing
# the source therefore changed nothing until someone re-installed, and a FIXED
# extension kept serving old code with nothing to indicate it was stale.
# --------------------------------------------------------------------------- #

def test_builtin_loads_from_source_not_a_stale_copy(app, install_dirs, monkeypatch):
    import os
    plugin_service.install_builtin_extension(SLUG)
    plugin = InstalledPlugin.query.filter_by(slug=SLUG).first()
    assert plugin.source_type == 'builtin'

    # Poison the copied file so anything importing it is unmistakable.
    copied = os.path.join(str(install_dirs['backend']), SLUG,
                          'cloud_provisioning_service.py')
    assert os.path.isfile(copied), copied
    with open(copied, 'w', encoding='utf-8') as f:
        f.write('STALE_COPY_MARKER = True\n'
                'class CloudProvisioningService:\n'
                '    pass\n')

    # Drop cached modules so the next import genuinely re-resolves.
    for name in list(sys.modules):
        if name == _PKG or name.startswith(_PKG + '.'):
            del sys.modules[name]

    assert plugin_service._prefer_builtin_source(plugin) is True

    mod = importlib.import_module(f'{_PKG}.cloud_provisioning_service')
    assert not hasattr(mod, 'STALE_COPY_MARKER'), 'imported the stale copy, not the source'
    # A real method only the source has.
    assert hasattr(mod.CloudProvisioningService, 'discover_provider')


def test_prefer_builtin_source_ignores_non_builtin_installs(app):
    """A registry/upload extension has no in-tree source; keep its extracted copy."""
    row = InstalledPlugin(name='ext-from-registry', display_name='Reg', version='1.0.0',
                          slug='ext-from-registry', source_type='registry',
                          status=InstalledPlugin.STATUS_ACTIVE)
    row.has_backend = True
    db.session.add(row)
    db.session.commit()
    assert plugin_service._prefer_builtin_source(row) is False


def test_prefer_builtin_source_ignores_a_missing_source_tree(app):
    row = InstalledPlugin(name='serverkit-not-in-tree', display_name='X', version='1.0.0',
                          slug='serverkit-not-in-tree', source_type='builtin',
                          status=InstalledPlugin.STATUS_ACTIVE)
    row.has_backend = True
    db.session.add(row)
    db.session.commit()
    assert plugin_service._prefer_builtin_source(row) is False


# --------------------------------------------------------------------------- #
# Hostinger: a VPS provider, so it belongs to the CloudProvider abstraction and
# inherits discovery/adoption/destroy-guard rather than inventing its own shape.
# Import-only on purpose — Hostinger creates a VPS through an ORDER (plan, term,
# payment), which is not something to drive from an API call, so `create` refuses
# with an explanation instead of failing somewhere deeper with a shape error.
# --------------------------------------------------------------------------- #

@pytest.fixture
def hostinger(app, install_dirs):
    plugin_service.install_builtin_extension(SLUG)
    from app.models.cloud_server import CloudProvider
    provider = CloudProvider(name='Hostinger', provider_type='hostinger',
                             api_key_encrypted='', is_active=True)
    db.session.add(provider)
    db.session.commit()
    svc = importlib.import_module(f'{_PKG}.cloud_provisioning_service').CloudProvisioningService
    return {'svc': svc, 'provider': provider}


def test_hostinger_supports_discovery_but_not_provisioning(hostinger):
    svc = hostinger['svc']
    assert svc.supports_discovery('hostinger') is True
    assert svc.supports_provisioning('hostinger') is False
    assert svc.supports_provisioning('vultr') is True


def test_hostinger_create_refuses_with_an_explanation(hostinger, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer
    called = []
    monkeypatch.setattr(requests, 'post', lambda *a, **k: called.append(a))

    server = CloudServer(provider_id=hostinger['provider'].id, name='nope',
                         status=CloudServer.STATUS_CREATING)
    db.session.add(server)
    db.session.commit()
    with pytest.raises(ValueError, match='Import existing'):
        hostinger['svc']._provider_create(hostinger['provider'], server, {})
    assert called == [], 'must refuse before calling the provider'


def test_hostinger_discovery_normalises_the_vps_list(hostinger, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer

    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp({'data': [
        {'id': 991, 'hostname': 'build-box', 'state': 'running', 'plan': 'KVM 4',
         'ipv4': [{'address': '203.0.113.7'}], 'ipv6': [{'address': '2001:db8::1'}],
         'datacenter': {'city': 'Frankfurt'}, 'template': {'name': 'Ubuntu 24.04'}},
        {'id': 992, 'hostname': '', 'state': 'stopped', 'plan': 'KVM 2',
         'ipv4': [{'address': '203.0.113.8'}]},
    ]}))

    out = hostinger['svc']._provider_list_remote(hostinger['provider'])
    assert [e['external_id'] for e in out] == ['991', '992']
    first = out[0]
    assert first['name'] == 'build-box'
    assert first['ip_address'] == '203.0.113.7'
    assert first['ipv6_address'] == '2001:db8::1'
    assert first['region'] == 'Frankfurt'
    assert first['image'] == 'Ubuntu 24.04'
    assert first['status'] == CloudServer.STATUS_ACTIVE
    # A stopped VPS still bills, so it is 'off' rather than dropped.
    assert out[1]['status'] == CloudServer.STATUS_OFF
    # Unlabelled hosts still need a name: the column is NOT NULL.
    assert out[1]['name'] == 'hostinger-992'
    assert out[1]['labelled'] is False


def test_hostinger_accepts_a_bare_list_response(hostinger, monkeypatch):
    """The v1 endpoint returns a bare list in some versions and {data:[…]} in
    others; trusting one shape breaks on the other."""
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        [{'id': 5, 'hostname': 'bare', 'state': 'running'}]))
    out = hostinger['svc']._provider_list_remote(hostinger['provider'])
    assert [e['name'] for e in out] == ['bare']


def test_hostinger_servers_adopt_and_cannot_be_destroyed(hostinger, monkeypatch):
    import requests
    from app.models.cloud_server import CloudServer
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _GetResp(
        {'data': [{'id': 77, 'hostname': 'legacy', 'state': 'running'}]}))

    result = hostinger['svc'].sync_provider(hostinger['provider'].id)
    assert len(result['adopted']) == 1

    row = CloudServer.query.filter_by(external_id='77').first()
    assert row.origin == CloudServer.ORIGIN_ADOPTED
    svc_mod = importlib.import_module(f'{_PKG}.cloud_provisioning_service')
    with pytest.raises(svc_mod.AdoptedServerError):
        hostinger['svc'].destroy_server(row.id)
