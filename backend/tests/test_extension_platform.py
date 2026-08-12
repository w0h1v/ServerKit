"""Phase 3 platform primitives: version gates, permissions, data models,
declarative jobs, socket extension point, and data-purge on uninstall.

Backend plugin code normally lives at app/plugins/<slug>/ on disk and is imported
by name. To exercise the importlib-driven hooks without writing packages into the
repo, we inject synthetic modules into sys.modules under the expected names.
"""
import io
import json
import sys
import types
import zipfile

import pytest

from app import db
from app.models.plugin import InstalledPlugin
from app.services import plugin_service, extension_lifecycle
from app.plugins_sdk import permissions, sockets as sdk_sockets
import app.plugins_sdk as sdk


SLUG = 'testext'


# A plugin-owned model, defined once against the real db so it registers on the
# shared metadata a single time (extend_existing keeps re-imports harmless).
class _ExtThing(db.Model):
    __tablename__ = 'ext_testext_thing'
    __table_args__ = {'extend_existing': True}
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(40))


_job_calls = []


def _inject_plugin_modules():
    """Register synthetic app.plugins.testext.{models,jobs,sockets} modules."""
    pkg = types.ModuleType(f'app.plugins.{SLUG}')
    pkg.__path__ = []
    sys.modules[f'app.plugins.{SLUG}'] = pkg

    models_mod = types.ModuleType(f'app.plugins.{SLUG}.models')
    models_mod.register = lambda _db: [_ExtThing]
    sys.modules[f'app.plugins.{SLUG}.models'] = models_mod

    jobs_mod = types.ModuleType(f'app.plugins.{SLUG}.jobs')
    jobs_mod.reindex = lambda job: _job_calls.append(job)
    sys.modules[f'app.plugins.{SLUG}.jobs'] = jobs_mod

    sockets_mod = types.ModuleType(f'app.plugins.{SLUG}.sockets')

    def _register():
        def on_connect():
            return 'connected'
        def on_ping(data):
            return {'pong': data}
        return {'connect': on_connect, 'ping': on_ping}
    sockets_mod.register = _register
    sys.modules[f'app.plugins.{SLUG}.sockets'] = sockets_mod


@pytest.fixture
def injected_plugin():
    _inject_plugin_modules()
    yield
    for name in list(sys.modules):
        if name == f'app.plugins.{SLUG}' or name.startswith(f'app.plugins.{SLUG}.'):
            del sys.modules[name]
    _job_calls.clear()


def _plugin_row(manifest):
    p = InstalledPlugin(
        name=SLUG, display_name='Test Ext', slug=SLUG, version='1.0.0',
        status=InstalledPlugin.STATUS_ACTIVE, has_backend=True,
    )
    p.manifest = manifest
    db.session.add(p)
    db.session.commit()
    return p


# --------------------------------------------------------------------------- #
# #28 — version compat gate at install (all sources)
# --------------------------------------------------------------------------- #

def test_min_panel_version_blocks_install(app):
    manifest = {'name': 'toonew', 'display_name': 'Too New', 'version': '1.0.0',
                'min_panel_version': '999.0.0'}
    with pytest.raises(ValueError, match='needs panel'):
        plugin_service._assert_manifest_panel_compatible(manifest)


def test_no_bounds_installs_anywhere(app):
    # Missing bounds → no exception.
    plugin_service._assert_manifest_panel_compatible(
        {'name': 'x', 'display_name': 'X', 'version': '1.0.0'})


def test_sdk_exposes_panel_version(app):
    v = sdk.panel_version()
    assert isinstance(v, str) and v


# --------------------------------------------------------------------------- #
# #25 — permission enforcement
# --------------------------------------------------------------------------- #

def test_permission_gate(app):
    _plugin_row({'permissions': ['docker', 'network']})
    assert permissions.has(SLUG, 'docker') is True
    assert permissions.has(SLUG, 'shell') is False
    assert permissions.require(SLUG, 'docker') is True
    with pytest.raises(permissions.PermissionDenied):
        permissions.require(SLUG, 'shell')
    # SDK re-export points at the same gate.
    with pytest.raises(permissions.PermissionDenied):
        sdk.require_permission(SLUG, 'filesystem')


