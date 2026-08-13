from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from app.services.dns_zone_service import DNSZoneService

dns_zones_bp = Blueprint('dns_zones', __name__)


def get_current_user():
    from flask_jwt_extended import get_jwt_identity
    from app.models.user import User
    return User.query.get(get_jwt_identity())


@dns_zones_bp.route('/', methods=['GET'])
@jwt_required()
def list_zones():
    zones = DNSZoneService.list_zones()
    return jsonify({'zones': [z.to_dict() for z in zones]})


@dns_zones_bp.route('/portfolio', methods=['GET'])
@jwt_required()
def dns_portfolio():
    """Every domain visible across connected DNS providers, merged with adopted
    zones — powers the Domains-page portfolio. Read-only: viewing the inventory is
    open to any authenticated user; adopting/managing a zone is admin-gated."""
    return jsonify(DNSZoneService.list_portfolio())


@dns_zones_bp.route('/adopt', methods=['POST'])
@jwt_required()
def adopt_zone():
    """Materialize a local zone row for a provider domain so it can be managed.
    Idempotent — returns the existing zone if already adopted."""
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json() or {}
    try:
        zone = DNSZoneService.adopt_zone(
            data.get('domain'), data.get('dns_provider_config_id'))
        return jsonify(zone.to_dict())
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@dns_zones_bp.route('/registration', methods=['GET'])
@jwt_required()
def domain_registration():
    """Registration expiry / registrar for a domain via RDAP — the lazy fallback
    when a connected provider has no registration data."""
    domain = request.args.get('domain', '')
    return jsonify(DNSZoneService.lookup_domain_registration(domain))


@dns_zones_bp.route('/provider-records', methods=['GET'])
@jwt_required()
def provider_records():
    """Live DNS records for a provider zone addressed by ?config_id= and ?zone=
    (the provider's zone id) — powers the Domains drawer without adopting first."""
    config_id = request.args.get('config_id', type=int)
    zone = request.args.get('zone')
    return jsonify(DNSZoneService.list_provider_records_by_ref(config_id, zone))


@dns_zones_bp.route('/provider-records', methods=['POST', 'PUT'])
@jwt_required()
def write_provider_record():
    """Create or update one record in a live provider zone.

    The write half of GET /provider-records. Without it the Domains drawer could
    list every record in a zone and change none of them, so DNS was read-plus-append:
    adding went through the local-zone path, editing an existing record had no route.

    Body: config_id, zone (provider zone id), record_type, name, content, ttl?,
    priority?, proxied?, provider_record_id? (update in place),
    allow_foreign? (explicitly manage a record ServerKit did not create).
    """
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json() or {}
    result = DNSZoneService.write_provider_record(
        data.get('config_id'), data.get('zone'), data,
        provider_record_id=data.get('provider_record_id'),
        allow_foreign=bool(data.get('allow_foreign')),
    )
    if result.get('conflict'):
        # A record we do not own is in the way and the caller did not say to take it
        # over. 409 with the reason, so the UI can offer that choice instead of
        # reporting a save that silently did nothing.
        return jsonify({**result, 'requires_confirmation': True}), 409
    if not result.get('success'):
        return jsonify(result), 400
    return jsonify(result)


@dns_zones_bp.route('/provider-records', methods=['DELETE'])
@jwt_required()
def delete_provider_record():
    """Delete one record from a live provider zone.

    Query/body: config_id, zone, provider_record_id (or record_type + name),
    allow_foreign? to remove a record ServerKit did not create.
    """
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json(silent=True) or {}
    args = request.args

    def field(key):
        return data.get(key) if data.get(key) is not None else args.get(key)

    allow_foreign = str(field('allow_foreign') or '').lower() in ('1', 'true', 'yes')
    result = DNSZoneService.delete_provider_record(
        field('config_id'), field('zone'),
        provider_record_id=field('provider_record_id'),
        record_type=field('record_type'), name=field('name'),
        allow_foreign=allow_foreign,
    )
    if result.get('skipped'):
        # guarded_delete returns success=True here because for automation "no record
        # of ours" is a satisfied postcondition. For a human who clicked Delete on a
        # specific row it is not — reporting success would claim a deletion that
        # never happened.
        return jsonify({**result, 'success': False, 'requires_confirmation': True,
                        'error': 'That record was not created by ServerKit. '
                                 'Confirm to delete it anyway.'}), 409
    if not result.get('success'):
        return jsonify(result), 400
    return jsonify(result)


@dns_zones_bp.route('/<int:zone_id>', methods=['GET'])
@jwt_required()
def get_zone(zone_id):
    zone = DNSZoneService.get_zone(zone_id)
    if not zone:
        return jsonify({'error': 'Zone not found'}), 404
    return jsonify(zone.to_dict())


