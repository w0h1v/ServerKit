"""Proving tests for DNS ownership + the never-touch-foreign guard (Phase 1).

ServerKit records every provider DNS record it creates in managed_dns_records, and
gates writes so it never overwrites or deletes a record the *user* created (their
own "Maria & Pedro" records). Automatic paths refuse foreign records; the explicit
Zones page adopts them. The mirror classifies a zone's live records accordingly.
"""


class _Resp:
    def __init__(self, js):
        self._js = js

    def json(self):
        return self._js


class FakeClient:
    """Duck-typed CloudflareClient for guard tests (no HTTP)."""
    def __init__(self, existing_id=None, upsert_result=None):
        self.existing_id = existing_id
        self.upsert_result = upsert_result or {'success': True, 'record_id': 'NEW'}
        self.calls = []

    def find_record_id(self, zone_id, record_type, name, caa=None):
        self.calls.append(('find', record_type, name))
        return self.existing_id

    def upsert(self, zone_id, spec, record_id=None):
        self.calls.append(('upsert', spec.record_type, spec.name, record_id))
        return self.upsert_result

    def delete(self, zone_id, record_id=None, record_type=None, name=None):
        self.calls.append(('delete', record_id, record_type, name))
        return {'success': True}


def _verbs(client):
    return [c[0] for c in client.calls]


# ── ledger reads/writes ──────────────────────────────────────────────────────

def test_record_write_and_owns(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com',
                   provider_record_id='R1', content='1.2.3.4', source='zone')
    assert O.owns('z1', provider_record_id='R1')
    assert O.owns('z1', record_type='A', name='www.example.com')
    assert not O.owns('z1', record_type='A', name='maria.example.com')
    assert not O.owns('other-zone', provider_record_id='R1')


def test_record_write_is_upsert(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.models.managed_dns_record import ManagedDnsRecord
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com', provider_record_id='R1', source='zone')
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com', provider_record_id='R1',
                   content='9.9.9.9', source='zone')
    rows = ManagedDnsRecord.query.filter_by(provider_zone_id='z1', name='www.example.com').all()
    assert len(rows) == 1 and rows[0].content == '9.9.9.9'


