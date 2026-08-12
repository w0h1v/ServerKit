"""Proving tests for the one-click GitHub App manifest setup flow.

Covers the pure/mockable half of the flow: building the manifest, converting the
returned code into stored credentials, routing the connect URL through the app's
install screen when an app slug exists, surfacing app info in the config, and
listing repositories via installations in app mode. The live github.com
round-trip (create app → install → authorize) is verified manually.
"""
import flask
import pytest

from app.services.settings_service import SettingsService
from app.services.source_connection_service import SourceConnectionService as SC


def test_build_manifest_shape_and_state(app):
    with app.test_request_context():
        manifest, state, post_url = SC.build_github_app_manifest(
            'https://panel.example/connections/github-app/callback',
            'https://panel.example',
        )
        assert post_url == SC.GITHUB_APP_NEW_URL
        assert manifest['name'].startswith('ServerKit')
        assert manifest['url'] == 'https://panel.example'
        assert manifest['redirect_url'].endswith('/connections/github-app/callback')
        assert manifest['callback_urls'] == ['https://panel.example/connections/callback/github']
        assert manifest['default_permissions']['contents'] == 'read'
        assert manifest['request_oauth_on_install'] is True
        assert manifest['public'] is False
        # State is remembered for the conversion step.
        assert flask.session['source_github_app_state'] == state


def test_build_manifest_requires_urls(app):
    with app.test_request_context():
        with pytest.raises(ValueError):
            SC.build_github_app_manifest('', 'https://panel.example')


def _fake_conversion_response():
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                'id': 424242,
                'slug': 'serverkit-abc123',
                'name': 'ServerKit abc123',
                'client_id': 'Iv1.deadbeef',
                'client_secret': 'super-secret',
                'pem': '-----BEGIN RSA PRIVATE KEY-----\nkey\n-----END RSA PRIVATE KEY-----',
                'webhook_secret': 'wh-secret',
                'html_url': 'https://github.com/apps/serverkit-abc123',
            }
    return FakeResp()


def test_complete_manifest_stores_credentials(app, monkeypatch):
    from app.services import source_connection_service as mod
    monkeypatch.setattr(mod.requests, 'post', lambda *a, **k: _fake_conversion_response())

    with app.test_request_context():
        flask.session['source_github_app_state'] = 'state-xyz'
        result = SC.complete_github_app_manifest('code-123', 'state-xyz', user_id=None)

        assert result['slug'] == 'serverkit-abc123'
        assert result['install_url'] == 'https://github.com/apps/serverkit-abc123/installations/new'

        cfg = SC.get_github_config()
        assert cfg['configured'] is True
        assert cfg['provider_kind'] == 'app'
        assert cfg['app_slug'] == 'serverkit-abc123'
        assert cfg['install_url'].endswith('/installations/new')
        # The raw client id is stored; the PEM is stored encrypted (not plaintext).
        assert SettingsService.get('source_github_client_id') == 'Iv1.deadbeef'
        pem = SettingsService.get('source_github_app_pem') or ''
        assert pem and 'BEGIN RSA PRIVATE KEY' not in pem


def test_complete_manifest_rejects_bad_state(app):
    with app.test_request_context():
        flask.session['source_github_app_state'] = 'good'
        with pytest.raises(ValueError):
            SC.complete_github_app_manifest('code', 'bad', user_id=None)


def test_authorize_url_uses_install_when_app(app):
    with app.test_request_context():
        SettingsService.set('source_github_client_id', 'cid')
        SettingsService.set('source_github_client_secret', 'sec')
        SettingsService.set('source_github_app_slug', 'serverkit-abc123')
        url, state = SC.generate_github_authorize_url('https://panel.example/cb')
        assert 'apps/serverkit-abc123/installations/new' in url
        assert f'state={state}' in url


def test_authorize_url_classic_without_app(app):
    with app.test_request_context():
        SettingsService.set('source_github_client_id', 'cid')
        SettingsService.set('source_github_client_secret', 'sec')
        # No app slug -> classic OAuth authorize screen.
        url, _ = SC.generate_github_authorize_url('https://panel.example/cb')
        assert 'login/oauth/authorize' in url
        assert 'client_id=cid' in url


def test_list_repositories_app_mode_uses_installations(app, monkeypatch):
    monkeypatch.setattr(SC, '_get_github_token', classmethod(lambda cls, uid: 'tok'))

    def fake_get(cls, token, path):
        if path.startswith('/user/installations?'):
            return {'installations': [{'id': 1}, {'id': 2}]}
        if '/installations/1/repositories' in path:
            return {'repositories': [
                {'id': 10, 'name': 'alpha', 'full_name': 'me/alpha', 'updated_at': '2024-02-01'},
            ]}
        if '/installations/2/repositories' in path:
            return {'repositories': [
                {'id': 20, 'name': 'beta', 'full_name': 'me/beta', 'updated_at': '2024-03-01'},
            ]}
        return []

    monkeypatch.setattr(SC, '_github_get', classmethod(fake_get))

    with app.test_request_context():
        SettingsService.set('source_github_app_slug', 'serverkit-abc123')
        repos = SC.list_github_repositories(user_id=1)
        names = [r['full_name'] for r in repos]
        assert names == ['me/beta', 'me/alpha']  # sorted by updated_at desc


# --------------------------------------------------------------------------- #
# Reachability guard. GitHub builds the App on its own servers, so a manifest for
# a panel it cannot reach fails ON GITHUB: the operator is redirected to
# github.com, sees the error there, and is never sent back — so /app-manifest
# returns 200, no /complete call ever arrives, and the panel stores nothing. The
# whole attempt leaves no trace anywhere in the panel. Refuse up front instead,
# and name the flow that does work on a private host.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('base_url,fragment', [
    ('http://100.73.44.92:5100', 'https'),                 # plain HTTP
    ('http://panel.example.com', 'https'),
    ('https://192.168.1.5', 'private IP'),
    ('https://10.0.0.9:5100', 'private IP'),
    ('https://127.0.0.1:5100', 'private IP'),
    ('https://8.8.8.8', 'hostname, not a bare IP'),
    ('https://build.llama-panga.ts.net:5100', 'own network'),
    ('https://panel.local', 'own network'),
    ('https://myhost', 'not a public domain'),
    ('nonsense', 'not a valid URL'),
])
def test_unreachable_base_urls_are_refused(app, base_url, fragment):
    with app.test_request_context():
        with pytest.raises(ValueError) as exc:
            SC.build_github_app_manifest(f'{base_url}/connections/github-app/callback',
                                         base_url)
        message = str(exc.value)
        assert fragment in message
        # The refusal must carry the alternative, or it is just a dead end with
        # better wording.
        assert 'OAuth App' in message
        assert 'github.com/settings/developers' in message


@pytest.mark.parametrize('base_url', [
    'https://panel.example.com',
    'https://serverkit.example.co.uk:8443',
])
def test_public_https_urls_are_allowed(app, base_url):
    with app.test_request_context():
        manifest, _state, _post = SC.build_github_app_manifest(
            f'{base_url}/connections/github-app/callback', base_url)
        assert manifest['url'] == base_url


def test_blocker_helper_returns_none_for_public_https():
    assert SC._manifest_url_blocker('https://panel.example.com') is None
