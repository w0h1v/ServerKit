"""Proving tests for the App Platforms extension (Railway / Vercel / Supabase).

Every provider call is mocked — the suite never touches a real platform. What these
assert is the contract that makes four different APIs usable behind one UI:

  - a credential is VERIFIED before it is stored, so a bad token is refused at the
    point of entry instead of looking configured and failing somewhere unrelated
    later (the exact bug the cloud-provider path had with an invalid Vultr key)
  - each adapter normalises its platform's vocabulary into one project shape
  - one failing connection degrades to an error row, never an empty page
  - nothing in this extension can write to a platform
"""
import importlib
import sys

import pytest

from app import db
from app.models.plugin import InstalledPlugin
from app.models.platform_connection import PlatformConnection
from app.services import plugin_service

SLUG = 'serverkit-platforms'
_PKG = f'app.plugins.{SLUG}'


@pytest.fixture
def platforms(app, tmp_path, monkeypatch):
    """Install the extension into temp dirs and hand back its modules."""
    backend = tmp_path / 'backend_plugins'
    frontend = tmp_path / 'frontend_plugins'
    backend.mkdir()
    frontend.mkdir()
    monkeypatch.setattr(plugin_service, 'BACKEND_PLUGINS_DIR', str(backend))
    monkeypatch.setattr(plugin_service, 'FRONTEND_PLUGINS_DIR', str(frontend))

    added = str(backend)
    pkg_plugins = importlib.import_module('app.plugins')
    if added not in pkg_plugins.__path__:
        # insert(0) for the same reason as the cloud extension's fixture: on a box
        # where this extension is really installed, app/plugins/<slug>/ would win
        # the import and the tests would exercise the copy, not the source.
        pkg_plugins.__path__.insert(0, added)

    plugin = plugin_service.install_builtin_extension(SLUG)
    assert plugin.status == InstalledPlugin.STATUS_ACTIVE

    svc = importlib.import_module(f'{_PKG}.platform_service').PlatformService
    adapters = importlib.import_module(f'{_PKG}.platform_adapters')
    yield {'svc': svc, 'adapters': adapters, 'plugin': plugin}

    if added in pkg_plugins.__path__:
        pkg_plugins.__path__.remove(added)
    for name in list(sys.modules):
        if name == _PKG or name.startswith(_PKG + '.'):
            del sys.modules[name]


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self):
        return self._payload


def _connection(platform='vercel', token='tok-abc'):
    from app.utils.crypto import encrypt_secret
    c = PlatformConnection(name=f'{platform}-conn', platform=platform,
                           api_token_encrypted=encrypt_secret(token), is_active=True)
    db.session.add(c)
    db.session.commit()
    return c


# --------------------------------------------------------------------------- #
# Extension shape
# --------------------------------------------------------------------------- #

def test_extension_registers_its_routes(platforms, client, auth_headers):
    assert platforms['plugin'].url_prefix == '/api/v1/platforms'
    resp = client.get('/api/v1/platforms/catalog', headers=auth_headers)
    assert resp.status_code == 200
    got = {p['platform'] for p in resp.get_json()['platforms']}
    assert got == {'railway', 'vercel', 'supabase'}


def test_extension_is_not_auto_installed_on_upgrade(app):
    """A brand-new integration must not appear on someone's panel uninvited.

    CONVERTED_BUILTIN_SLUGS means "was core, now extracted" — putting a new
    extension there would silently install it on every upgraded panel.
    """
    from app.services.extension_migration import CONVERTED_BUILTIN_SLUGS
    assert SLUG not in CONVERTED_BUILTIN_SLUGS


def test_token_is_never_serialised(platforms):
    c = _connection()
    assert 'api_token_encrypted' not in c.to_dict()
    assert 'tok-abc' not in str(c.to_dict())


def test_token_is_encrypted_at_rest(platforms):
    from app.utils.crypto import is_encrypted, decrypt_secret_safe
    c = _connection(token='super-secret-token')
    assert c.api_token_encrypted != 'super-secret-token'
    assert is_encrypted(c.api_token_encrypted)
    assert decrypt_secret_safe(c.api_token_encrypted) == 'super-secret-token'


# --------------------------------------------------------------------------- #
# Verify-before-store. The cloud path stored an unvalidated key and only failed at
# provision time; every adapter here proves the token first.
# --------------------------------------------------------------------------- #

