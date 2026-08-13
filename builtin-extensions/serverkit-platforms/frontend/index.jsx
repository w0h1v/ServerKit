// App Platforms — connected Railway / Vercel / Supabase projects in one list.
//
// Read-only by design. The panel did not create these projects, so it shows them
// and offers no action that could change or delete one; the only writes are to our
// own connection rows. Same reasoning as an adopted cloud server, applied harder:
// a delete button beside someone's production database is a hazard, not a feature.
//
// Inventory is fetched live on every load and never mirrored locally, so there is
// no local copy to drift and nothing here can claim a project vanished because one
// API call came back short. A connection that fails returns an error row alongside
// the projects that did answer — one dead platform must not blank the page.
import { useCallback, useEffect, useState } from 'react';
import { api, useToast, PageTopbar } from 'serverkit-sdk';

const PLATFORM_LABEL = { railway: 'Railway', vercel: 'Vercel', supabase: 'Supabase' };

function statusTone(status) {
    const s = (status || '').toLowerCase();
    if (['ready', 'active', 'active_healthy', 'running'].some((k) => s.includes(k))) return 'ok';
    if (['error', 'failed', 'unhealthy', 'canceled'].some((k) => s.includes(k))) return 'bad';
    if (['building', 'queued', 'pausing', 'restoring', 'coming_up'].some((k) => s.includes(k))) return 'warn';
    return 'muted';
}

