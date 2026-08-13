"""Ownership ledger + the never-touch-foreign guard for provider DNS writes.

Every record ServerKit creates through a connected provider is recorded in
``managed_dns_records``. Before the panel overwrites or deletes a provider record
it checks this ledger, so it never mutates a record the *user* created themselves
(their pre-existing "Maria & Pedro" records).

Two guard postures:

* **Automatic paths** (WordPress auto-DNS, wildcard setup, email) call with
  ``allow_foreign=False`` — they refuse to touch a record we didn't create.
* **Explicit management** (the /dns Zones page, where the user is deliberately
  editing their own zone) calls with ``allow_foreign=True`` — it adopts the
  existing record and records ownership from then on.
"""
import logging

from app import db
from app.models.managed_dns_record import ManagedDnsRecord

logger = logging.getLogger(__name__)


class DnsOwnershipService:

    # ── ledger reads ─────────────────────────────────────────────────────────
    @staticmethod
    def _row(provider_zone_id, record_type, name):
        return ManagedDnsRecord.query.filter_by(
            provider_zone_id=provider_zone_id, record_type=record_type, name=name).first()

    @staticmethod
    def owns(provider_zone_id, *, provider_record_id=None, record_type=None, name=None):
        """Whether ServerKit created the matching record (by provider record id, or
        by type+name)."""
        q = ManagedDnsRecord.query.filter_by(provider_zone_id=provider_zone_id)
        if provider_record_id:
            q = q.filter_by(provider_record_id=provider_record_id)
        elif record_type and name:
            q = q.filter_by(record_type=record_type, name=name)
        else:
            return False
        return q.first() is not None

    @staticmethod
    def owned_keys(provider_zone_id):
        """``(record_id set, (type, lower-name) set)`` for fast mirror classification."""
        rows = ManagedDnsRecord.query.filter_by(provider_zone_id=provider_zone_id).all()
        ids = {r.provider_record_id for r in rows if r.provider_record_id}
        keys = {(r.record_type, (r.name or '').lower().rstrip('.')) for r in rows}
        return ids, keys

    @staticmethod
    def list_for_zone(provider_zone_id):
        return ManagedDnsRecord.query.filter_by(provider_zone_id=provider_zone_id).all()

    @staticmethod
    def list_all():
        return ManagedDnsRecord.query.order_by(
            ManagedDnsRecord.provider_zone_id, ManagedDnsRecord.name).all()

    # ── ledger writes ────────────────────────────────────────────────────────
    @staticmethod
    def record_write(provider, provider_zone_id, record_type, name, *,
                     provider_record_id=None, content=None, source=None,
                     app_id=None, config_id=None):
        row = DnsOwnershipService._row(provider_zone_id, record_type, name)
        if row is None:
            row = ManagedDnsRecord(provider=provider, provider_zone_id=provider_zone_id,
                                   record_type=record_type, name=name)
            db.session.add(row)
        if provider_record_id:
            row.provider_record_id = provider_record_id
        row.content = content
        if source:
            row.source = source
        if app_id is not None:
            row.app_id = app_id
        if config_id is not None:
            row.dns_provider_config_id = config_id
        db.session.commit()
        return row

    @staticmethod
    def record_delete(provider_zone_id, *, record_type=None, name=None, provider_record_id=None):
        q = ManagedDnsRecord.query.filter_by(provider_zone_id=provider_zone_id)
        if provider_record_id:
            q = q.filter_by(provider_record_id=provider_record_id)
        elif record_type and name:
            q = q.filter_by(record_type=record_type, name=name)
        else:
            return 0
        n = q.delete()
        db.session.commit()
        return n

    # ── guarded provider writes ──────────────────────────────────────────────
    @staticmethod
    def guarded_upsert(client, *, provider, provider_zone_id, spec, source,
                       app_id=None, config_id=None, known_record_id=None,
                       allow_foreign=False):
        """Upsert via the provider client, refusing to overwrite a record ServerKit
        doesn't own (unless ``allow_foreign``), then record ownership on success.

        Returns the client result (``{success, record_id?, error?}``) with
        ``conflict=True`` when a foreign record blocked an automatic write.
        """
        from app.services.dns.cloudflare import parse_caa_value
        from app.services.dns_change_service import DnsChangeService

        record_id = known_record_id
        if record_id is None:
            caa = parse_caa_value(spec.content) if spec.record_type == 'CAA' else None
            existing = client.find_record_id(provider_zone_id, spec.record_type, spec.name, caa=caa)
            if existing:
                if DnsOwnershipService.owns(provider_zone_id, provider_record_id=existing):
                    record_id = existing            # ours — update in place
                elif allow_foreign:
                    record_id = existing            # explicit management — adopt it
                else:
                    logger.warning('Refusing to overwrite foreign DNS record %s %s in zone %s',
                                   spec.record_type, spec.name, provider_zone_id)
                    msg = (f'{spec.record_type} record {spec.name} already exists in this zone '
                           f'and was not created by ServerKit — left untouched.')
                    DnsChangeService.record(
                        provider=provider, provider_zone_id=provider_zone_id, action='create',
                        record_type=spec.record_type, name=spec.name, content=spec.content,
                        source=source, result='conflict', error=msg, config_id=config_id)
                    return {'success': False, 'conflict': True, 'error': msg}

        action = 'update' if record_id else 'create'
        res = client.upsert(provider_zone_id, spec, record_id=record_id)
        if res.get('success'):
            DnsOwnershipService.record_write(
                provider, provider_zone_id, spec.record_type, spec.name,
                provider_record_id=res.get('record_id'), content=spec.content,
                source=source, app_id=app_id, config_id=config_id)
        DnsChangeService.record(
            provider=provider, provider_zone_id=provider_zone_id, action=action,
            record_type=spec.record_type, name=spec.name, content=spec.content,
            provider_record_id=res.get('record_id'), source=source,
            result='ok' if res.get('success') else 'error',
            error=None if res.get('success') else res.get('error'), config_id=config_id)
        return res

    @staticmethod
    def guarded_delete(client, *, provider_zone_id, record_type, name, provider_record_id=None,
                       provider='cloudflare', source=None, config_id=None,
                       allow_foreign=False):
        """Delete a record ServerKit owns; a foreign one only with ``allow_foreign``.

        The default protects AUTOMATION: a deploy tearing down its own records must
        never take out a record the operator wrote by hand. ``allow_foreign=True`` is
        for the opposite case — a human looking at this exact record in their own
        zone and asking for it to go — and mirrors ``guarded_upsert``'s flag of the
        same name. Callers that pass nothing keep the old behaviour exactly.

        Clears our ledger entry and logs the change on success.
        """
        from app.services.dns_change_service import DnsChangeService
        if provider_record_id:
            owned = DnsOwnershipService.owns(provider_zone_id, provider_record_id=provider_record_id)
        else:
            owned = DnsOwnershipService.owns(provider_zone_id, record_type=record_type, name=name)
        if not owned and not allow_foreign:
            # success=True is kept for the automation callers that rely on it: from
            # their side "there is no record of ours here" is a satisfied
            # postcondition, not a failure. `skipped` is how an interactive caller
            # tells the difference — an endpoint must not report this as a delete.
            return {'success': True, 'skipped': True,
                    'message': 'No ServerKit-owned record to delete.'}
        res = client.delete(provider_zone_id, record_id=provider_record_id,
                            record_type=record_type, name=name)
        DnsOwnershipService.record_delete(provider_zone_id, record_type=record_type,
                                          name=name, provider_record_id=provider_record_id)
        DnsChangeService.record(
            provider=provider, provider_zone_id=provider_zone_id, action='delete',
            record_type=record_type, name=name, provider_record_id=provider_record_id,
            source=source, result='ok' if res.get('success') else 'error',
            error=None if res.get('success') else res.get('error'), config_id=config_id)
        return res
