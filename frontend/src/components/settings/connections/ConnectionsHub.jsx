// Connections hub — the Settings → Connections tab. Presents every external
// integration ServerKit can bridge to, grouped by category, over a single
// surface. It composes the backends that already exist instead of inventing new
// ones, keyed by each provider's `kind` (see providerCatalog.js):
//   - source    → /source-connections   (GitHub, GitLab OAuth)
//   - cloud     → /cloud                 (DigitalOcean, Hetzner, Vultr, Linode)
//   - dns       → /email/dns-providers   (Cloudflare, Route 53, DigitalOcean, GoDaddy)
//   - registrar → /registrars            (GoDaddy domain portfolio + expiry)
//   - storage   → /backups/storage       (S3-compatible, Backblaze B2)
// Each connected provider shows its status, access scope, and a cross-link to the
// in-app page it powers (Servers, Domains, Backups, New Service).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import api from '../../../services/api';
import useSettingFocus from '../../../hooks/useSettingFocus';
import { useAuth } from '../../../contexts/AuthContext';
import { useToast } from '../../../contexts/ToastContext';
import { useHasPlugin } from '../../../plugins/contributions';
import {
    CONNECTION_CATEGORIES, CONNECTION_PROVIDERS, deriveScope, dedupeScopes,
} from './providerCatalog';
import ProviderCard from './ProviderCard';
import ConnectProviderModal from './ConnectProviderModal';

// The settings-index deep-link id each category section is landable from. The
// cards are rendered by ProviderCard (presentational, no ref), so the flash
// lands on the section container. The source section carries `connections-github`
// (its headline); `connections-gitlab` still deep-links to the tab correctly.
// The `chat` category has no settings-index entry, so it is left unregistered.
const CATEGORY_FOCUS_ID = {
    source: 'connections-github',
    infra: 'connections-cloud-provider',
    registry: 'connections-container-registry',
    dns: 'connections-dns-provider',
    registrar: 'connections-registrar',
    email: 'connections-email-relay',
    storage: 'connections-storage',
};