def test_bad_token_is_refused_and_nothing_is_stored(platforms, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp({}, status_code=401))
    with pytest.raises(platforms['adapters'].PlatformError, match='rejected the token'):
        platforms['svc'].create_connection(
            {'platform': 'vercel', 'api_token': 'wrong'})
    assert PlatformConnection.query.count() == 0


def test_good_token_is_stored_with_the_resolved_account(platforms, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get',
                        lambda *a, **k: _Resp({'user': {'username': 'orie'}}))
    c = platforms['svc'].create_connection({'platform': 'vercel', 'api_token': 'good'})
    assert c.account_label == 'orie'
    assert c.name == 'orie'          # defaults to the account, not a placeholder
    assert c.last_verified_at is not None


def test_unsupported_platform_is_refused(platforms):
    with pytest.raises(ValueError, match='Unsupported platform'):
        platforms['svc'].create_connection({'platform': 'heroku', 'api_token': 'x'})


def test_missing_token_is_refused(platforms):
    with pytest.raises(ValueError, match='token is required'):
        platforms['svc'].create_connection({'platform': 'vercel', 'api_token': ''})


def test_create_endpoint_returns_400_for_a_rejected_token(
        platforms, client, auth_headers, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp({}, status_code=403))
    resp = client.post('/api/v1/platforms/connections', headers=auth_headers,
                       json={'platform': 'supabase', 'api_token': 'nope'})
    assert resp.status_code == 400
    assert resp.get_json()['verified'] is False


# --------------------------------------------------------------------------- #
# Normalisation — three vocabularies, one project shape
# --------------------------------------------------------------------------- #

PROJECT_KEYS = {'external_id', 'name', 'status', 'url', 'region', 'framework', 'updated_at'}


def test_vercel_projects_normalise(platforms, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp({
        'projects': [{'id': 'prj_1', 'name': 'site', 'framework': 'nextjs',
                      'updatedAt': 1755000000000,
                      'targets': {'production': {'readyState': 'READY', 'url': 'site.vercel.app'}}}],
        'pagination': {'next': None},
    }))
    a = platforms['adapters'].VercelAdapter('t')
    p = a.list_projects()[0]
    assert PROJECT_KEYS <= set(p)
    assert p['external_id'] == 'prj_1'
    assert p['status'] == 'ready'                       # lowercased from READY
    assert p['url'] == 'https://site.vercel.app'
    assert p['updated_at'].startswith('202')            # epoch ms -> ISO


def test_vercel_project_with_no_deployment_says_so(platforms, monkeypatch):
    """Inventing a health value for a never-deployed project would be a lie."""
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp(
        {'projects': [{'id': 'p', 'name': 'fresh', 'targets': {}}], 'pagination': {}}))
    p = platforms['adapters'].VercelAdapter('t').list_projects()[0]
    assert p['status'] == 'no deployment'
    assert p['url'] is None


def test_vercel_follows_pagination(platforms, monkeypatch):
    import requests
    pages = [
        _Resp({'projects': [{'id': 'a', 'name': 'a', 'targets': {}}],
               'pagination': {'next': 1700000000000}}),
        _Resp({'projects': [{'id': 'b', 'name': 'b', 'targets': {}}],
               'pagination': {'next': None}}),
    ]
    calls = []

    def fake_get(url, **kw):
        calls.append((kw.get('params') or {}).get('until'))
        return pages[len(calls) - 1]

    monkeypatch.setattr(requests, 'get', fake_get)
    assert len(platforms['adapters'].VercelAdapter('t').list_projects()) == 2
    assert calls == [None, 1700000000000]


def test_vercel_scope_id_is_sent_as_team_id(platforms, monkeypatch):
    import requests
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw.get('params') or {})
        return _Resp({'projects': [], 'pagination': {}})

    monkeypatch.setattr(requests, 'get', fake_get)
    platforms['adapters'].VercelAdapter('t', scope_id='team_xyz').list_projects()
    assert seen.get('teamId') == 'team_xyz'


def test_supabase_projects_normalise(platforms, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp([
        {'id': 'abc', 'ref': 'abcdefgh', 'name': 'prod-db', 'status': 'ACTIVE_HEALTHY',
         'region': 'us-east-1', 'created_at': '2026-01-02T03:04:05Z',
         'database': {'version': '15.1'}},
    ]))
    p = platforms['adapters'].SupabaseAdapter('t').list_projects()[0]
    assert PROJECT_KEYS <= set(p)
    assert p['status'] == 'active_healthy'
    assert p['url'] == 'https://abcdefgh.supabase.co'
    assert p['framework'] == 'postgres 15.1'