def test_record_delete(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com', provider_record_id='R1', source='zone')
    assert O.record_delete('z1', provider_record_id='R1') == 1
    assert not O.owns('z1', provider_record_id='R1')


# ── guarded_upsert ───────────────────────────────────────────────────────────

def test_guarded_upsert_creates_and_records_ownership(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.services.dns.base import DnsRecordSpec
    client = FakeClient(existing_id=None, upsert_result={'success': True, 'record_id': 'CF1'})
    res = O.guarded_upsert(client, provider='cloudflare', provider_zone_id='z1',
                           spec=DnsRecordSpec('A', 'www.example.com', '1.2.3.4'),
                           source='zone', allow_foreign=False)
    assert res['success'] and res['record_id'] == 'CF1'
    assert O.owns('z1', provider_record_id='CF1')
    assert O.owns('z1', record_type='A', name='www.example.com')


def test_guarded_upsert_refuses_foreign(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.services.dns.base import DnsRecordSpec
    client = FakeClient(existing_id='FOREIGN')      # exists in CF, not in our ledger
    res = O.guarded_upsert(client, provider='cloudflare', provider_zone_id='z1',
                           spec=DnsRecordSpec('A', 'maria.example.com', '9.9.9.9'),
                           source='auto-dns', allow_foreign=False)
    assert res['success'] is False and res.get('conflict') is True
    assert 'upsert' not in _verbs(client)            # never wrote
    assert not O.owns('z1', record_type='A', name='maria.example.com')


def test_guarded_upsert_adopts_foreign_when_allowed(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.services.dns.base import DnsRecordSpec
    client = FakeClient(existing_id='ADOPT', upsert_result={'success': True, 'record_id': 'ADOPT'})
    res = O.guarded_upsert(client, provider='cloudflare', provider_zone_id='z1',
                           spec=DnsRecordSpec('A', 'www.example.com', '1.1.1.1'),
                           source='zone', allow_foreign=True)
    assert res['success']
    upsert_call = next(c for c in client.calls if c[0] == 'upsert')
    assert upsert_call[3] == 'ADOPT'                 # updated the existing record in place
    assert O.owns('z1', provider_record_id='ADOPT')


def test_guarded_upsert_updates_owned_without_conflict(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.services.dns.base import DnsRecordSpec
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com', provider_record_id='OURS', source='zone')
    client = FakeClient(existing_id='OURS', upsert_result={'success': True, 'record_id': 'OURS'})
    res = O.guarded_upsert(client, provider='cloudflare', provider_zone_id='z1',
                           spec=DnsRecordSpec('A', 'www.example.com', '2.2.2.2'),
                           source='auto-dns', allow_foreign=False)
    assert res['success']                            # owned -> updates even on the strict path


# ── guarded_delete ───────────────────────────────────────────────────────────

def test_guarded_delete_owned_then_foreign(app):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com', provider_record_id='R1', source='zone')

    owned = FakeClient()
    res = O.guarded_delete(owned, provider_zone_id='z1', record_type='A',
                           name='www.example.com', provider_record_id='R1')
    assert res['success'] and 'delete' in _verbs(owned)
    assert not O.owns('z1', provider_record_id='R1')         # ledger cleared

    foreign = FakeClient()
    res2 = O.guarded_delete(foreign, provider_zone_id='z1', record_type='A',
                            name='maria.example.com', provider_record_id='RX')
    assert res2.get('skipped') and 'delete' not in _verbs(foreign)


# ── mirror classification ────────────────────────────────────────────────────

def test_mirror_classifies_owned_vs_external(app, monkeypatch):
    from app import db
    from app.models.dns_zone import DNSZone
    from app.models.email import DNSProviderConfig
    from app.services.dns_provider_service import DNSProviderService
    from app.services.dns_zone_service import DNSZoneService
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.services.dns import cloudflare as cf

    DNSProviderService.add_provider(name='cf', provider='cloudflare', api_key='tok')
    conn = DNSProviderConfig.query.filter_by(name='cf').first()
    zone = DNSZone(domain='example.com', provider='cloudflare',
                   provider_zone_id='Z', dns_provider_config_id=conn.id)
    db.session.add(zone)
    db.session.commit()

    O.record_write('cloudflare', 'Z', 'A', 'www.example.com', provider_record_id='MINE', source='zone')

    monkeypatch.setattr(cf.CloudflareClient, 'list_records', lambda self, zid: {'success': True, 'records': [
        {'id': 'MINE', 'type': 'A', 'name': 'www.example.com', 'content': '1.2.3.4',
         'ttl': 1, 'proxied': False, 'priority': None},
        {'id': 'THEIRS', 'type': 'A', 'name': 'maria.example.com', 'content': '5.6.7.8',
         'ttl': 1, 'proxied': False, 'priority': None},
    ]})

    out = DNSZoneService.list_provider_records(zone)
    assert out['success']
    by_name = {r['name']: r['managed_by'] for r in out['records']}
    assert by_name['www.example.com'] == 'serverkit'
    assert by_name['maria.example.com'] == 'external'
    assert out['counts'] == {'serverkit': 1, 'external': 1}


# ── end-to-end: the auto path refuses to clobber a foreign record ────────────

def test_provider_set_record_refuses_foreign(app, monkeypatch):
    from app.models.email import DNSProviderConfig
    from app.services.dns_provider_service import DNSProviderService
    from app.services.dns import cloudflare as cf

    DNSProviderService.add_provider(name='cf', provider='cloudflare', api_key='tok')
    cfg = DNSProviderConfig.query.filter_by(name='cf').first()

    # Cloudflare already has a record at this name that ServerKit didn't create.
    monkeypatch.setattr(cf.requests, 'get',
                        lambda url, headers=None, timeout=None: _Resp({'result': [{'id': 'FOREIGN'}]}))
    writes = {'n': 0}
    monkeypatch.setattr(cf.requests, 'post',
                        lambda *a, **k: (writes.update(n=writes['n'] + 1) or _Resp({'success': True})))
    monkeypatch.setattr(cf.requests, 'put',
                        lambda *a, **k: (writes.update(n=writes['n'] + 1) or _Resp({'success': True})))

    res = DNSProviderService.set_record(cfg.id, 'Z', 'A', 'maria.example.com', '1.2.3.4')
    assert res['success'] is False and res.get('conflict') is True
    assert writes['n'] == 0                          # never touched Cloudflare


# ── provider-record write path (the drawer's missing half) ───────────────────
#
# The drawer could LIST every record in a live zone and change none of them:
# adding a record went through the local-zone path, and editing an existing one had
# no route at all, so DNS was effectively read-plus-append. These cover the write
# path that closes it, and the ownership rules that decide what it may touch.

def _cf_config(app):
    """A Cloudflare DNS connection for the by-ref write path."""
    from app import db
    from app.models.email import DNSProviderConfig
    cfg = DNSProviderConfig(name='CF', provider='cloudflare', api_key='cf-token')
    db.session.add(cfg)
    db.session.commit()
    return cfg


def test_guarded_delete_still_refuses_a_foreign_record_by_default(app):
    """The default protects automation: a deploy must never remove a hand-written
    record while tearing down its own."""
    from app.services.dns_ownership_service import DnsOwnershipService as O
    client = FakeClient()
    res = O.guarded_delete(client, provider_zone_id='z1', record_type='A',
                           name='maria.example.com')
    assert res['skipped'] is True
    assert res['success'] is True          # satisfied postcondition for automation
    assert 'delete' not in _verbs(client)  # the provider was never called


def test_guarded_delete_removes_a_foreign_record_when_explicitly_allowed(app):
    """allow_foreign is the human case: this record, in my zone, remove it."""
    from app.services.dns_ownership_service import DnsOwnershipService as O
    client = FakeClient()
    res = O.guarded_delete(client, provider_zone_id='z1', record_type='A',
                           name='maria.example.com', provider_record_id='R9',
                           allow_foreign=True)
    assert res.get('skipped') is not True
    assert res['success'] is True
    assert ('delete', 'R9', 'A', 'maria.example.com') in client.calls


def test_write_provider_record_updates_in_place_by_provider_id(app, monkeypatch):
    from app.services.dns_zone_service import DNSZoneService
    from app.services.dns_ownership_service import DnsOwnershipService as O
    cfg = _cf_config(app)
    client = FakeClient(upsert_result={'success': True, 'record_id': 'R1'})
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (client, None)))

    res = DNSZoneService.write_provider_record(
        cfg.id, 'zone-1',
        {'record_type': 'a', 'name': 'www.example.com', 'content': '5.6.7.8', 'ttl': 120},
        provider_record_id='R1')

    assert res['success'] is True
    # known_record_id short-circuits the lookup: no find, straight to the update.
    assert _verbs(client) == ['upsert']
    assert client.calls[0][3] == 'R1'
    # A record ServerKit edits becomes one it tracks.
    assert O.owns('zone-1', provider_record_id='R1')
    # record_type is normalised, so a lowercase 'a' from a form still works.
    assert client.calls[0][1] == 'A'


def test_write_provider_record_conflicts_on_a_foreign_record(app, monkeypatch):
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    client = FakeClient(existing_id='FOREIGN')
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (client, None)))

    res = DNSZoneService.write_provider_record(
        cfg.id, 'zone-1',
        {'record_type': 'A', 'name': 'maria.example.com', 'content': '9.9.9.9'})

    assert res['success'] is False and res['conflict'] is True
    assert 'upsert' not in _verbs(client), 'must not overwrite without being told to'


def test_write_provider_record_takes_over_when_allowed(app, monkeypatch):
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    client = FakeClient(existing_id='FOREIGN',
                        upsert_result={'success': True, 'record_id': 'FOREIGN'})
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (client, None)))

    res = DNSZoneService.write_provider_record(
        cfg.id, 'zone-1',
        {'record_type': 'A', 'name': 'maria.example.com', 'content': '9.9.9.9'},
        allow_foreign=True)

    assert res['success'] is True
    assert client.calls[-1] == ('upsert', 'A', 'maria.example.com', 'FOREIGN')


