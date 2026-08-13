"""HTTP surface for connected application platforms. Mounted at /api/v1/platforms.

Read-only over the platforms themselves: the only writes are to OUR connection
rows (add a credential, re-verify it, forget it). Nothing here can change or delete
anything on Railway, Vercel or Supabase.
"""
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

from .platform_adapters import PlatformError
from .platform_service import PlatformService

platforms_bp = Blueprint('platforms', __name__)


def get_current_user():
    from flask_jwt_extended import get_jwt_identity
    from app.models.user import User
    return User.query.get(get_jwt_identity())


def _require_admin():
    user = get_current_user()
    if not user or not user.is_admin:
        return None, (jsonify({'error': 'Admin access required'}), 403)
    return user, None


@platforms_bp.route('/catalog', methods=['GET'])
@jwt_required()
def catalog():
    """Connectable platforms and what each one's token looks like.

    Served rather than hardcoded in the UI so adding an adapter lights up the
    connect form without a frontend change.
    """
    return jsonify({'platforms': PlatformService.catalog()})


@platforms_bp.route('/connections', methods=['GET'])
@jwt_required()
def list_connections():
    return jsonify({'connections': [c.to_dict()
                                    for c in PlatformService.list_connections()]})


@platforms_bp.route('/connections', methods=['POST'])
@jwt_required()
def create_connection():
    _user, err = _require_admin()
    if err:
        return err
    try:
        connection = PlatformService.create_connection(request.get_json() or {},
                                                       user_id=_user.id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except PlatformError as e:
        # The credential was rejected by the platform, so nothing was stored. 400
        # rather than 502: the token is the problem, and the operator can fix it.
        return jsonify({'error': str(e), 'verified': False}), 400
    return jsonify(connection.to_dict()), 201


@platforms_bp.route('/connections/<int:connection_id>/verify', methods=['POST'])
@jwt_required()
def verify_connection(connection_id):
    _user, err = _require_admin()
    if err:
        return err
    try:
        result = PlatformService.verify_connection(connection_id)
    except PlatformError as e:
        return jsonify({'error': str(e), 'ok': False}), 400
    if result is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(result)


@platforms_bp.route('/connections/<int:connection_id>', methods=['DELETE'])
@jwt_required()
def delete_connection(connection_id):
    _user, err = _require_admin()
    if err:
        return err
    if not PlatformService.delete_connection(connection_id):
        return jsonify({'error': 'Not found'}), 404
    # Say plainly that this only forgets a credential — the word "delete" next to a
    # list of someone's production projects deserves the reassurance.
    return jsonify({'message': 'Connection removed. Nothing on the platform was changed.'})


@platforms_bp.route('/inventory', methods=['GET'])
@jwt_required()
def inventory():
    """Live projects across every connection, with per-connection errors inline."""
    return jsonify(PlatformService.inventory(request.args.get('platform')))


@platforms_bp.route('/connections/<int:connection_id>/projects/<path:project_ref>/resources',
                    methods=['GET'])
@jwt_required()
def project_resources(connection_id, project_ref):
    try:
        result = PlatformService.project_resources(connection_id, project_ref)
    except PlatformError as e:
        return jsonify({'error': str(e)}), 502
    except Exception as e:
        current_app.logger.error('Platform resources failed for %s/%s: %s',
                                 connection_id, project_ref, e)
        return jsonify({'error': f'Could not reach the platform: {e}'}), 502
    if result is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(result)
