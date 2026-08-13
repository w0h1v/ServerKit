// The DNS records section shown inside the Domains drawer — the single DNS surface.
// For a Cloudflare domain it shows the *live* zone records (tagged ServerKit-managed
// vs your own) so you see the real DNS without leaving the drawer; for a
// ServerKit/manual zone it shows the managed records. Admins can add records inline
// (the zone is adopted on demand), turn any A/AAAA record into a token-updatable
// Dynamic DNS host, export the zone, and check propagation. Deeper management links
// out to the Cloudflare ops surface.
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, RefreshCw, Cloud, ShieldCheck, Radio, Download, Activity, Pencil, Trash2 } from 'lucide-react';
import api from '../../services/api';
import { useToast } from '../../contexts/ToastContext';
import { ProviderBrandIcon } from '../icons/ProviderBrands';
import DdnsTokenCallout from './DdnsTokenCallout';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FormField, FormRow } from '../FormField';
import {
    Select, SelectTrigger, SelectContent, SelectItem, SelectValue,
} from '@/components/ui/select';

const RECORD_TYPES = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'SRV', 'CAA', 'NS'];
const PROXYABLE = ['A', 'AAAA', 'CNAME'];
const DYNAMIC_TYPES = ['A', 'AAAA']; // Dynamic DNS only makes sense for IP records.
const EMPTY_FORM = { record_type: 'A', name: '@', content: '', ttl: 3600, priority: '', proxied: false };

const norm = (s) => (s || '').toLowerCase().replace(/\.$/, '');

const normalizeLive = (r) => ({
    id: r.id, type: r.type, name: r.name, content: r.content,
    ttl: r.ttl, proxied: r.proxied, priority: r.priority, source: r.managed_by,
});
const normalizeManaged = (r) => ({
    id: r.id, type: r.record_type, name: r.name, content: r.content,
    ttl: r.ttl, proxied: r.proxied, priority: r.priority, source: 'serverkit',
});

