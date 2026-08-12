from flask import Blueprint, current_app, request, jsonify
from flask_jwt_extended import jwt_required
from .cloud_provisioning_service import AdoptedServerError, CloudProvisioningService

cloud_provisioning_bp = Blueprint('cloud_provisioning', __name__)


def get_current_user():
    from flask_jwt_extended import get_jwt_identity
    from app.models.user import User
    return User.query.get(get_jwt_identity())


# --- Providers ---

@cloud_provisioning_bp.route('/providers', methods=['GET'])
@jwt_required()
def list_providers():
    providers = CloudProvisioningService.list_providers()
    return jsonify({'providers': [
        # supports_discovery tells the UI whether to offer "import existing
        # servers" for this provider, so the affordance is never shown for a
        # provider whose inventory we cannot enumerate.
        {**p.to_dict(),
         'supports_discovery': CloudProvisioningService.supports_discovery(p.provider_type)}
        for p in providers
    ]})


@cloud_provisioning_bp.route('/providers', methods=['POST'])
@jwt_required()
def create_provider():
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json()
    try:
        provider = CloudProvisioningService.create_provider(data, user.id)
        return jsonify(provider.to_dict()), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@cloud_provisioning_bp.route('/providers/<int:provider_id>', methods=['DELETE'])
@jwt_required()
def delete_provider(provider_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    if not CloudProvisioningService.delete_provider(provider_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'message': 'Provider removed'})


@cloud_provisioning_bp.route('/providers/<int:provider_id>/discover', methods=['GET'])
@jwt_required()
def discover_provider(provider_id):
    """Preview the provider's live inventory against what we already track.

    Read-only by design: nothing is adopted here, so the UI can show what an
    import would do and take an explicit confirmation. Adoption must never be a
    side effect of loading a page.
    """
    try:
        result = CloudProvisioningService.discover_provider(provider_id)
    except NotImplementedError as e:
        # A provider we hold credentials for but cannot enumerate yet.
        return jsonify({'error': str(e), 'supported': False}), 501
    except Exception as e:
        current_app.logger.error('Cloud discovery failed for provider %s: %s',
                                 provider_id, e)
        return jsonify({'error': f'Could not reach the provider: {e}'}), 502
    if result is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(result)


@cloud_provisioning_bp.route('/providers/<int:provider_id>/sync', methods=['POST'])
@jwt_required()
def sync_provider(provider_id):
    """Adopt the provider's untracked servers and refresh the ones we know.

    Destroys nothing: a server that vanished remotely is flagged, never deleted.
    """
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    try:
        result = CloudProvisioningService.sync_provider(
            provider_id, user_id=user.id)
    except NotImplementedError as e:
        return jsonify({'error': str(e), 'supported': False}), 501
    except Exception as e:
        current_app.logger.error('Cloud sync failed for provider %s: %s',
                                 provider_id, e)
        return jsonify({'error': f'Could not reach the provider: {e}'}), 502
    if result is None:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(result)


@cloud_provisioning_bp.route('/providers/<provider_type>/options', methods=['GET'])
@jwt_required()
def get_options(provider_type):
    options = CloudProvisioningService.get_provider_options(provider_type)
    if not options:
        return jsonify({'error': 'Unknown provider'}), 404
    return jsonify(options)


# --- Servers ---

@cloud_provisioning_bp.route('/servers', methods=['GET'])
@jwt_required()
def list_servers():
    provider_id = request.args.get('provider_id', type=int)
    servers = CloudProvisioningService.list_servers(provider_id)
    return jsonify({'servers': [s.to_dict() for s in servers]})


@cloud_provisioning_bp.route('/servers/<int:server_id>', methods=['GET'])
@jwt_required()
def get_server(server_id):
    server = CloudProvisioningService.get_server(server_id)
    if not server:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(server.to_dict())


@cloud_provisioning_bp.route('/servers', methods=['POST'])
@jwt_required()
def create_server():
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json()
    if not data or 'name' not in data or 'provider_id' not in data:
        return jsonify({'error': 'name and provider_id required'}), 400
    try:
        server = CloudProvisioningService.create_server(data, user.id)
        return jsonify(server.to_dict()), 201
    except (ValueError, Exception) as e:
        return jsonify({'error': str(e)}), 400


@cloud_provisioning_bp.route('/servers/<int:server_id>', methods=['DELETE'])
@jwt_required()
def destroy_server(server_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    try:
        if not CloudProvisioningService.destroy_server(server_id):
            return jsonify({'error': 'Not found'}), 404
    except AdoptedServerError as e:
        # Refused before contacting the provider: this server is not ours to delete.
        return jsonify({'error': str(e), 'destroyed': False, 'adopted': True}), 409
    except Exception as e:
        # The provider refused the delete, so the server is still running. Say so
        # instead of reporting a destroy that did not happen — and keep it 502
        # rather than 404, which would read as "already gone".
        current_app.logger.error('Cloud destroy refused for server %s: %s', server_id, e)
        return jsonify({
            'error': f'Provider refused the delete: {e}',
            'destroyed': False,
        }), 502
    return jsonify({'message': 'Server destroyed'})


@cloud_provisioning_bp.route('/servers/<int:server_id>/resize', methods=['POST'])
@jwt_required()
def resize_server(server_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json() or {}
    new_size = data.get('size')
    if not new_size:
        return jsonify({'error': 'size required'}), 400
    try:
        server = CloudProvisioningService.resize_server(server_id, new_size)
        if not server:
            return jsonify({'error': 'Not found'}), 404
        return jsonify(server.to_dict())
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


# --- Snapshots ---

@cloud_provisioning_bp.route('/servers/<int:server_id>/snapshots', methods=['GET'])
@jwt_required()
def list_snapshots(server_id):
    snapshots = CloudProvisioningService.get_snapshots(server_id)
    return jsonify({'snapshots': [s.to_dict() for s in snapshots]})


@cloud_provisioning_bp.route('/servers/<int:server_id>/snapshots', methods=['POST'])
@jwt_required()
def create_snapshot(server_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json() or {}
    name = data.get('name', f'snapshot-{server_id}')
    try:
        snapshot = CloudProvisioningService.create_snapshot(server_id, name)
        return jsonify(snapshot.to_dict()), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@cloud_provisioning_bp.route('/snapshots/<int:snapshot_id>', methods=['DELETE'])
@jwt_required()
def delete_snapshot(snapshot_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    if not CloudProvisioningService.delete_snapshot(snapshot_id):
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'message': 'Snapshot deleted'})


# --- Cost ---

@cloud_provisioning_bp.route('/costs', methods=['GET'])
@jwt_required()
def cost_summary():
    return jsonify(CloudProvisioningService.get_cost_summary())