def test_railway_projects_normalise(platforms, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **k: _Resp({'data': {'me': {'projects': {
        'edges': [{'node': {'id': 'proj_1', 'name': 'api', 'description': 'the api',
                            'updatedAt': '2026-02-03T04:05:06Z',
                            'environments': {'edges': [{'node': {'id': 'e1', 'name': 'production'}}]}}}],
    }}}}))
    p = platforms['adapters'].RailwayAdapter('t').list_projects()[0]
    assert PROJECT_KEYS <= set(p)
    assert p['external_id'] == 'proj_1'
    assert p['status'] == '1 environment'     # singular, not "1 environments"
    assert p['url'].endswith('/project/proj_1')


def test_railway_reports_a_graphql_error_body(platforms, monkeypatch):
    """GraphQL returns failures in a 200, so an HTTP check alone would call an auth
    error a success and hand back empty data."""
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **k: _Resp(
        {'errors': [{'message': 'Not Authorized'}]}))
    with pytest.raises(platforms['adapters'].PlatformError, match='Not Authorized'):
        platforms['adapters'].RailwayAdapter('t').list_projects()


def test_rate_limit_message_is_actionable(platforms, monkeypatch):
    import requests
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp({}, status_code=429))
    with pytest.raises(platforms['adapters'].PlatformError, match='rate-limited'):
        platforms['adapters'].SupabaseAdapter('t').list_projects()


# --------------------------------------------------------------------------- #
# Inventory fan-out: partial results beat no results
# --------------------------------------------------------------------------- #

def test_one_dead_connection_does_not_blank_the_page(platforms, monkeypatch):
    import requests
    _connection('vercel')
    _connection('supabase')

    def fake_get(url, **kw):
        if 'supabase' in url:
            return _Resp({}, status_code=500)
        return _Resp({'projects': [{'id': 'p1', 'name': 'live', 'targets': {}}],
                      'pagination': {}})

    monkeypatch.setattr(requests, 'get', fake_get)
    inv = platforms['svc'].inventory()
    assert [p['name'] for p in inv['projects']] == ['live']
    assert len(inv['errors']) == 1
    assert inv['errors'][0]['platform'] == 'supabase'
    assert len(inv['connections']) == 2


def test_inventory_tags_each_project_with_its_connection(platforms, monkeypatch):
    import requests
    c = _connection('vercel')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp(
        {'projects': [{'id': 'p', 'name': 'x', 'targets': {}}], 'pagination': {}}))
    p = platforms['svc'].inventory()['projects'][0]
    assert p['connection_id'] == c.id and p['connection_name'] == c.name


def test_inventory_can_filter_to_one_platform(platforms, monkeypatch):
    import requests
    _connection('vercel')
    _connection('railway')
    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp({'projects': [], 'pagination': {}}))
    inv = platforms['svc'].inventory(platform='vercel')
    assert [c['platform'] for c in inv['connections']] == ['vercel']


def test_deleting_a_connection_only_forgets_the_credential(platforms):
    """Soft delete: this must never look like deleting someone's projects."""
    c = _connection()
    assert platforms['svc'].delete_connection(c.id) is True
    row = PlatformConnection.query.get(c.id)
    assert row is not None and row.is_active is False


def test_delete_endpoint_says_the_platform_was_untouched(
        platforms, client, auth_headers):
    c = _connection()
    resp = client.delete(f'/api/v1/platforms/connections/{c.id}', headers=auth_headers)
    assert resp.status_code == 200
    assert 'Nothing on the platform was changed' in resp.get_json()['message']


def test_extension_exposes_no_write_path_to_a_platform(platforms, app):
    """Read-only is a property of the surface, not just an intention."""
    writes = [r for r in app.url_map.iter_rules()
              if str(r.rule).startswith('/api/v1/platforms')
              and {'POST', 'PUT', 'PATCH', 'DELETE'} & set(r.methods)]
    # The only writes are to our OWN connection rows.
    assert sorted({str(r.rule) for r in writes}) == [
        '/api/v1/platforms/connections',
        '/api/v1/platforms/connections/<int:connection_id>',
        '/api/v1/platforms/connections/<int:connection_id>/verify',
    ]