def test_unknown_permissions_flagged():
    assert permissions.unknown_permissions(['docker', 'bogus', 'agent.command:x']) == ['bogus']


# --------------------------------------------------------------------------- #
# #24 — plugin data models + purge
# --------------------------------------------------------------------------- #

def test_register_and_purge_models(app, injected_plugin):
    from sqlalchemy import inspect
    p = _plugin_row({'models': 'models:register'})

    extension_lifecycle.register_models(p, p.manifest)
    assert 'ext_testext_thing' in inspect(db.engine).get_table_names()

    dropped = extension_lifecycle.purge_models(p)
    assert dropped >= 1
    assert 'ext_testext_thing' not in inspect(db.engine).get_table_names()


# --------------------------------------------------------------------------- #
# #29 — declarative jobs (register / pause / resume / remove)
# --------------------------------------------------------------------------- #

def test_declarative_jobs_lifecycle(app, injected_plugin):
    from app.jobs import registry
    from app.jobs.models import ScheduledJob

    manifest = {
        'jobs': [{'kind': 'testext.reindex', 'handler': 'jobs:reindex'}],
        'schedules': [{'name': 'testext-nightly', 'kind': 'testext.reindex',
                       'interval_seconds': 3600}],
    }
    p = _plugin_row(manifest)

    extension_lifecycle.register_jobs(p, manifest)
    assert registry.get('testext.reindex') is not None
    sched = ScheduledJob.query.filter_by(name='testext-nightly').first()
    assert sched is not None and sched.enabled is True

    extension_lifecycle.pause_jobs(p, manifest)
    assert ScheduledJob.query.filter_by(name='testext-nightly').first().enabled is False

    extension_lifecycle.resume_jobs(p, manifest)
    assert ScheduledJob.query.filter_by(name='testext-nightly').first().enabled is True

    extension_lifecycle.remove_jobs(p, manifest)
    assert ScheduledJob.query.filter_by(name='testext-nightly').first() is None


def test_disable_enable_pauses_jobs(app, injected_plugin):
    from app.jobs.models import ScheduledJob
    manifest = {
        'schedules': [{'name': 'testext-nightly', 'kind': 'testext.reindex',
                       'interval_seconds': 3600}],
    }
    p = _plugin_row(manifest)
    extension_lifecycle.register_jobs(p, manifest)

    plugin_service.disable_plugin(p.id)
    assert ScheduledJob.query.filter_by(name='testext-nightly').first().enabled is False
    plugin_service.enable_plugin(p.id)
    assert ScheduledJob.query.filter_by(name='testext-nightly').first().enabled is True


# --------------------------------------------------------------------------- #
# #26 — Socket.IO extension point
# --------------------------------------------------------------------------- #

class _FakeIO:
    def __init__(self):
        self.registered = []
    def on_event(self, event, handler, namespace=None):
        self.registered.append((event, namespace, handler))


def test_socket_namespace_registration(app, injected_plugin, monkeypatch):
    fake = _FakeIO()
    monkeypatch.setattr('app.get_socketio', lambda: fake, raising=False)
    import app as app_pkg
    monkeypatch.setattr(app_pkg, 'get_socketio', lambda: fake)

    p = _plugin_row({'socket_entry': 'sockets:register'})
    ns = sdk_sockets.register_from_manifest(p, p.manifest)

    assert ns == f'/ext/{SLUG}'
    events = {e for e, _ns, _h in fake.registered}
    assert events == {'connect', 'ping'}
    assert all(_ns == f'/ext/{SLUG}' for _e, _ns, _h in fake.registered)


# --------------------------------------------------------------------------- #
# #30 — uninstall purge vs keep-data
# --------------------------------------------------------------------------- #

