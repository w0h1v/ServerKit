from datetime import datetime
from app import db
import json


class CloudProvider(db.Model):
    """Cloud provider configuration (DigitalOcean, Hetzner, Vultr, Linode)."""
    __tablename__ = 'cloud_providers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    provider_type = db.Column(db.String(32), nullable=False)  # digitalocean, hetzner, vultr, linode
    api_key_encrypted = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    servers = db.relationship('CloudServer', backref='provider', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'provider_type': self.provider_type,
            'is_active': self.is_active,
            'server_count': self.servers.count(),
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CloudServer(db.Model):
    """A cloud server ServerKit provisioned, or adopted from the provider."""
    __tablename__ = 'cloud_servers'
    # One row per remote server. Without this, re-running a discovery/sync would
    # insert a duplicate for every instance on every pass.
    __table_args__ = (
        db.UniqueConstraint('provider_id', 'external_id',
                            name='uq_cloud_servers_provider_external'),
    )

    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('cloud_providers.id'), nullable=False)
    external_id = db.Column(db.String(128))  # provider's server ID
    name = db.Column(db.String(128), nullable=False)
    hostname = db.Column(db.String(256))

    # Specs
    region = db.Column(db.String(64))
    size = db.Column(db.String(64))
    image = db.Column(db.String(128))  # OS image
    ip_address = db.Column(db.String(45))
    ipv6_address = db.Column(db.String(64))

    # Status
    STATUS_CREATING = 'creating'
    STATUS_ACTIVE = 'active'
    STATUS_OFF = 'off'
    STATUS_DESTROYED = 'destroyed'
    STATUS_ERROR = 'error'
    status = db.Column(db.String(32), default=STATUS_CREATING)

    # Cost
    monthly_cost = db.Column(db.Float, default=0)
    currency = db.Column(db.String(3), default='USD')

    # Auto-install agent
    agent_installed = db.Column(db.Boolean, default=False)

    # SSH key
    ssh_key_id = db.Column(db.String(128))

    # Metadata
    metadata_json = db.Column(db.Text)

    # How this row came to exist. 'provisioned' means ServerKit created the server
    # and owns its lifecycle; 'adopted' means it already existed at the provider
    # and was imported, so ServerKit did NOT create it and must not offer to
    # destroy it — an accidental click there would take out infrastructure the
    # panel never provisioned.
    ORIGIN_PROVISIONED = 'provisioned'
    ORIGIN_ADOPTED = 'adopted'
    origin = db.Column(db.String(16), default=ORIGIN_PROVISIONED, nullable=False)

    # Reconciliation against the provider. `missing_remote` is set when a server
    # we know about is no longer returned by the provider; it is deliberately NOT
    # the same as 'destroyed', because we did not observe a destroy and must not
    # silently claim one.
    SYNC_IN_SYNC = 'in_sync'
    SYNC_DRIFTED = 'drifted'
    SYNC_MISSING_REMOTE = 'missing_remote'
    sync_state = db.Column(db.String(16))
    last_synced_at = db.Column(db.DateTime)

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    destroyed_at = db.Column(db.DateTime)

    snapshots = db.relationship('CloudSnapshot', backref='server', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def server_metadata(self):
        return json.loads(self.metadata_json) if self.metadata_json else {}

    @server_metadata.setter
    def server_metadata(self, v):
        self.metadata_json = json.dumps(v)

    def to_dict(self):
        return {
            'id': self.id,
            'provider_id': self.provider_id,
            'provider_name': self.provider.name if self.provider else None,
            'provider_type': self.provider.provider_type if self.provider else None,
            'external_id': self.external_id,
            'name': self.name,
            'hostname': self.hostname,
            'region': self.region,
            'size': self.size,
            'image': self.image,
            'ip_address': self.ip_address,
            'ipv6_address': self.ipv6_address,
            'status': self.status,
            'monthly_cost': self.monthly_cost,
            'currency': self.currency,
            'agent_installed': self.agent_installed,
            'origin': self.origin or self.ORIGIN_PROVISIONED,
            # Surfaced so the UI can hide destroy on an adopted server rather than
            # relying on every caller to remember the rule.
            'can_destroy': (self.origin or self.ORIGIN_PROVISIONED) == self.ORIGIN_PROVISIONED,
            'sync_state': self.sync_state,
            'last_synced_at': self.last_synced_at.isoformat() if self.last_synced_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CloudSnapshot(db.Model):
    """Snapshot of a cloud server."""
    __tablename__ = 'cloud_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    server_id = db.Column(db.Integer, db.ForeignKey('cloud_servers.id'), nullable=False)
    external_id = db.Column(db.String(128))
    name = db.Column(db.String(128))
    size_gb = db.Column(db.Float)
    status = db.Column(db.String(32), default='creating')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'server_id': self.server_id,
            'external_id': self.external_id,
            'name': self.name,
            'size_gb': self.size_gb,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
