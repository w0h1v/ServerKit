"""A connected application platform (PaaS/BaaS) — Railway, Vercel, Supabase.

Deliberately NOT CloudProvider. A cloud provider hands you a server: it has a
region, a size, an image, an IP, an agent, and ServerKit can create and destroy
it. A platform hands you a *project* — there is no host to reach, nothing to
install an agent on, and no size to resize. Modelling them together would mean a
CloudServer row with almost every column null and a destroy button that cannot
mean what it says.

What these connections are for is visibility: one place that answers "what do I
have running, and where", across a fleet that spans a VPS, a Vercel project and a
Supabase database. So the connection stores a credential and nothing else — the
inventory is read live, per request, and never mirrored into local rows. That
sidesteps the whole adoption/reconciliation problem: there is no local copy to
drift, and nothing here can claim a project was deleted because a list came back
short.
"""
from datetime import datetime

from app import db


class PlatformConnection(db.Model):
    __tablename__ = 'platform_connections'

    # Platform keys. Adding one means adding an adapter in the platforms
    # extension; the key is the dispatch value, so it is part of the contract.
    PLATFORM_RAILWAY = 'railway'
    PLATFORM_VERCEL = 'vercel'
    PLATFORM_SUPABASE = 'supabase'
    PLATFORMS = (PLATFORM_RAILWAY, PLATFORM_VERCEL, PLATFORM_SUPABASE)

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    platform = db.Column(db.String(32), nullable=False)

    # Encrypted at rest via app.utils.crypto, like every other provider secret.
    api_token_encrypted = db.Column(db.Text)

    # Some platforms scope a token to one team/org and need it echoed on each
    # call (Vercel's teamId, Railway's workspace). Free-text because each
    # platform names it differently and none of them are secret.
    scope_id = db.Column(db.String(128))

    # Identity the credential resolved to at save time, so the UI can show WHOSE
    # account this is without another round trip — and so a swapped token is
    # visible as a changed account rather than silently pointing elsewhere.
    account_label = db.Column(db.String(128))

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    last_verified_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'platform': self.platform,
            'scope_id': self.scope_id,
            'account_label': self.account_label,
            'is_active': self.is_active,
            'last_verified_at': self.last_verified_at.isoformat() if self.last_verified_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            # The token is never serialised, at any verbosity.
        }