@dns_zones_bp.route('/', methods=['POST'])
@jwt_required()
def create_zone():
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json()
    try:
        zone = DNSZoneService.create_zone(data)
        return jsonify(zone.to_dict()), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@dns_zones_bp.route('/<int:zone_id>', methods=['DELETE'])
@jwt_required()
def delete_zone(zone_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    if not DNSZoneService.delete_zone(zone_id):
        return jsonify({'error': 'Zone not found'}), 404
    return jsonify({'message': 'Zone deleted'})


# --- Records ---

@dns_zones_bp.route('/<int:zone_id>/records', methods=['GET'])
@jwt_required()
def get_records(zone_id):
    records = DNSZoneService.get_records(zone_id)
    return jsonify({'records': [r.to_dict() for r in records]})


@dns_zones_bp.route('/<int:zone_id>/records', methods=['POST'])
@jwt_required()
def create_record(zone_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json()
    try:
        record = DNSZoneService.create_record(zone_id, data)
        return jsonify(record.to_dict()), 201
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@dns_zones_bp.route('/records/<int:record_id>', methods=['PUT'])
@jwt_required()
def update_record(record_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json()
    record = DNSZoneService.update_record(record_id, data)
    if not record:
        return jsonify({'error': 'Record not found'}), 404
    return jsonify(record.to_dict())


@dns_zones_bp.route('/records/<int:record_id>', methods=['DELETE'])
@jwt_required()
def delete_record(record_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    if not DNSZoneService.delete_record(record_id):
        return jsonify({'error': 'Record not found'}), 404
    return jsonify({'message': 'Record deleted'})


# --- Tools ---

@dns_zones_bp.route('/presets', methods=['GET'])
@jwt_required()
def get_presets():
    return jsonify({'presets': DNSZoneService.get_presets()})


@dns_zones_bp.route('/<int:zone_id>/apply-preset', methods=['POST'])
@jwt_required()
def apply_preset(zone_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json() or {}
    preset_key = data.get('preset')
    variables = data.get('variables', {})
    try:
        records = DNSZoneService.apply_preset(zone_id, preset_key, variables)
        return jsonify({'records': [r.to_dict() for r in records]})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@dns_zones_bp.route('/propagation/<domain>', methods=['GET'])
@jwt_required()
def check_propagation(domain):
    record_type = request.args.get('type', 'A')
    results = DNSZoneService.check_propagation(domain, record_type)
    return jsonify({'domain': domain, 'record_type': record_type, 'results': results})


@dns_zones_bp.route('/<int:zone_id>/mirror', methods=['GET'])
@jwt_required()
def zone_mirror(zone_id):
    """Live provider records for a zone, each tagged serverkit-owned vs external."""
    zone = DNSZoneService.get_zone(zone_id)
    if not zone:
        return jsonify({'error': 'Zone not found'}), 404
    return jsonify(DNSZoneService.list_provider_records(zone))


@dns_zones_bp.route('/changes', methods=['GET'])
@jwt_required()
def list_dns_changes():
    """The 'Changes to your Cloudflare' activity feed — every record write ServerKit
    sent to a connected provider. Filter by ?config_id=, ?zone= (provider zone id),
    ?result= (ok|error|conflict|skipped)."""
    from app.services.dns_change_service import DnsChangeService
    config_id = request.args.get('config_id', type=int)
    zone = request.args.get('zone')
    result = request.args.get('result')
    limit = request.args.get('limit', default=100, type=int)
    changes = DnsChangeService.list(config_id=config_id, provider_zone_id=zone,
                                    result=result, limit=min(max(limit, 1), 500))
    return jsonify({'changes': [c.to_dict() for c in changes]})


@dns_zones_bp.route('/managed', methods=['GET'])
@jwt_required()
def list_managed_records():
    """Every DNS record ServerKit owns across all provider zones, in one place —
    enriched with the app that triggered each (when applicable)."""
    from app.services.dns_ownership_service import DnsOwnershipService
    from app.models.application import Application
    rows = DnsOwnershipService.list_all()
    app_names = {a.id: a.name for a in Application.query.all()} if rows else {}
    out = []
    for r in rows:
        d = r.to_dict()
        d['app_name'] = app_names.get(r.app_id)
        out.append(d)
    return jsonify({'records': out, 'count': len(out)})


@dns_zones_bp.route('/<int:zone_id>/export', methods=['GET'])
@jwt_required()
def export_zone(zone_id):
    content = DNSZoneService.export_zone(zone_id)
    if content is None:
        return jsonify({'error': 'Zone not found'}), 404
    return jsonify({'zone_file': content})


@dns_zones_bp.route('/<int:zone_id>/import', methods=['POST'])
@jwt_required()
def import_zone(zone_id):
    user = get_current_user()
    if not user or not user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    data = request.get_json() or {}
    content = data.get('zone_file', '')
    try:
        records = DNSZoneService.import_zone(zone_id, content)
        return jsonify({'imported': len(records), 'records': [r.to_dict() for r in records]})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