export default function DomainDnsPanel({ domain, isAdmin }) {
    const navigate = useNavigate();
    const toast = useToast();
    const isCloudflare = domain?.provider === 'cloudflare';
    const canLive = isCloudflare && !!domain?.provider_zone_id && !!domain?.config_id;
    const base = norm(domain?.name);

    const [records, setRecords] = useState([]);
    const [state, setState] = useState('loading'); // loading | ready | none | error
    const [error, setError] = useState('');
    const [zoneId, setZoneId] = useState(domain?.zone_id || null); // local DNSZone id, once known
    const [showAdd, setShowAdd] = useState(false);
    const [form, setForm] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);

    // Dynamic DNS hosts for this zone, the just-revealed token, and per-record busy.
    const [hosts, setHosts] = useState([]);
    const [revealedHost, setRevealedHost] = useState(null);
    const [busyKey, setBusyKey] = useState(null);
    const [editing, setEditing] = useState(null);
    // Set when the provider write came back 409: the record was not created by
    // ServerKit, so the operator confirms taking it over rather than us deciding.
    const [confirmForeign, setConfirmForeign] = useState(null);

    // Power tools (moved from the retired DNS Zones page).
    const [exporting, setExporting] = useState(false);
    const [propOpen, setPropOpen] = useState(false);
    const [propLoading, setPropLoading] = useState(false);
    const [propResults, setPropResults] = useState(null);

    // ── FQDN helpers — reconcile live (FQDN) vs managed (relative) record names ──
    const recordFqdn = useCallback((r) => {
        const n = norm(r.name);
        if (!n || n === '@') return base;
        if (n === base || n.endsWith(`.${base}`)) return n;
        return `${n}.${base}`;
    }, [base]);
    const recordRelativeName = useCallback((r) => {
        const fq = recordFqdn(r);
        return fq === base ? '@' : fq.slice(0, -(base.length + 1));
    }, [recordFqdn, base]);

    const hostByFqdn = new Map(hosts.map((h) => [norm(h.hostname), h]));
    const hostFor = (r) => hostByFqdn.get(recordFqdn(r));

    const loadHosts = useCallback(async () => {
        try {
            const d = await api.getDdnsHosts();
            setHosts(d.hosts || []);
        } catch { /* best-effort: hosts just won't be tagged */ }
    }, []);

    const load = useCallback(async () => {
        if (!domain) return;
        setState('loading');
        setError('');
        setZoneId(domain.zone_id || null); // reset so a prior domain's id can't leak in
        loadHosts();
        try {
            if (canLive) {
                const res = await api.getProviderRecords(domain.config_id, domain.provider_zone_id);
                if (!res.success) { setError(res.error || 'Could not load records'); setState('error'); return; }
                setRecords((res.records || []).map(normalizeLive));
                setZoneId(domain.zone_id || null);
            } else if (domain.zone_id) {
                const res = await api.getDNSRecords(domain.zone_id);
                setRecords((res.records || []).map(normalizeManaged));
                setZoneId(domain.zone_id);
            } else {
                // App/manual domain without a provider zone — match a local zone by name.
                const zones = (await api.getDNSZones()).zones || [];
                const zone = zones.find((z) => z.domain === domain.name);
                if (!zone) { setState('none'); return; }
                const res = await api.getDNSRecords(zone.id);
                setRecords((res.records || []).map(normalizeManaged));
                setZoneId(zone.id);
            }
            setState('ready');
        } catch (e) {
            setError(e.message || 'Could not load records');
            setState('error');
        }
    }, [domain, canLive, loadHosts]);

    useEffect(() => {
        setShowAdd(false); setForm(EMPTY_FORM);
        setRevealedHost(null); setPropOpen(false); setPropResults(null);
        load();
    }, [load]);

    // Materialize the local zone row on demand (writes / Cloudflare ops need it).
    async function ensureZone() {
        if (zoneId) return zoneId;
        const zone = await api.adoptDnsZone(domain.name, domain.config_id);
        setZoneId(zone.id);
        return zone.id;
    }

    async function handleAdd() {
        setSaving(true);
        try {
            const zid = await ensureZone();
            await api.createDNSRecord(zid, {
                record_type: form.record_type,
                name: form.name || '@',
                content: form.content,
                ttl: Number(form.ttl) || 3600,
                priority: form.priority !== '' && form.priority != null ? Number(form.priority) : null,
                proxied: isCloudflare && PROXYABLE.includes(form.record_type) ? !!form.proxied : false,
            });
            toast.success('Record added');
            setShowAdd(false);
            setForm(EMPTY_FORM);
            await load();
        } catch (e) {
            toast.error(e.message || 'Failed to add record');
        } finally {
            setSaving(false);
        }
    }

    // ── Edit / delete a record that lives in the provider zone ────────────────
    // Until now this panel could list every record and change none of them: adding
    // went through the local-zone path, and editing an existing record had no route
    // at all. These call the provider write path directly, addressed by the
    // provider's own record id.
    //
    // A record ServerKit did not create comes back 409 with requires_confirmation
    // rather than being overwritten silently. The operator is looking at their own
    // zone, so taking it over is a fair thing to offer — but only once they say so.
    function startEdit(r) {
        setEditing({
            provider_record_id: r.id,
            record_type: r.type,
            name: r.name,
            content: r.content,
            ttl: r.ttl === 1 ? 1 : (r.ttl || 3600),
            priority: r.priority ?? '',
            proxied: !!r.proxied,
            was_external: r.source !== 'serverkit',
        });
    }

    async function saveEdit({ allowForeign = false } = {}) {
        if (!editing) return;
        setSaving(true);
        try {
            const payload = {
                provider_record_id: editing.provider_record_id,
                record_type: editing.record_type,
                name: editing.name,
                content: editing.content,
                ttl: Number(editing.ttl) || 3600,
                priority: editing.priority === '' ? null : Number(editing.priority),
                proxied: isCloudflare && PROXYABLE.includes(editing.record_type)
                    ? !!editing.proxied : false,
            };
            await api.writeProviderRecord(domain.config_id, domain.provider_zone_id,
                                         payload, { allowForeign });
            toast.success('Record updated');
            setEditing(null);
            setConfirmForeign(null);
            await load();
        } catch (e) {
            if (e.status === 409 || /not created by ServerKit|already exists/i.test(e.message || '')) {
                setConfirmForeign({ kind: 'edit', label: `${editing.record_type} ${editing.name}` });
            } else {
                toast.error(e.message || 'Failed to update record');
            }
        } finally {
            setSaving(false);
        }
    }

    async function deleteRecord(r, { allowForeign = false } = {}) {
        setBusyKey(r.id);
        try {
            await api.deleteProviderRecord(domain.config_id, domain.provider_zone_id,
                                          r.id, { allowForeign });
            toast.success('Record deleted');
            setConfirmForeign(null);
            await load();
        } catch (e) {
            if (e.status === 409 || /not created by ServerKit/i.test(e.message || '')) {
                setConfirmForeign({ kind: 'delete', record: r,
                                    label: `${r.type} ${r.name}` });
            } else {
                toast.error(e.message || 'Failed to delete record');
            }
        } finally {
            setBusyKey(null);
        }
    }

    // ── Dynamic DNS — turn a record into a token-updatable host ───────────────
    async function handleMakeDynamic(r) {
        setBusyKey(recordFqdn(r));
        try {
            const zid = await ensureZone();
            const host = await api.createDdnsHost({ zone_id: zid, record_name: recordRelativeName(r) });
            setRevealedHost(host); // includes the one-time token
            toast.success('Dynamic DNS enabled');
            await loadHosts();
        } catch (e) {
            toast.error(e.message || 'Failed to enable Dynamic DNS');
        } finally {
            setBusyKey(null);
        }
    }

    async function handleRegenerate(host) {
        try {
            const updated = await api.regenerateDdnsToken(host.id);
            setRevealedHost(updated);
            toast.success('Token regenerated');
            await loadHosts();
        } catch (e) {
            toast.error(e.message || 'Failed to regenerate token');
        }
    }

    async function handleStopDynamic(host) {
        if (!confirm(`Disable Dynamic DNS for ${host.hostname}? Its update token will stop working.`)) return;
        try {
            await api.deleteDdnsHost(host.id);
            if (revealedHost?.id === host.id) setRevealedHost(null);
            toast.success('Dynamic DNS disabled');
            await loadHosts();
        } catch (e) {
            toast.error(e.message || 'Failed to disable Dynamic DNS');
        }
    }

    // ── Power tools ───────────────────────────────────────────────────────────
    async function handleExport() {
        setExporting(true);
        try {
            const zid = await ensureZone();
            const data = await api.exportDNSZone(zid);
            const blob = new Blob([data.zone_file || ''], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${domain.name}.txt`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e) {
            toast.error(e.message || 'Export failed');
        } finally {
            setExporting(false);
        }
    }

    async function handleCheckPropagation() {
        if (propOpen) { setPropOpen(false); return; }
        setPropOpen(true);
        setPropLoading(true);
        try {
            const d = await api.checkDNSPropagation(domain.name);
            setPropResults(d.results || []);
        } catch (e) {
            toast.error(e.message || 'Propagation check failed');
            setPropResults([]);
        } finally {
            setPropLoading(false);
        }
    }

    async function openCloudflareOps() {
        try {
            const zid = await ensureZone();
            navigate(`/cloudflare/zones/${zid}`);
        } catch (e) {
            toast.error(e.message || 'Could not open Cloudflare');
        }
    }

    // A proxied A/AAAA/CNAME means Cloudflare terminates TLS at its edge — i.e. the
    // site is served over HTTPS with no separate certificate to manage here.
    const hasProxiedSSL = isCloudflare && records.some((r) => r.proxied && PROXYABLE.includes(r.type));
    const showActions = isAdmin || records.some((r) => !!hostFor(r));
    const canExport = state === 'ready' && records.length > 0 && (isAdmin || !!zoneId);

    return (
        <div className="ddp">
            <div className="ddp__head">
                <h3 className="ddp__title">
                    DNS records{state === 'ready' && <span className="ddp__count"> · {records.length}</span>}
                </h3>
                <div className="ddp__head-actions">
                    <Button variant="ghost" size="sm" onClick={load} title="Refresh records">
                        <RefreshCw size={14} />
                    </Button>
                    {isAdmin && (state === 'ready' || state === 'none') && (
                        <Button size="sm" onClick={() => setShowAdd((v) => !v)}>
                            <Plus size={14} /> Add record
                        </Button>
                    )}
                </div>
            </div>

            {revealedHost && (
                <DdnsTokenCallout host={revealedHost} onDismiss={() => setRevealedHost(null)} />
            )}

            {canLive && (
                <p className="ddp__hint">
                    Live from Cloudflare — records ServerKit manages are tagged; the rest are your own and shown read-only.
                </p>
            )}

            {state === 'ready' && hasProxiedSSL && (
                <p className="ddp__ssl">
                    <ShieldCheck size={13} /> HTTPS is served by Cloudflare on proxied records — no separate certificate needed.
                </p>
            )}

            {showAdd && isAdmin && (
                <div className="ddp__form">
                    <FormRow>
                        <FormField label="Type">
                            <Select value={form.record_type} onValueChange={(v) => setForm({ ...form, record_type: v })}>
                                <SelectTrigger><SelectValue /></SelectTrigger>
                                <SelectContent>{RECORD_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                            </Select>
                        </FormField>
                        <FormField label="Name">
                            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="@ or subdomain" />
                        </FormField>
                    </FormRow>
                    <FormField label="Content">
                        <Input value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} placeholder="IP address or target" />
                    </FormField>
                    <FormRow>
                        <FormField label="TTL">
                            <Input type="number" value={form.ttl} onChange={(e) => setForm({ ...form, ttl: e.target.value })} />
                        </FormField>
                        {(form.record_type === 'MX' || form.record_type === 'SRV') && (
                            <FormField label="Priority">
                                <Input type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })} />
                            </FormField>
                        )}
                    </FormRow>
                    {isCloudflare && PROXYABLE.includes(form.record_type) && (
                        <label className="ddp__proxy-toggle">
                            <input type="checkbox" checked={!!form.proxied} onChange={(e) => setForm({ ...form, proxied: e.target.checked })} />
                            Proxy through Cloudflare (orange cloud)
                        </label>
                    )}
                    <div className="ddp__form-actions">
                        <Button variant="outline" size="sm" onClick={() => setShowAdd(false)}>Cancel</Button>
                        <Button size="sm" disabled={!form.content || saving} onClick={handleAdd}>
                            {saving ? 'Adding…' : 'Add record'}
                        </Button>
                    </div>
                </div>
            )}

            {state === 'loading' && <p className="ddp__msg">Loading records…</p>}
            {state === 'error' && <p className="ddp__msg ddp__msg--error">{error}</p>}
            {state === 'none' && <p className="ddp__msg">This domain isn&apos;t set up for DNS in ServerKit yet.</p>}

            {state === 'ready' && (
                records.length === 0 ? (
                    <p className="ddp__msg">No DNS records yet.</p>
                ) : (
                    <div className="ddp__table-wrap">
                        <table className="sk-dtable ddp__table">
                            <thead>
                                <tr>
                                    <th className="ddp__c-type">Type</th>
                                    <th className="ddp__c-name">Name</th>
                                    <th>Content</th>
                                    <th className="ddp__c-ttl">TTL</th>
                                    {isCloudflare && <th className="ddp__c-proxy">Proxy</th>}
                                    {canLive && <th className="ddp__c-src">Source</th>}
                                    {showActions && <th className="ddp__c-act" />}
                                </tr>
                            </thead>
                            <tbody>
                                {records.map((r) => {
                                    const host = hostFor(r);
                                    const dynamicable = DYNAMIC_TYPES.includes(r.type);
                                    return (
                                        <tr key={r.id}>
                                            <td><span className={`dns-rtype dns-rtype--${(r.type || '').toLowerCase()}`}>{r.type}</span></td>
                                            <td className="sk-cell-mono ddp__c-name" title={r.name}>{r.name}</td>
                                            <td className="sk-cell-mono ddp__content" title={r.content}>{r.priority ? `${r.priority} ` : ''}{r.content}</td>
                                            <td className="sk-cell-mono">{r.ttl === 1 ? 'Auto' : r.ttl}</td>
                                            {isCloudflare && (
                                                <td>{r.proxied
                                                    ? <span className="ddp__proxy ddp__proxy--on"><Cloud size={12} /> Proxied</span>
                                                    : <span className="ddp__proxy">DNS only</span>}</td>
                                            )}
                                            {canLive && (
                                                <td>{r.source === 'serverkit'
                                                    ? <span className="ddp__src ddp__src--sk">ServerKit</span>
                                                    : <span className="ddp__src">External</span>}</td>
                                            )}
                                            {showActions && (
                                                <td className="ddp__c-act">
                                                    {host ? (
                                                        <span className="ddp__dynwrap">
                                                            <span className="ddp__dyn" title={host.last_ip ? `Last IP ${host.last_ip}` : 'No update yet'}>
                                                                <Radio size={11} /> Dynamic
                                                            </span>
                                                            {isAdmin && (
                                                                <>
                                                                    <Button variant="ghost" size="sm" className="ddp__iconbtn" title="Regenerate token" onClick={() => handleRegenerate(host)}>
                                                                        <RefreshCw size={13} />
                                                                    </Button>
                                                                    <Button variant="ghost" size="sm" className="ddp__stopbtn" title="Disable Dynamic DNS" onClick={() => handleStopDynamic(host)}>
                                                                        Stop
                                                                    </Button>
                                                                </>
                                                            )}
                                                        </span>
                                                    ) : (
                                                        isAdmin && dynamicable && (
                                                            <Button
                                                                variant="outline"
                                                                size="sm"
                                                                onClick={() => handleMakeDynamic(r)}
                                                                disabled={busyKey === recordFqdn(r)}
                                                            >
                                                                <Radio size={13} /> {busyKey === recordFqdn(r) ? 'Enabling…' : 'Make dynamic'}
                                                            </Button>
                                                        )
                                                    )}
                                                    {/* Edit/Delete address the record by the PROVIDER's record id, so
                                                        they work on any record in the zone — not just ones ServerKit
                                                        created. Taking over a record it did not create still needs an
                                                        explicit confirmation. */}
                                                    {isAdmin && canLive && r.id && (
                                                        <>
                                                            <Button variant="ghost" size="sm" className="ddp__iconbtn"
                                                                    title="Edit this record"
                                                                    onClick={() => startEdit(r)}>
                                                                <Pencil size={13} />
                                                            </Button>
                                                            <Button variant="ghost" size="sm" className="ddp__stopbtn"
                                                                    title="Delete this record"
                                                                    disabled={busyKey === r.id}
                                                                    onClick={() => deleteRecord(r)}>
                                                                <Trash2 size={13} />
                                                            </Button>
                                                        </>
                                                    )}
                                                </td>
                                            )}
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )
            )}

            {propOpen && (
                <div className="ddp__prop">
                    {propLoading && <p className="ddp__msg">Checking propagation…</p>}
                    {!propLoading && (propResults?.length ? (
                        propResults.map((r, i) => (
                            <div key={i} className="ddp__prop-row">
                                <span className={`status-dot status-dot--${r.propagated ? 'success' : 'danger'}`} />
                                <strong>{r.nameserver}</strong>
                                <span className="ddp__prop-ip">({r.ip})</span>
                                <span className="ddp__prop-res">{r.result?.join(', ') || 'No result'}</span>
                            </div>
                        ))
                    ) : (
                        <p className="ddp__msg">No propagation data.</p>
                    ))}
                </div>
            )}

            {editing && isAdmin && (
                <div className="ddp__edit">
                    <h4>Edit {editing.record_type} {editing.name}</h4>
                    {editing.was_external && (
                        // Say this before the save, not after the 409 — the operator
                        // should know they are taking over a record they wrote by hand.
                        <p className="ddp__msg">
                            This record was not created by ServerKit. Saving will take over
                            managing it.
                        </p>
                    )}
                    <div className="ddp__editgrid">
                        <label>Name
                            <Input value={editing.name}
                                   onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                        </label>
                        <label>Content
                            <Input value={editing.content}
                                   onChange={(e) => setEditing({ ...editing, content: e.target.value })} />
                        </label>
                        <label>TTL
                            <Input value={editing.ttl}
                                   onChange={(e) => setEditing({ ...editing, ttl: e.target.value })} />
                        </label>
                        {['MX', 'SRV'].includes(editing.record_type) && (
                            <label>Priority
                                <Input value={editing.priority}
                                       onChange={(e) => setEditing({ ...editing, priority: e.target.value })} />
                            </label>
                        )}
                        {isCloudflare && PROXYABLE.includes(editing.record_type) && (
                            <label className="ddp__check">
                                <input type="checkbox" checked={!!editing.proxied}
                                       onChange={(e) => setEditing({ ...editing, proxied: e.target.checked })} />
                                Proxied
                            </label>
                        )}
                    </div>
                    <div className="ddp__editact">
                        <Button variant="outline" size="sm" onClick={() => setEditing(null)}>Cancel</Button>
                        <Button size="sm" disabled={saving || !editing.content}
                                onClick={() => saveEdit({ allowForeign: editing.was_external })}>
                            {saving ? 'Saving…' : 'Save record'}
                        </Button>
                    </div>
                </div>
            )}

            {confirmForeign && (
                <div className="ddp__edit">
                    <h4>
                        {confirmForeign.kind === 'delete' ? 'Delete' : 'Take over'}{' '}
                        {confirmForeign.label}?
                    </h4>
                    <p className="ddp__msg">
                        ServerKit did not create this record, so it stopped rather than
                        change something you may rely on.{' '}
                        {confirmForeign.kind === 'delete'
                            ? 'Deleting it affects live DNS immediately.'
                            : 'Saving will overwrite it and start managing it here.'}
                    </p>
                    <div className="ddp__editact">
                        <Button variant="outline" size="sm" onClick={() => setConfirmForeign(null)}>
                            Leave it alone
                        </Button>
                        <Button variant="destructive" size="sm"
                                onClick={() => (confirmForeign.kind === 'delete'
                                    ? deleteRecord(confirmForeign.record, { allowForeign: true })
                                    : saveEdit({ allowForeign: true }))}>
                            {confirmForeign.kind === 'delete' ? 'Delete anyway' : 'Overwrite it'}
                        </Button>
                    </div>
                </div>
            )}

            <div className="ddp__foot">
                {canExport && (
                    <Button variant="outline" size="sm" onClick={handleExport} disabled={exporting}>
                        <Download size={14} /> {exporting ? 'Exporting…' : 'Export'}
                    </Button>
                )}
                <Button variant="outline" size="sm" onClick={handleCheckPropagation}>
                    <Activity size={14} /> {propOpen ? 'Hide propagation' : 'Check propagation'}
                </Button>
                {isCloudflare && (
                    <Button variant="outline" size="sm" onClick={openCloudflareOps}>
                        <ProviderBrandIcon provider="cloudflare" size={14} /> Open in Cloudflare
                    </Button>
                )}
            </div>
        </div>
    );
}