export function PlatformsPage() {
    const toast = useToast();
    const [inventory, setInventory] = useState(null);
    const [catalog, setCatalog] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showConnect, setShowConnect] = useState(false);
    const [form, setForm] = useState({ platform: 'vercel', name: '', api_token: '', scope_id: '' });
    const [saving, setSaving] = useState(false);
    const [expanded, setExpanded] = useState(null);
    const [resources, setResources] = useState({});

    const load = useCallback(async () => {
        try {
            const [inv, cat] = await Promise.all([
                api.getPlatformInventory(),
                api.getPlatformCatalog().catch(() => ({ platforms: [] })),
            ]);
            setInventory(inv);
            setCatalog(cat.platforms || []);
        } catch (e) {
            toast.error(e.message || 'Could not load platform inventory');
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { load(); }, [load]);

    async function handleConnect() {
        setSaving(true);
        try {
            await api.createPlatformConnection(form);
            // The token was proven against the platform before it was stored, so
            // this success means the credential actually works.
            toast.success('Connected — credential verified');
            setShowConnect(false);
            setForm({ platform: 'vercel', name: '', api_token: '', scope_id: '' });
            await load();
        } catch (e) {
            toast.error(e.message || 'Could not connect');
        } finally {
            setSaving(false);
        }
    }

    async function toggleProject(project) {
        const key = `${project.connection_id}:${project.external_id}`;
        if (expanded === key) { setExpanded(null); return; }
        setExpanded(key);
        if (resources[key]) return;
        try {
            const res = await api.getPlatformProjectResources(project.connection_id, project.external_id);
            setResources((prev) => ({ ...prev, [key]: res.resources || [] }));
        } catch (e) {
            setResources((prev) => ({ ...prev, [key]: { error: e.message } }));
        }
    }

    const selected = catalog.find((c) => c.platform === form.platform);
    const projects = inventory?.projects || [];
    const errors = inventory?.errors || [];
    const connections = inventory?.connections || [];

    if (loading) return <div className="platforms-page__loading">Loading platforms…</div>;

    return (
        <div className="sk-tabgroup__inner platforms-page">
            <PageTopbar
                title="App Platforms"
                actions={<button className="btn btn--primary" onClick={() => setShowConnect(true)}>Connect a platform</button>}
            />

            {errors.map((err) => (
                <div key={err.connection_id} className="platforms-page__error card">
                    <strong>{err.connection_name}</strong>
                    <span> ({PLATFORM_LABEL[err.platform] || err.platform})</span>
                    <p>{err.error}</p>
                </div>
            ))}

            {connections.length === 0 && (
                <div className="platforms-page__empty card">
                    <h3>No platforms connected</h3>
                    <p>
                        Connect Railway, Vercel or Supabase to see every project, deployment and
                        database in one list. ServerKit only reads from them — it never changes or
                        deletes anything on the platform.
                    </p>
                    <button className="btn btn--primary" onClick={() => setShowConnect(true)}>
                        Connect a platform
                    </button>
                </div>
            )}

            {projects.length > 0 && (
                <table className="platforms-page__table">
                    <thead>
                        <tr>
                            <th>Project</th><th>Platform</th><th>Status</th>
                            <th>Stack</th><th>Region</th><th>Updated</th>
                        </tr>
                    </thead>
                    <tbody>
                        {projects.map((p) => {
                            const key = `${p.connection_id}:${p.external_id}`;
                            const detail = resources[key];
                            return (
                                <>
                                    <tr key={key} onClick={() => toggleProject(p)} className="platforms-page__row">
                                        <td>
                                            <strong>{p.name}</strong>
                                            {p.url && (
                                                <a href={p.url} target="_blank" rel="noreferrer noopener"
                                                   onClick={(e) => e.stopPropagation()}> ↗</a>
                                            )}
                                        </td>
                                        <td>{PLATFORM_LABEL[p.platform] || p.platform}</td>
                                        <td><span className={`platforms-page__status platforms-page__status--${statusTone(p.status)}`}>{p.status || '—'}</span></td>
                                        <td>{p.framework || '—'}</td>
                                        <td>{p.region || '—'}</td>
                                        <td>{p.updated_at ? new Date(p.updated_at).toLocaleDateString() : '—'}</td>
                                    </tr>
                                    {expanded === key && (
                                        <tr key={`${key}:detail`}>
                                            <td colSpan={6} className="platforms-page__detail">
                                                {detail === undefined && <span>Loading…</span>}
                                                {detail?.error && <span>{detail.error}</span>}
                                                {Array.isArray(detail) && detail.length === 0 && <span>Nothing to show.</span>}
                                                {Array.isArray(detail) && detail.map((r) => (
                                                    <div key={r.external_id} className="platforms-page__resource">
                                                        <span>{r.kind}</span>
                                                        <strong>{r.name}</strong>
                                                        {r.status && <span>{r.status}</span>}
                                                        {r.url && <a href={r.url} target="_blank" rel="noreferrer noopener">open</a>}
                                                    </div>
                                                ))}
                                            </td>
                                        </tr>
                                    )}
                                </>
                            );
                        })}
                    </tbody>
                </table>
            )}

            {showConnect && (
                <div className="platforms-page__modal card">
                    <h3>Connect a platform</h3>
                    <label>
                        Platform
                        <select value={form.platform}
                                onChange={(e) => setForm({ ...form, platform: e.target.value })}>
                            {(catalog.length ? catalog : [{ platform: 'vercel', label: 'Vercel' }])
                                .map((c) => <option key={c.platform} value={c.platform}>{c.label}</option>)}
                        </select>
                    </label>
                    <label>
                        Name (optional)
                        <input value={form.name} placeholder="defaults to the account name"
                               onChange={(e) => setForm({ ...form, name: e.target.value })} />
                    </label>
                    <label>
                        API token
                        <input type="password" value={form.api_token}
                               onChange={(e) => setForm({ ...form, api_token: e.target.value })} />
                        {selected?.token_hint && <small>{selected.token_hint}</small>}
                    </label>
                    {selected?.scope_hint && (
                        <label>
                            Scope
                            <input value={form.scope_id}
                                   onChange={(e) => setForm({ ...form, scope_id: e.target.value })} />
                            <small>{selected.scope_hint}</small>
                        </label>
                    )}
                    <p className="platforms-page__note">
                        The token is checked against the platform before it is saved, so a bad one is
                        refused here rather than failing silently later.
                    </p>
                    <div className="platforms-page__modal-actions">
                        <button className="btn" onClick={() => setShowConnect(false)}>Cancel</button>
                        <button className="btn btn--primary" disabled={saving || !form.api_token}
                                onClick={handleConnect}>
                            {saving ? 'Verifying…' : 'Verify and connect'}
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

export default PlatformsPage;