def test_write_provider_record_validates_its_input(app, monkeypatch):
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (FakeClient(), None)))
    res = DNSZoneService.write_provider_record(cfg.id, 'zone-1', {'record_type': 'A'})
    assert res['success'] is False and 'required' in res['error']

    res = DNSZoneService.write_provider_record(
        cfg.id, '', {'record_type': 'A', 'name': 'x', 'content': 'y'})
    assert res['success'] is False and 'zone id' in res['error']


def test_write_provider_record_refuses_a_non_cloudflare_connection(app):
    from app import db
    from app.models.email import DNSProviderConfig
    from app.services.dns_zone_service import DNSZoneService
    cfg = DNSProviderConfig(name='Route53', provider='route53', api_key='k')
    db.session.add(cfg)
    db.session.commit()
    res = DNSZoneService.write_provider_record(
        cfg.id, 'z', {'record_type': 'A', 'name': 'a.example.com', 'content': '1.1.1.1'})
    assert res['success'] is False
    assert 'only available for Cloudflare' in res['error']


def test_delete_provider_record_needs_an_address(app, monkeypatch):
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (FakeClient(), None)))
    res = DNSZoneService.delete_provider_record(cfg.id, 'zone-1')
    assert res['success'] is False and 'required' in res['error']


# ── endpoints: a skipped delete must never read as a delete ──────────────────