export default function ConnectionsHub() {
    const register = useSettingFocus();
    const { isAdmin } = useAuth();
    const toast = useToast();
    // A catalog entry whose API lives in an extension (`requiresExtension`) is
    // hidden until that extension is installed — otherwise the card looks
    // available and its Save lands on a route that does not exist.
    const hasEmailExtension = useHasPlugin('serverkit-email');
    const isProviderAvailable = useCallback((provider) => {
        if (provider.requiresExtension === 'serverkit-email') return hasEmailExtension;
        return !provider.requiresExtension;
    }, [hasEmailExtension]);

    const [sourceStatus, setSourceStatus] = useState({ github: null, gitlab: null, bitbucket: null });
    const [sourceConfig, setSourceConfig] = useState({ github: null, gitlab: null, bitbucket: null });
    const [dnsProviders, setDnsProviders] = useState([]);
    const [cloudProviders, setCloudProviders] = useState([]);
    const [storageConfig, setStorageConfig] = useState(null);
    const [relayConfig, setRelayConfig] = useState(null);
    const [registrarConnections, setRegistrarConnections] = useState([]);
    const [registrarDomains, setRegistrarDomains] = useState([]);
    const [containerRegistries, setContainerRegistries] = useState([]);
    const [allConnections, setAllConnections] = useState([]);
    const [loading, setLoading] = useState(true);
    const [modalProvider, setModalProvider] = useState(null);
    const [modalOpen, setModalOpen] = useState(false);

    const loadData = useCallback(async () => {
        try {
            const [ghStatus, glStatus, bbStatus, dns, cloudP, storage, relay, regConns, regDomains, registries, allConns] = await Promise.all([
                api.getGithubSourceStatus().catch(() => null),
                api.getGitlabSourceStatus().catch(() => null),
                api.getBitbucketSourceStatus().catch(() => null),
                api.getEmailDNSProviders().then((d) => d.providers || []).catch(() => []),
                api.getCloudProviders().then((d) => d.providers || []).catch(() => []),
                api.getStorageConfig().catch(() => null),
                api.getEmailRelay().catch(() => null),
                api.getRegistrarConnections().then((d) => d.connections || []).catch(() => []),
                api.getRegistrarDomains().then((d) => d.domains || []).catch(() => []),
                api.getContainerRegistries().then((d) => d.registries || []).catch(() => []),
                api.getAllConnections().then((d) => d.connections || []).catch(() => []),
            ]);
            setSourceStatus({ github: ghStatus, gitlab: glStatus, bitbucket: bbStatus });
            setDnsProviders(dns);
            setCloudProviders(cloudP);
            setStorageConfig(storage);
            setRelayConfig(relay);
            setRegistrarConnections(regConns);
            setRegistrarDomains(regDomains);
            setContainerRegistries(registries);
            setAllConnections(allConns);
            if (isAdmin) {
                const [ghCfg, glCfg, bbCfg] = await Promise.all([
                    api.getGithubSourceConfig().catch(() => null),
                    api.getGitlabSourceConfig().catch(() => null),
                    api.getBitbucketSourceConfig().catch(() => null),
                ]);
                setSourceConfig({
                    github: ghCfg?.config || { client_id: '', client_secret: '' },
                    gitlab: glCfg?.config || { client_id: '', client_secret: '' },
                    bitbucket: bbCfg?.config || { client_id: '', client_secret: '' },
                });
            }
        } finally {
            setLoading(false);
        }
    }, [isAdmin]);

    useEffect(() => { loadData(); }, [loadData]);

    // ── Source (GitHub / GitLab) ──
    const onConnectSource = useCallback(async (provider) => {
        try {
            const redirectUri = `${window.location.origin}/connections/callback/${provider.provider}`;
            sessionStorage.setItem('sourceConnectionReturnTo', '/settings/connections');
            const { auth_url } = await api.startSourceConnection(provider.provider, redirectUri);
            window.location.href = auth_url;
        } catch (err) {
            toast.error(err.message || `Failed to start ${provider.name} connection`);
        }
    }, [toast]);

    const onDisconnectSource = useCallback(async (provider) => {
        try {
            await api.disconnectSourceConnection(provider.provider);
            toast.success(`${provider.name} disconnected`);
            await loadData();
            setModalOpen(false);
        } catch (err) {
            toast.error(err.message || 'Failed to disconnect');
        }
    }, [toast, loadData]);

    // One-click GitHub App setup: fetch the manifest, then POST it to github.com
    // via an auto-submitted form (GitHub only accepts the manifest as a form
    // field). GitHub redirects back to the callback which finishes the setup.
    const onSetupGithubApp = useCallback(async () => {
        try {
            const baseUrl = window.location.origin;
            const redirectUri = `${baseUrl}/connections/github-app/callback`;
            const { manifest, state, post_url } = await api.getGithubAppManifest(baseUrl, redirectUri);
            sessionStorage.setItem('sourceConnectionReturnTo', '/settings/connections');
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = `${post_url}?state=${encodeURIComponent(state)}`;
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'manifest';
            input.value = JSON.stringify(manifest);
            form.appendChild(input);
            document.body.appendChild(form);
            form.submit();
        } catch (err) {
            toast.error(err.message || 'Failed to start GitHub App setup');
        }
    }, [toast]);

    const onSaveSourceConfig = useCallback(async (provider, config) => {
        try {
            if (provider.provider === 'gitlab') await api.updateGitlabSourceConfig(config);
            else if (provider.provider === 'bitbucket') await api.updateBitbucketSourceConfig(config);
            else await api.updateGithubSourceConfig(config);
            toast.success(`${provider.name} OAuth app saved`);
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to save OAuth app');
            return false;
        }
    }, [toast, loadData]);

    // ── DNS ──
    const onAddDns = useCallback(async (payload) => {
        try {
            const res = await api.addEmailDNSProvider(payload);
            if (res && res.success === false) throw new Error(res.error || 'Failed to add connection');
            toast.success(`${payload.name} connected`);
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to add connection');
            return false;
        }
    }, [toast, loadData]);

    const onRemoveDns = useCallback(async (record) => {
        if (!window.confirm(`Remove the connection "${record.name}"?`)) return false;
        try {
            await api.deleteEmailDNSProvider(record.id);
            toast.success(`${record.name} removed`);
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to remove connection');
            return false;
        }
    }, [toast, loadData]);

    const onTestDns = useCallback(async (id) => {
        try {
            const res = await api.testEmailDNSProvider(id);
            if (res && res.success) toast.success(res.message || 'Connection works');
            else toast.error((res && res.error) || 'Connection test failed');
            return res;
        } catch (err) {
            toast.error(err.message || 'Connection test failed');
            return null;
        }
    }, [toast]);

    // ── Cloud (server provisioning) ──
    const onAddCloud = useCallback(async (payload) => {
        try {
            await api.createCloudProvider(payload); // { provider_type, name, api_key }
            toast.success(`${payload.name || 'Provider'} connected`);
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to connect provider');
            return false;
        }
    }, [toast, loadData]);

    const onRemoveCloud = useCallback(async (id) => {
        if (!window.confirm('Disconnect this cloud account? Existing servers are not affected.')) return false;
        try {
            await api.deleteCloudProvider(id);
            toast.success('Disconnected');
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to disconnect');
            return false;
        }
    }, [toast, loadData]);

    // ── Storage ──
    const onSaveStorage = useCallback(async (config) => {
        try {
            const res = await api.updateStorageConfig(config);
            if (res && res.success === false) throw new Error(res.error || 'Failed to save storage');
            toast.success('Storage saved');
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to save storage');
            return false;
        }
    }, [toast, loadData]);

    const onTestStorage = useCallback(async (config) => {
        try {
            const res = await api.testStorageConnection(config);
            if (res && res.success) toast.success(res.message || 'Connection works');
            else toast.error((res && res.error) || 'Connection test failed');
            return res;
        } catch (err) {
            toast.error(err.message || 'Connection test failed');
            return null;
        }
    }, [toast]);

    // ── Email relay ──
    const onSaveRelay = useCallback(async (payload) => {
        try {
            const res = await api.updateEmailRelay(payload);
            toast.success(res?.apply?.note || 'Relay saved');
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to save relay');
            return false;
        }
    }, [toast, loadData]);

    const onTestRelay = useCallback(async (payload) => {
        try {
            const res = await api.testEmailRelay(payload);
            if (res && res.success) toast.success(res.message || 'Connection works');
            else toast.error((res && res.error) || 'Connection test failed');
            return res;
        } catch (err) {
            toast.error(err.message || 'Connection test failed');
            return null;
        }
    }, [toast]);

    const onDisableRelay = useCallback(async () => {
        try {
            await api.disableEmailRelay();
            toast.success('Relay disabled');
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to disable relay');
            return false;
        }
    }, [toast, loadData]);

    // ── Registrar ──
    const onAddRegistrar = useCallback(async (payload) => {
        try {
            await api.addRegistrarConnection(payload);
            toast.success(`${payload.name || 'Registrar'} connected`);
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to connect registrar');
            return false;
        }
    }, [toast, loadData]);

    const onRemoveRegistrar = useCallback(async (id) => {
        if (!window.confirm('Disconnect this registrar?')) return false;
        try {
            await api.deleteRegistrarConnection(id);
            toast.success('Registrar disconnected');
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to disconnect');
            return false;
        }
    }, [toast, loadData]);

    const onTestRegistrar = useCallback(async (id) => {
        try {
            const res = await api.testRegistrarConnection(id);
            if (res && res.success) toast.success(res.message || 'Connection works');
            else toast.error((res && res.error) || 'Connection test failed');
            return res;
        } catch (err) {
            toast.error(err.message || 'Connection test failed');
            return null;
        }
    }, [toast]);

    // ── Container registries ──
    const onAddRegistry = useCallback(async (payload) => {
        try {
            await api.addContainerRegistry(payload);
            toast.success(`${payload.name || 'Registry'} connected`);
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to add registry');
            return false;
        }
    }, [toast, loadData]);

    const onRemoveRegistry = useCallback(async (id) => {
        if (!window.confirm('Remove this container registry? Apps that pull from it will lose access.')) return false;
        try {
            await api.deleteContainerRegistry(id);
            toast.success('Registry removed');
            await loadData();
            return true;
        } catch (err) {
            toast.error(err.message || 'Failed to remove registry');
            return false;
        }
    }, [toast, loadData]);

    const onTestRegistry = useCallback(async (id) => {
        try {
            const res = await api.testContainerRegistry(id);
            if (res && res.success) toast.success(res.message || 'Login works');
            else toast.error((res && res.error) || 'Login failed');
            return res;
        } catch (err) {
            toast.error(err.message || 'Login failed');
            return null;
        }
    }, [toast]);

    // ── Per-provider card summaries ──
    const summaries = useMemo(() => {
        const out = {};
        const cloudByType = (type) => cloudProviders.filter((p) => p.provider_type === type);

        for (const provider of CONNECTION_PROVIDERS) {
            if (provider.comingSoon) { out[provider.id] = { connected: false }; continue; }
            const manageHref = provider.manageHref;

            if (provider.kind === 'source') {
                const status = sourceStatus[provider.provider];
                const conn = status?.connection;
                out[provider.id] = conn
                    ? {
                        connected: true, statusLabel: 'Connected', statusTone: 'ok',
                        subtitle: conn.provider_username ? `@${conn.provider_username}` : (conn.display_name || null),
                        scopes: [{ label: 'OAuth', tone: 'neutral', hint: conn.scope || 'Authorized via OAuth' }],
                        manageHref, manageLabel: 'New service',
                    }
                    : { connected: false, statusLabel: status?.configured ? 'Not connected' : 'Setup needed', statusTone: 'neutral', scopes: [] };
            } else if (provider.kind === 'cloud') {
                const matches = cloudByType(provider.providerType);
                const count = matches.reduce((n, p) => n + (p.server_count || 0), 0);
                out[provider.id] = matches.length
                    ? {
                        connected: true, statusLabel: 'Connected', statusTone: 'ok',
                        subtitle: count ? `${count} server${count === 1 ? '' : 's'}` : 'No servers yet',
                        scopes: [], manageHref, manageLabel: 'Servers',
                    }
                    : { connected: false, statusLabel: 'Not connected', statusTone: 'neutral', scopes: [] };
            } else if (provider.kind === 'dns') {
                const list = dnsProviders.filter((p) => p.provider === provider.provider);
                out[provider.id] = list.length
                    ? {
                        connected: true, statusLabel: list.length === 1 ? 'Connected' : `${list.length} connected`, statusTone: 'ok',
                        subtitle: list.map((p) => p.name).join(', '),
                        scopes: dedupeScopes(list.map(deriveScope).filter(Boolean)),
                        manageHref: '/domains', manageLabel: 'Domains',
                    }
                    : { connected: false, statusLabel: 'Not connected', statusTone: 'neutral', scopes: [] };
            } else if (provider.kind === 'registrar') {
                const list = registrarConnections.filter((c) => c.provider === provider.provider);
                const mine = registrarDomains.filter((d) => d.registrar === provider.provider);
                const expiring = mine.filter((d) => d.days_until_expiry != null && d.days_until_expiry <= 30).length;
                out[provider.id] = list.length
                    ? {
                        connected: true, statusLabel: 'Connected', statusTone: 'ok',
                        subtitle: `${mine.length} domain${mine.length === 1 ? '' : 's'}${expiring ? ` · ${expiring} expiring ≤30d` : ''}`,
                        scopes: expiring ? [{ label: `${expiring} expiring`, tone: 'warn', hint: 'Registration expires within 30 days' }] : [],
                        manageHref, manageLabel: 'Domains',
                    }
                    : { connected: false, statusLabel: 'Not connected', statusTone: 'neutral', scopes: [] };
            } else if (provider.kind === 'registry') {
                const list = containerRegistries;
                out[provider.id] = list.length
                    ? {
                        connected: true, statusLabel: list.length === 1 ? 'Connected' : `${list.length} connected`, statusTone: 'ok',
                        subtitle: list.map((r) => r.name).join(', '),
                        scopes: [], manageHref, manageLabel: 'New service',
                    }
                    : { connected: false, statusLabel: 'Not connected', statusTone: 'neutral', scopes: [] };
            } else if (provider.kind === 'storage') {
                const active = storageConfig?.provider === provider.storageProvider;
                const sub = storageConfig?.[provider.storageProvider];
                out[provider.id] = active && sub?.bucket
                    ? {
                        connected: true, statusLabel: 'Active', statusTone: 'ok',
                        subtitle: `Bucket: ${sub.bucket}`,
                        scopes: [{ label: 'Backups', tone: 'neutral', hint: 'Used as the offsite backup destination' }],
                        manageHref, manageLabel: 'Backups',
                    }
                    : { connected: false, statusLabel: 'Not connected', statusTone: 'neutral', scopes: [] };
            } else if (provider.kind === 'email') {
                out[provider.id] = (relayConfig?.enabled && relayConfig?.host)
                    ? {
                        connected: true, statusLabel: 'Active', statusTone: 'ok',
                        subtitle: `${relayConfig.host}:${relayConfig.port || 587}`,
                        scopes: relayConfig.use_tls ? [{ label: 'TLS', tone: 'ok', hint: 'STARTTLS enabled' }] : [],
                    }
                    : { connected: false, statusLabel: 'Not connected', statusTone: 'neutral', scopes: [] };
            } else {
                out[provider.id] = { connected: false };
            }
        }
        return out;
    }, [sourceStatus, cloudProviders, dnsProviders, registrarConnections, registrarDomains, containerRegistries, storageConfig, relayConfig]);

    function handleManage(provider) {
        setModalProvider(provider);
        setModalOpen(true);
    }

    const unencryptedCount = allConnections.filter((c) => c && c.encrypted === false).length;

    // Defense in depth: Settings only mounts this for admins and the registry API is
    // admin-gated, but guard here too so the hub never half-renders for a non-admin.
    if (!isAdmin) {
        return (
            <div className="connections-hub">
                <div className="connections-hub__warning">
                    <ShieldAlert size={16} />
                    <span>Connections are managed by administrators.</span>
                </div>
            </div>
        );
    }

    return (
        <div className="connections-hub">
            {!loading && unencryptedCount > 0 && (
                <div className="connections-hub__warning">
                    <ShieldAlert size={16} />
                    <span>
                        {unencryptedCount} connected account{unencryptedCount === 1 ? '' : 's'} {unencryptedCount === 1 ? 'has' : 'have'} credentials not encrypted at rest.
                        Restart the panel to migrate them, or check that <code>SERVERKIT_ENCRYPTION_KEY</code> is set.
                    </span>
                </div>
            )}
            {loading ? (
                <div className="connections-hub__loading">Loading connections…</div>
            ) : (
                CONNECTION_CATEGORIES.map((cat) => {
                    const providers = CONNECTION_PROVIDERS
                        .filter((p) => p.category === cat.key)
                        .filter(isProviderAvailable);
                    if (!providers.length) return null;
                    const focusId = CATEGORY_FOCUS_ID[cat.key];
                    return (
                        <section
                            key={cat.key}
                            {...(focusId
                                ? register(focusId, 'connections-hub__category')
                                : { className: 'connections-hub__category' })}
                        >
                            <header className="connections-hub__category-head">
                                <h3>{cat.label}</h3>
                                <p>{cat.blurb}</p>
                            </header>
                            <div className="connections-hub__grid">
                                {providers.map((provider) => (
                                    <ProviderCard
                                        key={provider.id}
                                        provider={provider}
                                        summary={summaries[provider.id]}
                                        onManage={handleManage}
                                    />
                                ))}
                            </div>
                        </section>
                    );
                })
            )}

            <ConnectProviderModal
                provider={modalProvider}
                open={modalOpen}
                onOpenChange={setModalOpen}
                isAdmin={isAdmin}
                sourceStatus={sourceStatus}
                sourceConfig={sourceConfig}
                dnsProviders={dnsProviders}
                cloudProviders={cloudProviders}
                storageConfig={storageConfig}
                registrarConnections={registrarConnections}
                containerRegistries={containerRegistries}
                onConnectSource={onConnectSource}
                onDisconnectSource={onDisconnectSource}
                onSaveSourceConfig={onSaveSourceConfig}
                onSetupGithubApp={onSetupGithubApp}
                onAddDns={onAddDns}
                onRemoveDns={onRemoveDns}
                onTestDns={onTestDns}
                onAddCloud={onAddCloud}
                onRemoveCloud={onRemoveCloud}
                onSaveStorage={onSaveStorage}
                onTestStorage={onTestStorage}
                onAddRegistrar={onAddRegistrar}
                onRemoveRegistrar={onRemoveRegistrar}
                onTestRegistrar={onTestRegistrar}
                onAddRegistry={onAddRegistry}
                onRemoveRegistry={onRemoveRegistry}
                onTestRegistry={onTestRegistry}
                relayConfig={relayConfig}
                onSaveRelay={onSaveRelay}
                onTestRelay={onTestRelay}
                onDisableRelay={onDisableRelay}
            />
        </div>
    );
}
