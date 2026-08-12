import { useState, useEffect, useCallback } from 'react';
import { useTopbarActions } from '@/hooks/useTopbarActions';
import api from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import PageLoader from '../components/PageLoader';
import ConfirmDialog from '../components/ConfirmDialog';
import EmptyState from '../components/EmptyState';
import Modal from '@/components/Modal';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Cloud, Server } from 'lucide-react';

const CloudProvision = () => {
    const toast = useToast();
    const { user } = useAuth();
    const [providers, setProviders] = useState([]);
    const [servers, setServers] = useState([]);
    const [costs, setCosts] = useState(null);
    const [loading, setLoading] = useState(true);
    const [showCreateProvider, setShowCreateProvider] = useState(false);
    const [showCreateServer, setShowCreateServer] = useState(false);
    const [providerOptions, setProviderOptions] = useState(null);
    const [deleteConfirm, setDeleteConfirm] = useState(null);
    const [importPreview, setImportPreview] = useState(null);
    const [discovering, setDiscovering] = useState(false);
    const [importing, setImporting] = useState(false);

    const [providerForm, setProviderForm] = useState({ name: '', provider_type: 'digitalocean', api_key: '' });
    const [serverForm, setServerForm] = useState({ name: '', provider_id: '', region: '', size: '', image: '', install_agent: true });

    const loadData = useCallback(async () => {
        try {
            const [pData, sData, cData] = await Promise.all([
                api.getCloudProviders(),
                api.getCloudServers(),
                api.getCloudCosts(),
            ]);
            setProviders(pData.providers || []);
            setServers(sData.servers || []);
            setCosts(cData);
        } catch (err) {
            toast.error('Failed to load cloud data');
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { loadData(); }, [loadData]);

    const handleCreateProvider = async () => {
        try {
            await api.createCloudProvider(providerForm);
            toast.success('Provider added');
            setShowCreateProvider(false);
            loadData();
        } catch (err) { toast.error(err.message); }
    };

    const loadProviderOptions = async (type) => {
        try {
            const data = await api.getCloudProviderOptions(type);
            setProviderOptions(data);
        } catch (err) { toast.error(err.message); }
    };

    const handleCreateServer = async () => {
        try {
            await api.createCloudServer(serverForm);
            toast.success('Server provisioning initiated');
            setShowCreateServer(false);
            loadData();
        } catch (err) { toast.error(err.message); }
    };

    const handleDestroy = async (id) => {
        try {
            await api.destroyCloudServer(id);
            toast.success('Server destroyed');
            setDeleteConfirm(null);
            loadData();
        } catch (err) { toast.error(err.message); }
    };

    // Import: preview first (read-only), then adopt only on explicit confirmation.
    // Never adopt as a side effect of opening this page.
    const handleDiscover = async () => {
        const importable = providers.filter(p => p.supports_discovery);
        if (!importable.length) {
            toast.error('None of your providers support importing yet');
            return;
        }
        setDiscovering(true);
        try {
            const results = await Promise.all(
                importable.map(p => api.discoverCloudProvider(p.id)
                    .then(r => ({ provider: p, ...r }))
                    .catch(err => ({ provider: p, error: err.message }))),
            );
            setImportPreview(results);
        } finally {
            setDiscovering(false);
        }
    };

    const handleConfirmImport = async () => {
        setImporting(true);
        try {
            const targets = (importPreview || []).filter(r => !r.error && r.new?.length);
            const results = await Promise.all(
                targets.map(r => api.syncCloudProvider(r.provider.id)
                    .then(res => ({ ok: true, ...res }))
                    .catch(err => ({ ok: false, error: err.message }))),
            );
            const adopted = results.reduce((n, r) => n + (r.adopted?.length || 0), 0);
            const failed = results.filter(r => !r.ok);
            if (adopted) toast.success(`Imported ${adopted} server${adopted === 1 ? '' : 's'}`);
            failed.forEach(r => toast.error(r.error));
            if (!adopted && !failed.length) toast.info('Nothing new to import');
            setImportPreview(null);
            loadData();
        } finally {
            setImporting(false);
        }
    };

    const canImport = providers.some(p => p.supports_discovery);
    const previewNewCount = (importPreview || [])
        .reduce((n, r) => n + (r.new?.length || 0), 0);

    // Registered AFTER the handlers it references: these are `const`, so reading
    // them from a deps array declared above would hit the temporal dead zone and
    // throw on first render.
    useTopbarActions(() =>
        user?.is_admin ? (
            <>
                <Button size="sm" variant="outline" onClick={() => setShowCreateProvider(true)}>Add Provider</Button>
                {canImport && (
                    <Button size="sm" variant="outline" disabled={discovering} onClick={handleDiscover}>
                        {discovering ? 'Checking…' : 'Import existing'}
                    </Button>
                )}
                <Button size="sm" onClick={() => setShowCreateServer(true)}>New Server</Button>
            </>
        ) : null,
        [user?.is_admin, canImport, discovering]
    );

    const providerTypes = {
        digitalocean: 'DigitalOcean', hetzner: 'Hetzner Cloud', vultr: 'Vultr', linode: 'Linode'
    };

    const serverStatusVariant = (status) => {
        if (status === 'active') return 'success';
        if (status === 'error') return 'destructive';
        return 'warning';
    };

    if (loading) return <PageLoader />;

    return (
        <div className="sk-tabgroup__inner cloud-provision-page">
            <Tabs defaultValue="servers">
                <TabsList>
                    <TabsTrigger value="servers">Servers</TabsTrigger>
                    <TabsTrigger value="providers">Providers</TabsTrigger>
                    <TabsTrigger value="costs">Costs</TabsTrigger>
                </TabsList>

                <TabsContent value="servers">
                    <div className="cloud-servers-grid">
                        {servers.map(srv => (
                            <div key={srv.id} className="cloud-server-card card">
                                <div className="cloud-server-card__header">
                                    <h3>{srv.name}</h3>
                                    <Badge variant={serverStatusVariant(srv.status)}>{srv.status}</Badge>
                                    {srv.origin === 'adopted' && (
                                        <Badge variant="outline" title="Imported from the provider — ServerKit did not create it">
                                            Adopted
                                        </Badge>
                                    )}
                                    {srv.sync_state === 'missing_remote' && (
                                        <Badge variant="warning" title="The provider no longer lists this server. It has NOT been destroyed here — confirm at the provider before removing it.">
                                            Missing at provider
                                        </Badge>
                                    )}
                                </div>
                                <div className="cloud-server-card__meta">
                                    <span>{srv.provider_name}</span>
                                    <span>{srv.region}</span>
                                    <span>{srv.size}</span>
                                </div>
                                {srv.ip_address && <div className="text-mono">{srv.ip_address}</div>}
                                <div className="cloud-server-card__cost">
                                    {srv.monthly_cost
                                        ? `$${srv.monthly_cost}/mo`
                                        : <span className="text-muted" title="The provider bills this at the account level, not per server">Billed by provider</span>}
                                </div>
                                <div className="cloud-server-card__actions">
                                    {srv.agent_installed && <Badge variant="success">Agent Installed</Badge>}
                                    {/* can_destroy is false for adopted servers: destroying one would
                                        take out infrastructure ServerKit never provisioned. */}
                                    {user?.is_admin && srv.status === 'active' && srv.can_destroy !== false && (
                                        <Button size="sm" variant="destructive" onClick={() => setDeleteConfirm(srv)}>Destroy</Button>
                                    )}
                                </div>
                            </div>
                        ))}
                        {servers.length === 0 && (
                            <EmptyState
                                size="lg"
                                icon={Server}
                                title="No cloud servers yet"
                                description={user?.is_admin
                                    ? (canImport
                                        // This page only ever listed servers ServerKit created, which
                                        // read as "broken" to anyone whose provider account was
                                        // already full. Say so, and offer the import.
                                        ? 'This page lists servers ServerKit manages. If your provider account already has servers, import them.'
                                        : 'Add a provider, then create a server.')
                                    : 'No servers have been provisioned.'}
                                action={user?.is_admin && (
                                    <>
                                        {canImport && (
                                            <Button variant="outline" disabled={discovering} onClick={handleDiscover}>
                                                {discovering ? 'Checking…' : 'Import existing servers'}
                                            </Button>
                                        )}
                                        <Button onClick={() => setShowCreateServer(true)}>New Server</Button>
                                    </>
                                )}
                            />
                        )}
                    </div>
                </TabsContent>

                <TabsContent value="providers">
                    <div className="providers-list">
                        {providers.map(p => (
                            <div key={p.id} className="provider-row card">
                                <strong>{p.name}</strong>
                                <Badge variant="outline">{providerTypes[p.provider_type] || p.provider_type}</Badge>
                                <span>{p.server_count} servers</span>
                            </div>
                        ))}
                        {providers.length === 0 && (
                            <EmptyState
                                size="lg"
                                icon={Cloud}
                                title="No providers configured"
                                description={user?.is_admin ? 'Add a cloud provider to provision servers.' : 'No providers have been added.'}
                                action={user?.is_admin && <Button variant="outline" onClick={() => setShowCreateProvider(true)}>Add Provider</Button>}
                            />
                        )}
                    </div>
                </TabsContent>

                <TabsContent value="costs">
                    {costs && (
                        <div className="costs-panel card">
                            {(costs.account_charges || []).length > 0 && (
                                <>
                                    <h3>Charges reported by your provider</h3>
                                    <div className="cost-breakdown">
                                        {costs.account_charges.map(a => (
                                            <div key={a.provider_id} className="cost-row">
                                                <span>{a.provider_name}</span>
                                                <span>
                                                    {a.pending_charges != null
                                                        ? `$${Number(a.pending_charges).toFixed(2)} pending`
                                                        : 'pending charges unavailable'}
                                                </span>
                                                <span>
                                                    {a.balance != null ? `balance $${Number(a.balance).toFixed(2)}` : ''}
                                                </span>
                                            </div>
                                        ))}
                                    </div>
                                </>
                            )}

                            <h3>Monthly Cost Summary</h3>
                            <div className="cost-total">${costs.total_monthly}/mo across {costs.server_count} servers</div>
                            {costs.local_total_is_partial && (
                                // An adopted server has no per-instance price, so this sum is not the
                                // bill. Saying so beats showing a confidently wrong total.
                                <p className="text-muted">
                                    Excludes imported servers — your provider bills those at the account
                                    level, so the figure above is not your full spend.
                                </p>
                            )}
                            <div className="cost-breakdown">
                                {Object.entries(costs.by_provider || {}).map(([name, data]) => (
                                    <div key={name} className="cost-row">
                                        <span>{name}</span>
                                        <span>{data.count} servers</span>
                                        <span>${data.cost.toFixed(2)}/mo</span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </TabsContent>
            </Tabs>

            <Modal
                open={showCreateProvider}
                onClose={() => setShowCreateProvider(false)}
                title="Add Cloud Provider"
                footer={(
                    <>
                        <Button variant="outline" onClick={() => setShowCreateProvider(false)}>Cancel</Button>
                        <Button onClick={handleCreateProvider}>Add</Button>
                    </>
                )}
            >
                <div className="form-group"><label>Provider</label><select className="form-select" value={providerForm.provider_type} onChange={e => setProviderForm({...providerForm, provider_type: e.target.value})}>{Object.entries(providerTypes).map(([k,v]) => <option key={k} value={k}>{v}</option>)}</select></div>
                <div className="form-group"><label>Name</label><Input value={providerForm.name} onChange={e => setProviderForm({...providerForm, name: e.target.value})} /></div>
                <div className="form-group"><label>API Key</label><Input type="password" value={providerForm.api_key} onChange={e => setProviderForm({...providerForm, api_key: e.target.value})} /></div>
            </Modal>

            <Modal
                open={showCreateServer}
                onClose={() => setShowCreateServer(false)}
                title="New Cloud Server"
                footer={(
                    <>
                        <Button variant="outline" onClick={() => setShowCreateServer(false)}>Cancel</Button>
                        <Button onClick={handleCreateServer} disabled={!serverForm.name || !serverForm.provider_id}>Create</Button>
                    </>
                )}
            >
                <div className="form-group"><label>Provider</label><select className="form-select" value={serverForm.provider_id} onChange={e => { setServerForm({...serverForm, provider_id: parseInt(e.target.value)}); const p = providers.find(x => x.id === parseInt(e.target.value)); if (p) loadProviderOptions(p.provider_type); }}><option value="">Select provider</option>{providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>
                <div className="form-group"><label>Server Name</label><Input value={serverForm.name} onChange={e => setServerForm({...serverForm, name: e.target.value})} /></div>
                {providerOptions && (
                    <>
                        <div className="form-group"><label>Region</label><select className="form-select" value={serverForm.region} onChange={e => setServerForm({...serverForm, region: e.target.value})}><option value="">Select region</option>{(providerOptions.regions || []).map(r => <option key={r} value={r}>{r}</option>)}</select></div>
                        <div className="form-group"><label>Size</label><select className="form-select" value={serverForm.size} onChange={e => setServerForm({...serverForm, size: e.target.value})}><option value="">Select size</option>{(providerOptions.sizes || []).map(s => <option key={s} value={s}>{s}</option>)}</select></div>
                        <div className="form-group"><label>Image</label><select className="form-select" value={serverForm.image} onChange={e => setServerForm({...serverForm, image: e.target.value})}><option value="">Select image</option>{(providerOptions.images || []).map(i => <option key={i} value={i}>{i}</option>)}</select></div>
                    </>
                )}
                <div className="form-group"><label className="checkbox-label"><input type="checkbox" checked={serverForm.install_agent} onChange={e => setServerForm({...serverForm, install_agent: e.target.checked})} /> Auto-install ServerKit agent</label></div>
            </Modal>

            <Modal
                open={Boolean(importPreview)}
                onClose={() => setImportPreview(null)}
                title="Import existing servers"
                footer={(
                    <>
                        <Button variant="outline" onClick={() => setImportPreview(null)}>Cancel</Button>
                        <Button
                            disabled={importing || previewNewCount === 0}
                            onClick={handleConfirmImport}
                        >
                            {importing
                                ? 'Importing…'
                                : `Import ${previewNewCount} server${previewNewCount === 1 ? '' : 's'}`}
                        </Button>
                    </>
                )}
            >
                <p className="text-muted">
                    Nothing has been imported yet. Imported servers are shown and monitored, but
                    ServerKit will not offer to destroy them — it did not create them.
                </p>
                {(importPreview || []).map(result => (
                    <div key={result.provider.id} className="form-group">
                        <label>{result.provider.name}</label>
                        {result.error && <p className="text-muted">Could not read this provider: {result.error}</p>}
                        {!result.error && (
                            <>
                                {(result.new || []).length === 0 && (
                                    <p className="text-muted">
                                        Nothing new — all {result.remote_total} server(s) are already tracked.
                                    </p>
                                )}
                                {(result.new || []).map(entry => (
                                    <div key={entry.external_id} className="cost-row">
                                        <span>{entry.name}</span>
                                        <span>{entry.region} · {entry.size}</span>
                                        <span>{entry.status}</span>
                                    </div>
                                ))}
                                {(result.missing_remote || []).length > 0 && (
                                    <p className="text-muted">
                                        {result.missing_remote.length} tracked server(s) are no longer at
                                        this provider. They will be flagged, not destroyed.
                                    </p>
                                )}
                            </>
                        )}
                    </div>
                ))}
            </Modal>

            {deleteConfirm && (
                <ConfirmDialog title="Destroy Server" message={`Destroy "${deleteConfirm.name}"? This action is irreversible.`} onConfirm={() => handleDestroy(deleteConfirm.id)} onCancel={() => setDeleteConfirm(null)} variant="danger" />
            )}
        </div>
    );
};

export default CloudProvision;