def test_write_endpoint_returns_409_for_a_foreign_record(app, client, auth_headers, monkeypatch):
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    fake = FakeClient(existing_id='FOREIGN')
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (fake, None)))
    resp = client.put('/api/v1/dns/provider-records', headers=auth_headers, json={
        'config_id': cfg.id, 'zone': 'z1',
        'record_type': 'A', 'name': 'maria.example.com', 'content': '9.9.9.9'})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['requires_confirmation'] is True
    assert 'not created by ServerKit' in body['error']


def test_delete_endpoint_reports_a_skip_as_409_not_success(
        app, client, auth_headers, monkeypatch):
    """guarded_delete returns success=True for automation; an operator who clicked
    Delete on a row must not be told it worked when nothing was removed."""
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (FakeClient(), None)))
    resp = client.delete('/api/v1/dns/provider-records', headers=auth_headers, json={
        'config_id': cfg.id, 'zone': 'z1', 'provider_record_id': 'R-foreign'})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['success'] is False
    assert body['requires_confirmation'] is True


def test_delete_endpoint_succeeds_for_an_owned_record(app, client, auth_headers, monkeypatch):
    from app.services.dns_ownership_service import DnsOwnershipService as O
    from app.services.dns_zone_service import DNSZoneService
    cfg = _cf_config(app)
    O.record_write('cloudflare', 'z1', 'A', 'www.example.com',
                   provider_record_id='R1', content='1.2.3.4', source='zone')
    monkeypatch.setattr(DNSZoneService, '_provider_client_by_ref',
                        staticmethod(lambda cid: (FakeClient(), None)))
    resp = client.delete('/api/v1/dns/provider-records', headers=auth_headers, json={
        'config_id': cfg.id, 'zone': 'z1', 'provider_record_id': 'R1'})
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True
    assert not O.owns('z1', provider_record_id='R1'), 'ledger entry must be cleared'