def test_uninstall_purge_drops_tables(app, injected_plugin, tmp_path, monkeypatch):
    from sqlalchemy import inspect
    monkeypatch.setattr(plugin_service, 'BACKEND_PLUGINS_DIR', str(tmp_path / 'b'))
    monkeypatch.setattr(plugin_service, 'FRONTEND_PLUGINS_DIR', str(tmp_path / 'f'))

    p = _plugin_row({'models': 'models:register'})
    extension_lifecycle.register_models(p, p.manifest)
    assert 'ext_testext_thing' in inspect(db.engine).get_table_names()

    plugin_service.uninstall_plugin(p.id, purge=True)
    assert 'ext_testext_thing' not in inspect(db.engine).get_table_names()


def test_uninstall_keep_data_leaves_tables(app, injected_plugin, tmp_path, monkeypatch):
    from sqlalchemy import inspect
    monkeypatch.setattr(plugin_service, 'BACKEND_PLUGINS_DIR', str(tmp_path / 'b'))
    monkeypatch.setattr(plugin_service, 'FRONTEND_PLUGINS_DIR', str(tmp_path / 'f'))

    p = _plugin_row({'models': 'models:register'})
    extension_lifecycle.register_models(p, p.manifest)

    plugin_service.uninstall_plugin(p.id, purge=False)
    assert 'ext_testext_thing' in inspect(db.engine).get_table_names()


# --------------------------------------------------------------------------- #
# Pending-restart flag — Flask refuses register_blueprint once the app has
# served a request, so an extension installed on a RUNNING panel gets an
# 'active' row with no mounted routes. The flag makes that visible instead of
# leaving every call to its API 404ing with only a log line to explain it.
# --------------------------------------------------------------------------- #

def test_restart_required_flag_round_trip(app):
    p = _plugin_row({})
    assert p.to_dict()['restart_required'] is False

    plugin_service._set_restart_required(p, True, reason='needs a restart')
    assert p.config['_restart_required'] == {'reason': 'needs a restart'}
    assert p.to_dict()['restart_required'] is True

    plugin_service._set_restart_required(p, False)
    assert '_restart_required' not in p.config
    assert p.to_dict()['restart_required'] is False


def test_restart_required_survives_a_config_put(app):
    """The flag is panel-managed: a user config PUT must not be able to clear
    (or forge) it, same guarantee as _signature/_frontend_hashes."""
    assert '_restart_required' in plugin_service.RESERVED_PLUGIN_CONFIG_KEYS


def test_boot_load_clears_restart_required(app, injected_plugin, monkeypatch):
    """The restart the flag asked for actually clears it."""
    from flask import Blueprint

    api_mod = types.ModuleType(f'app.plugins.{SLUG}.api')
    bp = Blueprint('testext_api', __name__)

    @bp.route('/ping')
    def _ping():  # an empty blueprint contributes no URL rules
        return {'ok': True}

    api_mod.test_bp = bp
    sys.modules[f'app.plugins.{SLUG}.api'] = api_mod

    # The synthetic plugin has no files on disk; the repair pass would flip it
    # to 'error' before the loader ever sees it.
    monkeypatch.setattr(plugin_service, 'repair_missing_plugins', lambda: None)

    p = _plugin_row({})
    p.entry_point = 'api:test_bp'
    p.url_prefix = f'/api/v1/{SLUG}'
    db.session.commit()
    plugin_service._set_restart_required(p, True, reason='pending')
    assert p.to_dict()['restart_required'] is True

    plugin_service.load_all_plugins(app)

    # load_all_plugins runs in its own app context, and Flask-SQLAlchemy scopes
    # the session to the context — so re-read the row rather than trusting the
    # instance this session loaded before the call.
    db.session.expire_all()
    reloaded = InstalledPlugin.query.filter_by(slug=SLUG).first()
    assert reloaded.status == InstalledPlugin.STATUS_ACTIVE
    assert reloaded.to_dict()['restart_required'] is False
    assert any(r.rule.startswith(f'/api/v1/{SLUG}') for r in app.url_map.iter_rules())
