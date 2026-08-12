import { useState, useEffect, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import api from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { useConfirm } from '../hooks/useConfirm';
import { Button } from '@/components/ui/button';
import { Pill } from '../components/ds';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import PackagesTab from '../components/serverdetail/PackagesTab';
import ServicesTab from '../components/serverdetail/ServicesTab';
import ServerOverviewTab from '../components/serverdetail/ServerOverviewTab';
import AlertsTab from '../components/serverdetail/AlertsTab';
import ServerDockerTab from '../components/serverdetail/ServerDockerTab';
import CronTab from '../components/serverdetail/CronTab';
import CloudflaredTab from '../components/serverdetail/CloudflaredTab';
import ServerMetricsTab from '../components/serverdetail/ServerMetricsTab';
import SurveyTab from '../components/serverdetail/SurveyTab';
import ServerSettingsTab, { TokenModal } from '../components/serverdetail/ServerSettingsTab';
import {
    STATUS_PILL_KIND,
    CopyChip,
    FolderTinyIcon,
    RefreshIcon,
} from '../components/serverdetail/serverDetailShared';
import ProxyStackPanel from '../components/proxy/ProxyStackPanel';
import RemoteAccess from '../pages/RemoteAccess';
import { useHasPlugin } from '../plugins/contributions';
import EmptyState from '../components/EmptyState';

const ServerDetail = () => {
    const { id, tab } = useParams();
    const navigate = useNavigate();
    const { confirm } = useConfirm();
    const hasRemoteAccessExtension = useHasPlugin('serverkit-remote-access');
    const [server, setServer] = useState(null);
    const [metrics, setMetrics] = useState(null);
    const [systemInfo, setSystemInfo] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [showTokenModal, setShowTokenModal] = useState(false);
    const [securityAlerts, setSecurityAlerts] = useState([]);
    const toast = useToast();

    const validTabs = ['overview', 'docker', 'proxy', 'cron', 'cloudflared', 'packages', 'services', 'survey', 'metrics', 'alerts', 'remote-access', 'settings'];
    const activeTab = validTabs.includes(tab) ? tab : 'overview';

    const loadServer = useCallback(async () => {
        try {
            const data = await api.getServer(id);
            setServer(data);
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, [id]);

    const loadMetrics = useCallback(async () => {
        if (!server || server.status !== 'online') return;
        try {
            const data = await api.getRemoteSystemMetrics(id);
            // Endpoint returns the metrics payload directly, not a {success,data} envelope.
            if (data) setMetrics(data);
        } catch (err) {
            console.error('Failed to load metrics:', err);
        }
    }, [id, server]);

    const loadSystemInfo = useCallback(async () => {
        if (!server || server.status !== 'online') return;
        try {
            const data = await api.getRemoteSystemInfo(id);
            if (data) setSystemInfo(data);
        } catch (err) {
            console.error('Failed to load system info:', err);
        }
    }, [id, server]);

    useEffect(() => {
        loadServer();
    }, [loadServer]);

    const loadSecurityAlerts = useCallback(async () => {
        try {
            const data = await api.getServerSecurityAlerts(id, { status: 'open', limit: 25 });
            setSecurityAlerts(Array.isArray(data) ? data : []);
        } catch (err) {
            console.error('Failed to load security alerts:', err);
        }
    }, [id]);

    useEffect(() => {
        loadSecurityAlerts();
    }, [loadSecurityAlerts]);

    async function handleAcknowledgeAlert(alertId) {
        try {
            await api.acknowledgeAlert(alertId);
            setSecurityAlerts(prev => prev.map(a =>
                a.id === alertId ? { ...a, status: 'acknowledged' } : a
            ));
        } catch {
            toast.error('Failed to acknowledge alert');
        }
    }

    async function handleResolveAlert(alertId) {
        try {
            await api.resolveAlert(alertId);
            setSecurityAlerts(prev => prev.filter(a => a.id !== alertId));
        } catch {
            toast.error('Failed to resolve alert');
        }
    }

    useEffect(() => {
        if (server?.status === 'online') {
            loadMetrics();
            loadSystemInfo();
            const interval = setInterval(loadMetrics, 10000);
            return () => clearInterval(interval);
        }
    }, [server, loadMetrics, loadSystemInfo]);

    async function handleDeleteServer() {
        const confirmed = await confirm({ title: 'Remove Server', message: 'Are you sure you want to remove this server? This action cannot be undone.' });
        if (!confirmed) return;

        try {
            await api.deleteServer(id);
            toast.success('Server removed successfully');
            navigate('/servers');
        } catch (err) {
            toast.error(err.message || 'Failed to remove server');
        }
    }

    async function handlePingServer() {
        try {
            const result = await api.pingServer(id);
            if (result.success) {
                toast.success(`Server responded in ${result.latency}ms`);
                loadServer();
            } else {
                toast.error('Server did not respond');
            }
        } catch (err) {
            toast.error('Failed to ping server');
        }
    }

    // Both the inline "Generate Token" header button and the SettingsTab
    // regenerate button funnel through the same modal — the modal owns the
    // expiry picker and the connection-string display, so the header path
    // doesn't need its own confirm dialog. Reaching the modal effectively
    // *is* the confirmation: the actual token mint happens when the user
    // clicks "Generate" inside it.
    async function handleOpenTokenModal() {
        setShowTokenModal(true);
    }

    function handleTokenGenerated(result) {
        // Mirror the new token onto the in-memory server so the existing
        // AgentRegistrationSection (rendered in SettingsTab) reflects it
        // without a full reload.
        setServer(prev => ({
            ...prev,
            registration_token: result.registration_token,
            registration_expires: result.registration_expires,
            connection_string: result.connection_string,
        }));
    }

    if (loading) {
        return <EmptyState loading loadingVariant="detail" title="Loading server details" />;
    }

    if (error) {
        return (
            <div className="error-page">
                <h2>Error Loading Server</h2>
                <p>{error}</p>
                <Button asChild><Link to="/servers">Back to Servers</Link></Button>
            </div>
        );
    }

    if (!server) {
        return (
            <div className="error-page">
                <h2>Server Not Found</h2>
                <p>The requested server could not be found.</p>
                <Button asChild><Link to="/servers">Back to Servers</Link></Button>
            </div>
        );
    }

    // Aggregate any "you should know about this" alerts into a single
    // Alerts tab. Today only the polling-transport fallback shows up as
    // a system notification, but this is the place to add future
    // advisories (stale agent, missing capabilities, expiring tokens,
    // etc.). Security alerts (raised by the security service) are
    // surfaced alongside them so there's only one place to look.
    const systemNotifications = [];
    if (server.transport === 'poll') {
        systemNotifications.push({
            id: 'limited-mode',
            severity: 'warning',
            title: 'Limited mode',
            message:
                'This agent connected via the REST polling fallback because the WebSocket link could not be established cleanly. Heartbeats and one-shot commands work; live logs, real-time metrics, and terminal sessions are unavailable until the WS link is restored.',
        });
    }
    const openSecurityAlerts = securityAlerts.filter(a => a.status === 'open');
    const totalAlertCount = systemNotifications.length + openSecurityAlerts.length;

    // Show the cron tab only when the agent reported the capability.
    // Older agents (pre-1.6.16) and Windows hosts won't have it set —
    // hiding the tab matches the rest of the panel's "don't expose what
    // the host can't do" behaviour.
    const tabs = [
        { id: 'overview', label: 'Overview' },
        { id: 'docker', label: 'Docker' },
        { id: 'proxy', label: 'Proxy' },
        ...(server.capabilities?.cron ? [{ id: 'cron', label: 'Cron' }] : []),
        ...(server.capabilities?.cloudflared ? [{ id: 'cloudflared', label: 'Tunnels' }] : []),
        ...(server.capabilities?.packages ? [{ id: 'packages', label: 'Packages' }] : []),
        ...(server.capabilities?.systemd ? [{ id: 'services', label: 'Services' }] : []),
        ...(server.capabilities?.survey ? [{ id: 'survey', label: 'Survey' }] : []),
        { id: 'metrics', label: 'Metrics' },
        ...(totalAlertCount > 0
            ? [{ id: 'alerts', label: 'Alerts', badge: totalAlertCount }]
            : [{ id: 'alerts', label: 'Alerts' }]),
        // The agent advertising wireguard is necessary but NOT sufficient: the tab
        // renders RemoteAccess, whose whole API (/api/v1/tunnels/*) is served by
        // the Remote Access extension. Gated on the capability alone, a panel
        // without that extension showed the tab on every wireguard-capable agent
        // and greeted the operator with a red "Not found" toast plus a wizard whose
        // Save can never enable, with nothing naming the missing extension.
        ...(server.capabilities?.wireguard && hasRemoteAccessExtension
            ? [{ id: 'remote-access', label: 'Remote Access' }] : []),
        { id: 'settings', label: 'Settings' }
    ];

    return (
        <div className="page-container server-detail-page">
            <div className="page-breadcrumb">
                <Link to="/servers">Servers</Link>
                <span className="breadcrumb-separator">/</span>
                <span>{server.name}</span>
            </div>

            <header className="server-detail-header">
                <div className="server-detail-header__main">
                    <div className={`server-detail-header__avatar server-detail-header__avatar--${server.status || 'pending'}`}>
                        {(server.name || '?').charAt(0).toUpperCase()}
                    </div>
                    <div className="server-detail-header__identity">
                        <div className="server-detail-header__title-row">
                            <h1>{server.name}</h1>
                            <Pill kind={STATUS_PILL_KIND[server.status] || 'gray'}>
                                {server.status || 'pending'}
                            </Pill>
                            <CopyChip
                                label="id"
                                value={server.id}
                                title="Copy server ID"
                                mono
                            />
                        </div>
                        <div className="server-detail-header__meta">
                            <span className="server-detail-header__meta-item">
                                {server.hostname || server.ip_address || 'No endpoint configured'}
                            </span>
                            {server.group_name && (
                                <>
                                    <span className="dotsep">·</span>
                                    <span className="server-detail-header__meta-item"><FolderTinyIcon /> {server.group_name}</span>
                                </>
                            )}
                            {server.os_type && (
                                <>
                                    <span className="dotsep">·</span>
                                    <span className="server-detail-header__meta-item">{server.os_type}</span>
                                </>
                            )}
                            {server.agent_version && (
                                <>
                                    <span className="dotsep">·</span>
                                    <span className="server-detail-header__meta-item">agent {server.agent_version}</span>
                                </>
                            )}
                            {server.last_seen && (
                                <>
                                    <span className="dotsep">·</span>
                                    <span className="server-detail-header__meta-item">
                                        last seen {new Date(server.last_seen).toLocaleString()}
                                    </span>
                                </>
                            )}
                        </div>
                        {server.description && (
                            <p className="server-detail-header__description">{server.description}</p>
                        )}
                    </div>
                </div>
                <div className="server-detail-header__actions">
                    <Button variant="outline" size="sm" onClick={handlePingServer}>
                        <RefreshIcon /> Ping
                    </Button>
                </div>
            </header>

            <Tabs
                value={activeTab}
                onValueChange={(value) =>
                    navigate(value === 'overview' ? `/servers/${id}` : `/servers/${id}/${value}`, { replace: true })
                }
            >
                <TabsList>
                    {tabs.map(t => (
                        <TabsTrigger key={t.id} value={t.id}>
                            {t.label}
                            {t.badge ? <span className="tab-badge">{t.badge}</span> : null}
                        </TabsTrigger>
                    ))}
                </TabsList>

                <div className="server-detail-content">
                    <TabsContent value="overview">
                        <ServerOverviewTab
                            server={server}
                            metrics={metrics}
                            systemInfo={systemInfo}
                            onRefreshServer={loadServer}
                        />
                    </TabsContent>
                    <TabsContent value="docker">
                        <ServerDockerTab serverId={id} serverStatus={server.status} server={server} />
                    </TabsContent>
                    <TabsContent value="proxy">
                        <ProxyStackPanel serverId={id} />
                    </TabsContent>
                    {server.capabilities?.cron && (
                        <TabsContent value="cron">
                            <CronTab serverId={id} serverStatus={server.status} />
                        </TabsContent>
                    )}
                    {server.capabilities?.cloudflared && (
                        <TabsContent value="cloudflared">
                            <CloudflaredTab serverId={id} serverStatus={server.status} />
                        </TabsContent>
                    )}
                    {server.capabilities?.packages && (
                        <TabsContent value="packages">
                            <PackagesTab serverId={id} serverStatus={server.status} />
                        </TabsContent>
                    )}
                    {server.capabilities?.systemd && (
                        <TabsContent value="services">
                            <ServicesTab serverId={id} serverStatus={server.status} />
                        </TabsContent>
                    )}
                    {server.capabilities?.survey && (
                        <TabsContent value="survey">
                            <SurveyTab serverId={id} serverStatus={server.status} server={server} />
                        </TabsContent>
                    )}
                    <TabsContent value="metrics">
                        <ServerMetricsTab serverId={id} metrics={metrics} />
                    </TabsContent>
                    <TabsContent value="alerts">
                        <AlertsTab
                            notifications={systemNotifications}
                            securityAlerts={securityAlerts}
                            onAcknowledge={handleAcknowledgeAlert}
                            onResolve={handleResolveAlert}
                        />
                    </TabsContent>
                    <TabsContent value="remote-access">
                        {hasRemoteAccessExtension ? (
                            <RemoteAccess serverId={id} />
                        ) : (
                            <EmptyState
                                title="Remote Access extension not installed"
                                description="Exposing a local service over WireGuard is provided by the Remote Access extension. Install it from Extensions to use this tab."
                                action={<Button onClick={() => navigate('/extensions')}>Open Extensions</Button>}
                            />
                        )}
                    </TabsContent>
                    <TabsContent value="settings">
                        <ServerSettingsTab
                            server={server}
                            onUpdate={loadServer}
                            onRegenerateToken={handleOpenTokenModal}
                            onDelete={handleDeleteServer}
                        />
                    </TabsContent>
                </div>
            </Tabs>

            {showTokenModal && server && (
                <TokenModal
                    server={server}
                    onClose={() => setShowTokenModal(false)}
                    onGenerated={handleTokenGenerated}
                />
            )}
        </div>
    );
};

export default ServerDetail;
