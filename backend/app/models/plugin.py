"""Installed plugin tracking model."""
from datetime import datetime
from app import db
import json


class InstalledPlugin(db.Model):
    """Tracks plugins installed from external sources (zips/URLs)."""
    __tablename__ = 'installed_plugins'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, unique=True)
    display_name = db.Column(db.String(256), nullable=False)
    slug = db.Column(db.String(128), nullable=False, unique=True)
    version = db.Column(db.String(32), nullable=False)
    description = db.Column(db.Text)
    author = db.Column(db.String(128))
    homepage = db.Column(db.String(512))
    repository = db.Column(db.String(512))
    license = db.Column(db.String(64))
    category = db.Column(db.String(64))

    # Where it came from
    source_url = db.Column(db.String(1024))
    source_type = db.Column(db.String(32), default='url')  # url, local, marketplace

    # Paths relative to ServerKit root
    backend_path = db.Column(db.String(512))   # e.g. app/plugins/serverkit-ai
    frontend_path = db.Column(db.String(512))  # e.g. src/plugins/serverkit-ai

    # Blueprint registration info from manifest
    entry_point = db.Column(db.String(256))    # e.g. blueprint:ai_assistant_bp
    url_prefix = db.Column(db.String(256))     # e.g. /api/v1/ai-assistant

    # Frontend entry from manifest
    frontend_entry = db.Column(db.String(256))  # e.g. components/AiAssistant.jsx

    # Full manifest stored as JSON
    manifest_json = db.Column(db.Text)

    # Saved per-plugin config values (#49). The manifest's `config_schema`
    # describes the fields; admins edit values from the Marketplace and the
    # plugin reads them via plugins_sdk.config(slug). May hold secrets — kept
    # out of to_dict; served only by the admin-gated config endpoint.
    config_json = db.Column(db.Text)

    # Status
    STATUS_ACTIVE = 'active'
    STATUS_DISABLED = 'disabled'
    STATUS_ERROR = 'error'
    STATUS_INSTALLING = 'installing'
    status = db.Column(db.String(32), default=STATUS_INSTALLING)
    error_message = db.Column(db.Text)

    # Has frontend component that needs rebuild
    has_frontend = db.Column(db.Boolean, default=False)
    # Has backend blueprint
    has_backend = db.Column(db.Boolean, default=False)

    installed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    installed_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def manifest(self):
        return json.loads(self.manifest_json) if self.manifest_json else {}

    @manifest.setter
    def manifest(self, v):
        self.manifest_json = json.dumps(v)

    @property
    def config(self):
        return json.loads(self.config_json) if self.config_json else {}

    @config.setter
    def config(self, v):
        self.config_json = json.dumps(v or {})

    def to_dict(self):
        manifest = self.manifest or {}
        return {
            'id': self.id,
            'name': self.name,
            'display_name': self.display_name,
            'slug': self.slug,
            'version': self.version,
            'description': self.description,
            'author': self.author,
            'homepage': self.homepage,
            'repository': self.repository,
            'license': self.license,
            'category': self.category,
            'source_url': self.source_url,
            'source_type': self.source_type,
            'entry_point': self.entry_point,
            'url_prefix': self.url_prefix,
            'has_frontend': self.has_frontend,
            'has_backend': self.has_backend,
            'status': self.status,
            'error_message': self.error_message,
            # Surface declarative manifest fields so the install UI can
            # show the user what they're approving without making a
            # second round-trip to fetch the manifest.
            'permissions': manifest.get('permissions') or [],
            'contributions': manifest.get('contributions') or {},
            'templates': manifest.get('templates') or [],
            'lifecycle': manifest.get('lifecycle') or {},
            # Schema only — saved VALUES may hold secrets and are served
            # exclusively by the admin-gated /config endpoint.
            'config_schema': manifest.get('config_schema') or {},
            # Install-time signature verdict (plan 55): None for sources that
            # predate stamping, else {status: verified|untrusted_key|unsigned,
            # key_id?, publisher?}. Panel-managed (reserved config key).
            'signature': (self.config or {}).get('_signature'),
            # Set when the extension's blueprint could not hot-load into the
            # already-running app (Flask forbids register_blueprint after the
            # first request), so its API stays unmounted until the panel
            # restarts. Panel-managed reserved config key; cleared at boot.
            'restart_required': bool((self.config or {}).get('_restart_required')),
            'installed_at': self.installed_at.isoformat() if self.installed_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
