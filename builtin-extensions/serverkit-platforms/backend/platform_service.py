"""Connection CRUD plus live inventory across every connected platform.

Nothing here is cached or mirrored. `inventory()` fans out to each connection on
every call and returns whatever answered, with a per-connection error entry for
whatever did not — the same shape the DNS portfolio uses, and for the same reason:
one platform being down must not blank a page that covers four.
"""
import logging
from datetime import datetime

from app import db
from app.models.platform_connection import PlatformConnection
from app.utils.crypto import encrypt_secret, decrypt_secret_safe

from .platform_adapters import PlatformError, catalog, get_adapter

logger = logging.getLogger(__name__)


class PlatformService:

    @staticmethod
    def catalog():
        return catalog()

    @staticmethod
    def list_connections():
        return PlatformConnection.query.filter_by(is_active=True).order_by(
            PlatformConnection.platform, PlatformConnection.name).all()

    @staticmethod
    def get_connection(connection_id):
        return PlatformConnection.query.get(connection_id)

    @staticmethod
    def _adapter_for(connection):
        return get_adapter(connection.platform,
                           decrypt_secret_safe(connection.api_token_encrypted or ''),
                           connection.scope_id)

    @staticmethod
    def create_connection(data, user_id=None):
        """Verify the credential, THEN store it.

        Verifying first is the point. A token that is never exercised at save time
        produces a connection that looks configured and is not — the operator finds
        out later, somewhere unrelated, from an error that does not mention the
        credential. Refuse up front instead.
        """
        platform = (data.get('platform') or '').strip()
        token = (data.get('api_token') or '').strip()
        if platform not in PlatformConnection.PLATFORMS:
            raise ValueError(f'Unsupported platform: {platform or "(none)"}')
        if not token:
            raise ValueError('An API token is required')

        scope_id = (data.get('scope_id') or '').strip() or None
        adapter = get_adapter(platform, token, scope_id)
        identity = adapter.verify()  # raises PlatformError on a bad credential

        connection = PlatformConnection(
            name=(data.get('name') or '').strip() or identity.get('account') or platform,
            platform=platform,
            api_token_encrypted=encrypt_secret(token),
            scope_id=scope_id,
            account_label=identity.get('account'),
            last_verified_at=datetime.utcnow(),
            created_by=user_id,
        )
        db.session.add(connection)
        db.session.commit()
        return connection

    @staticmethod
    def verify_connection(connection_id):
        """Re-check a stored credential and refresh the identity we show."""
        connection = PlatformConnection.query.get(connection_id)
        if not connection:
            return None
        identity = PlatformService._adapter_for(connection).verify()
        connection.account_label = identity.get('account')
        connection.last_verified_at = datetime.utcnow()
        db.session.commit()
        return {'ok': True, 'account': connection.account_label,
                'verified_at': connection.last_verified_at.isoformat()}

    @staticmethod
    def delete_connection(connection_id):
        """Soft-delete, mirroring CloudProvider.delete_provider.

        Nothing on the platform is touched — this forgets a credential, it does not
        delete anyone's projects.
        """
        connection = PlatformConnection.query.get(connection_id)
        if not connection:
            return False
        connection.is_active = False
        db.session.commit()
        return True

    @staticmethod
    def inventory(platform=None):
        """Live projects across connections. Partial results beat no results."""
        connections = PlatformService.list_connections()
        if platform:
            connections = [c for c in connections if c.platform == platform]

        projects, errors = [], []
        for connection in connections:
            try:
                for project in PlatformService._adapter_for(connection).list_projects():
                    projects.append({
                        **project,
                        'connection_id': connection.id,
                        'connection_name': connection.name,
                        'platform': connection.platform,
                    })
            except PlatformError as e:
                errors.append({'connection_id': connection.id,
                               'connection_name': connection.name,
                               'platform': connection.platform, 'error': str(e)})
            except Exception as e:  # a broken adapter must not take the page down
                logger.warning('Platform inventory failed for %s: %s', connection.name, e)
                errors.append({'connection_id': connection.id,
                               'connection_name': connection.name,
                               'platform': connection.platform,
                               'error': f'Unexpected error: {e}'})

        return {
            'projects': projects,
            'errors': errors,
            'connections': [c.to_dict() for c in connections],
        }

    @staticmethod
    def project_resources(connection_id, project_ref):
        connection = PlatformConnection.query.get(connection_id)
        if not connection or not connection.is_active:
            return None
        return {
            'resources': PlatformService._adapter_for(connection).list_resources(project_ref),
        }
