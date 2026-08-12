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
