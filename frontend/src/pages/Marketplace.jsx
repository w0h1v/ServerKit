import { useState, useEffect, useCallback } from 'react';
import { useLocation, useSearchParams, useNavigate } from 'react-router-dom';
import {
    Activity,
    CheckCircle2,
    DownloadCloud,
    ExternalLink,
    Gamepad2,
    LayoutGrid,
    Package,
    PackageCheck,
    Plug,
    PlugZap,
    ServerCog,
    ShieldAlert,
    ShieldCheck,
    Sparkles,
    Star,
    UploadCloud,
} from 'lucide-react';
import api from '../services/api';
import { useToast } from '../contexts/ToastContext';
import { sanitizeSvgInner } from '../utils/sanitizeSvg';
import Modal from '@/components/Modal';
import PageLoader from '../components/PageLoader';
import EmptyState from '../components/EmptyState';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
    SearchField, FilterDrawer, FilterButton, countActiveFilters,
} from '@/components/ds';
import { useTopbarActions } from '@/hooks/useTopbarActions';
import ManualInstallModal from '../components/marketplace/ManualInstallModal';
import {
    ExtensionBrandMark, hasBrandMark, extensionCoverStyle,
} from '../components/icons/ExtensionBrands';
import { resolveExtensionIcon } from '../components/icons/ExtensionIcons';

const CATEGORIES = ['ai', 'games', 'monitoring', 'security', 'deployment', 'integration', 'ui', 'utility'];

const CATEGORY_ICONS = {
    ai: Sparkles,
    games: Gamepad2,
    monitoring: Activity,
    security: ShieldCheck,
    deployment: ServerCog,
    integration: Plug,
    ui: LayoutGrid,
    utility: Package,
};

const titleCase = (value = '') => {
    const cleaned = String(value || 'utility').replace(/[-_]/g, ' ');
    return cleaned
        .split(' ')
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
};

const getCategoryIcon = (category) => CATEGORY_ICONS[category] || Package;

// A first-party ("by ServerKit") entry is one authored by ServerKit or one whose
// manifest explicitly opts in via `first_party`. Case-insensitive on author.
const isFirstParty = (author, source = {}) => {
    if (source && source.first_party) return true;
    return String(author || '').trim().toLowerCase() === 'serverkit';
};

// Union of categories actually present across the merged catalog, ordered by the
// canonical CATEGORIES list first, then any extras alphabetically (stable, deduped).
const deriveCatalogCategories = (entries) => {
    const present = new Set(entries.map((entry) => entry.category || 'utility'));
    const known = CATEGORIES.filter((item) => present.has(item));
    const extra = [...present].filter((item) => !CATEGORIES.includes(item)).sort();
    return [...known, ...extra];
};

const getRegistryCatalogEntry = (entry) => ({
    key: `registry:${entry.slug}`,
    source: 'registry',
    sourceLabel: 'Registry',
    sourceDetail: 'Remote registry package',
    installKey: entry.slug,
    displayName: entry.display_name || entry.slug,
    description: entry.description || 'No description provided.',
    category: entry.category || 'utility',
    version: entry.version || '0.0.0',
    author: entry.author,
    firstParty: isFirstParty(entry.author, entry),
    trust: entry.trust || 'unreviewed',
    review: entry.review || null,
    // Release-signature state from the index (plan 55): `signed` means the
    // entry carries an ed25519 signature the panel verifies at install;
    // `publisherTrusted` means the named key is pinned by this panel.
    signed: Boolean(entry.signature),
    publisherTrusted: Boolean(entry.publisher_trusted),
    publisherKeyId: entry.publisher_key_id || null,
    icon: entry.icon || null,
    logo: entry.logo || null,
    repo: entry.repo || entry.homepage || null,
    screenshots: Array.isArray(entry.screenshots) ? entry.screenshots : [],
    permissions: Array.isArray(entry.permissions) ? entry.permissions : [],
    configSchema: entry.config_schema && typeof entry.config_schema === 'object' ? entry.config_schema : null,
    extensionType: 'registry',
    installed: Boolean(entry.installed),
    status: entry.status,
    featured: Boolean(entry.featured),
    featureScore: Number(entry.feature_score) || 0,
});

// Source badge tint: built-in is 'warning', registry is 'info'.
const sourceBadgeVariant = (source) => {
    if (source === 'local') return 'warning';
    if (source === 'registry') return 'info';
    return 'outline';
};

// Trust badge derived from the registry's hash-bound review stamp. 'reviewed'
// shows who/when in the tooltip; 'unreviewed' warns on registry entries
// (built-in entries never carry a trust field).
const TrustBadge = ({ entry }) => {
    if (entry.trust === 'reviewed') {
        const review = entry.review || {};
        const title = review.reviewer && review.date
            ? `Reviewed by ${review.reviewer} on ${review.date}`
            : 'Reviewed by the ServerKit maintainers';
        return (
            <Badge variant="success" title={title}>
                <ShieldCheck aria-hidden="true" /> Reviewed
            </Badge>
        );
    }
    if (entry.trust === 'unreviewed' && entry.source === 'registry') {
        return <Badge variant="warning">Unreviewed</Badge>;
    }
    return null;
};

// Release-signature badge (plan 55). Shown on the registry detail modal next
// to TrustBadge: 'signed · verified' means the index pins an ed25519 signature
// whose publisher key this panel trusts (verified cryptographically at
// install); unsigned entries get the honest unsigned treatment.
const SignatureBadge = ({ entry }) => {
    if (entry.source !== 'registry') return null;
    if (entry.signed && entry.publisherTrusted) {
        return (
            <Badge variant="success" title="Release zip is signed; the panel verifies the ed25519 signature at install">
                <ShieldCheck aria-hidden="true" /> Signed
            </Badge>
        );
    }
    if (entry.signed) {
        return (
            <Badge variant="warning" title={`Signed with publisher key '${entry.publisherKeyId}', which this panel does not pin`}>
                <ShieldAlert aria-hidden="true" /> Unknown signer
            </Badge>
        );
    }
    return (
        <Badge variant="outline" title="This release carries no publisher signature">
            Unsigned
        </Badge>
    );
};

const getLocalCatalogEntry = (builtin) => {
    const manifest = builtin.manifest || {};

    return {
        key: `local:${builtin.slug}`,
        source: 'local',
        sourceLabel: 'Built-in',
        sourceDetail: 'Bundled with ServerKit',
        installKey: builtin.slug,
        displayName: manifest.display_name || builtin.slug,
        description: manifest.description || 'Bundled extension.',
        category: manifest.category || 'utility',
        version: manifest.version || '0.0.0',
        author: manifest.author,
        firstParty: isFirstParty(manifest.author, manifest),
        icon: manifest.icon || null,
        logo: manifest.logo || null,
        screenshots: Array.isArray(manifest.screenshots) ? manifest.screenshots : [],
        permissions: Array.isArray(manifest.permissions) ? manifest.permissions : [],
        configSchema: manifest.config_schema && typeof manifest.config_schema === 'object' ? manifest.config_schema : null,
        extensionType: 'built-in',
        installed: Boolean(builtin.installed),
        status: builtin.status,
        featured: Boolean(manifest.featured),
        featureScore: Number(manifest.feature_score) || 0,
    };
};

const catalogEntryMatches = (entry, search, category) => {
    if (category && entry.category !== category) return false;

    const query = search.trim().toLowerCase();
    if (!query) return true;

    return [
        entry.displayName,
        entry.description,
        entry.category,
        entry.author,
        entry.sourceLabel,
    ].some((value) => String(value || '').toLowerCase().includes(query));
};

const Marketplace = () => {
    const toast = useToast();
    const [plugins, setPlugins] = useState([]);
    const [builtins, setBuiltins] = useState([]);
    const [registryExtensions, setRegistryExtensions] = useState([]);
    const [pluginUpdates, setPluginUpdates] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    // Advanced filters live in a shared FilterDrawer. `ownership` is single-select
    // ('' all, 'serverkit' firstParty, 'community'); `category` is multi-select.
    const [filters, setFilters] = useState({ ownership: '', category: [] });
    const [filtersOpen, setFiltersOpen] = useState(false);
    const location = useLocation();
    const [searchParams, setSearchParams] = useSearchParams();
    const navigate = useNavigate();
    // The active view is driven by the route (/marketplace = browse,
    // /marketplace/installed = installed). The legacy ?tab=installed deep link
    // still resolves to the installed view.
    const installedView = location.pathname.endsWith('/installed')
        || searchParams.get('tab') === 'installed';
    // Manual install modal (URL / folder / zip); null when closed, otherwise
    // the source sub-tab to preselect.
    const [manualInstallSource, setManualInstallSource] = useState(null);
    const [installing, setInstalling] = useState(false);
    const [detailEntry, setDetailEntry] = useState(null);
    // Registry entry awaiting risk confirmation — drives the acknowledge-risk
    // dialog (confirm retries with acknowledge_risk: true). Shape:
    // { slug, reason } where reason is 'unreviewed' (proactive gate or 409),
    // 'unverified' (409: entry has no pinned checksum), or 'untrusted_key'
    // (signed by a publisher key this panel doesn't pin — plan 55).
    const [riskTarget, setRiskTarget] = useState(null);
    // Plugin pending uninstall — drives the keep-vs-purge data-policy dialog.
    const [uninstallTarget, setUninstallTarget] = useState(null);
    // Id of the installed plugin whose row action (uninstall/toggle/update) is
    // in flight. Drives per-row button disabling + live status copy so an
    // operation can't be double-fired and the operator always sees progress.
    const [busyPlugin, setBusyPlugin] = useState(null); // { id, action }
    // Plugin whose config is being edited (#49) — drives the config dialog.
    const [configTarget, setConfigTarget] = useState(null);

    const loadExtensions = useCallback(async () => {
        try {
            const [pData, bData, rData, uData] = await Promise.all([
                api.getInstalledPlugins().catch(() => ({ plugins: [] })),
                api.getBuiltinExtensions().catch(() => ({ builtin: [] })),
                api.getRegistryExtensions().catch(() => ({ extensions: [] })),
                api.getPluginUpdates().catch(() => ({ updates: [] })),
            ]);
            setPlugins(pData.plugins || []);
            setBuiltins(bData.builtin || []);
            setRegistryExtensions(rData.extensions || []);
            setPluginUpdates(uData.updates || []);
        } catch {
            toast.error('Failed to load extensions');
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => { loadExtensions(); }, [loadExtensions]);

    // Deep link: /extensions?install=<slug> opens that extension's detail
    // modal. This is what a serverkit.ai install link lands on (and the
    // counterpart to Templates.jsx's ?install=<id>).
    //
    // It opens the modal rather than installing: a URL arriving from another
    // site must never be able to install anything on its own, so the operator
    // still presses Install and every trust gate behind it still fires.
    //
    // Note /marketplace?install=… cannot work — that path only redirects here
    // via <Navigate>, which drops the query string. Links must target
    // /extensions directly.
    const installSlug = searchParams.get('install');
    useEffect(() => {
        if (!installSlug || loading) return;

        // Lookup only, so the catalog's featured/sort ordering is irrelevant.
        const entry = [
            ...builtins.map(getLocalCatalogEntry),
            ...registryExtensions.map(getRegistryCatalogEntry),
        ].find((candidate) => candidate.installKey === installSlug);

        if (entry) {
            setDetailEntry(entry);
        } else {
            toast.error(`No extension named "${installSlug}" in this panel's catalog.`);
        }

        // Drop the param either way so a refresh doesn't reopen it.
        const next = new URLSearchParams(searchParams);
        next.delete('install');
        setSearchParams(next, { replace: true });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [installSlug, loading, builtins, registryExtensions]);

    const handleBuiltinInstall = async (slug) => {
        setInstalling(true);
        try {
            const result = await api.installBuiltinExtension(slug);
            toast.success(`Installed "${result.display_name}". Hot-reload should pick it up; restart backend if blueprint routes do not appear.`);
            loadExtensions();
        } catch (err) {
            toast.error(err.message || 'Local install failed');
        } finally {
            setInstalling(false);
        }
    };

    const handleRegistryInstall = async (slug, acknowledgeRisk = false) => {
        setInstalling(true);
        try {
            const result = await api.installRegistryExtension(
                slug, acknowledgeRisk ? { acknowledge_risk: true } : undefined);
            toast.success(`Installed "${result.display_name}". Restart backend if blueprint routes do not appear.`);
            loadExtensions();
        } catch (err) {
            // 409 trust gate — ask for explicit confirmation, then retry.
            if (err.status === 409 && err.data?.requires_acknowledgment) {
                setRiskTarget({ slug, reason: err.data?.reason || 'unreviewed' });
            } else {
                toast.error(err.message || 'Registry install failed');
            }
        } finally {
            setInstalling(false);
        }
    };

    const installEntry = (entry) => {
        if (entry.source === 'local') {
            handleBuiltinInstall(entry.installKey);
        } else if (entry.trust === 'unreviewed') {
            // Proactive gate: unreviewed registry entries confirm first
            // (the backend 409 path above covers the race/stale cases).
            setRiskTarget({ slug: entry.installKey, reason: 'unreviewed' });
        } else if (entry.signed && !entry.publisherTrusted) {
            // Signed by a publisher key this panel doesn't pin — consent first
            // (plan 55); the backend 409 path is the authoritative backstop.
            setRiskTarget({ slug: entry.installKey, reason: 'untrusted_key' });
        } else {
            handleRegistryInstall(entry.installKey);
        }
    };

    const confirmRiskyInstall = () => {
        const slug = riskTarget?.slug;
        const updatePluginId = riskTarget?.updatePluginId;
        setRiskTarget(null);
        if (updatePluginId) {
            handlePluginUpdate(updatePluginId, true);
        } else if (slug) {
            handleRegistryInstall(slug, true);
        }
    };

    // Land on Installed after a manual install so the new row (and its
    // actions) is immediately visible.
    const handleManualInstalled = () => {
        setManualInstallSource(null);
        loadExtensions();
        navigate('/extensions/installed');
    };

    // Open the data-policy dialog instead of uninstalling immediately, so the
    // operator can choose to keep or purge the extension's tables.
    const requestPluginUninstall = (plugin) => setUninstallTarget(plugin);

    const confirmPluginUninstall = async (purge) => {
        const plugin = uninstallTarget;
        setUninstallTarget(null);
        if (!plugin || busyPlugin) return;
        setBusyPlugin({ id: plugin.id, action: 'uninstall' });
        try {
            await api.uninstallPlugin(plugin.id, purge);
            toast.success(purge ? 'Extension uninstalled; data purged' : 'Extension uninstalled; data kept');
            await loadExtensions();
        } catch (err) {
            toast.error(err.message);
        } finally {
            setBusyPlugin(null);
        }
    };

    const handlePluginUpdate = async (pluginId, acknowledgeRisk = false) => {
        if (busyPlugin) return;
        setBusyPlugin({ id: pluginId, action: 'update' });
        try {
            const result = await api.updatePlugin(
                pluginId, acknowledgeRisk ? { acknowledge_risk: true } : undefined);
            toast.success(`Extension "${result.display_name}" updated to v${result.version}.`);
            await loadExtensions();
        } catch (err) {
            // 409 consent gate (audit M2) — same acknowledge flow as installs.
            if (err.status === 409 && err.data?.requires_acknowledgment) {
                setRiskTarget({ updatePluginId: pluginId, reason: err.data?.reason || 'unsigned' });
            } else {
                toast.error(err.message || 'Extension update failed');
            }
        } finally {
            setBusyPlugin(null);
        }
    };

    const handlePluginToggle = async (plugin) => {
        if (busyPlugin) return;
        setBusyPlugin({ id: plugin.id, action: plugin.status === 'active' ? 'disable' : 'enable' });
        try {
            if (plugin.status === 'active') {
                await api.disablePlugin(plugin.id);
                toast.success('Extension disabled');
            } else {
                await api.enablePlugin(plugin.id);
                toast.success('Extension enabled');
            }
            await loadExtensions();
        } catch (err) {
            toast.error(err.message);
        } finally {
            setBusyPlugin(null);
        }
    };

    const resetFilters = () => {
        setSearch('');
        setFilters({ ownership: '', category: [] });
    };

    const pluginStatusVariant = (status) => {
        if (status === 'active') return 'success';
        if (status === 'error') return 'destructive';
        return 'outline';
    };

    const activeFilterCount = countActiveFilters(filters);

    // Search + advanced-filter trigger sit in the shared page top bar (next to
    // Install manually) so the browse body starts straight at the grid. Only the
    // browse view gets search/filters; the installed view keeps Install manually
    // alone. Search is debounced, so re-publishing here is cheap and React
    // reconciles the input in place (focus is preserved between keystrokes).
    useTopbarActions(() =>
        <>
            {!installedView && (
                <>
                    <SearchField
                        value={search}
                        onSearch={setSearch}
                        placeholder="Search extensions…"
                    />
                    <FilterButton count={activeFilterCount} onClick={() => setFiltersOpen(true)} />
                </>
            )}
            <Button variant="outline" size="sm" onClick={() => setManualInstallSource('url')}>
                <UploadCloud aria-hidden="true" />
                Install manually
            </Button>
        </>,
        [installedView, search, activeFilterCount],
    );

    if (loading) return <PageLoader />;

    const localCatalogEntries = builtins.map(getLocalCatalogEntry);
    const registryCatalogEntries = registryExtensions.map(getRegistryCatalogEntry);
    // Update descriptors keyed by both plugin_id and slug so PluginRow can match
    // whichever identifier it has on hand.
    const updatesByKey = new Map();
    pluginUpdates.forEach((update) => {
        if (update.plugin_id != null) updatesByKey.set(String(update.plugin_id), update);
        if (update.slug) updatesByKey.set(update.slug, update);
    });
    // Featured entries float to the top of the catalog, ordered by their
    // feature score (higher = more prominent). The score itself is not shown —
    // it only drives ordering; featured cards just get a "Featured" badge.
    const mergedCatalogEntries = [...localCatalogEntries, ...registryCatalogEntries]
        .map((entry, index) => ({ entry, index }))
        .sort((a, b) => {
            const fa = a.entry.featured ? 1 : 0;
            const fb = b.entry.featured ? 1 : 0;
            if (fa !== fb) return fb - fa;
            if (fa && a.entry.featureScore !== b.entry.featureScore) {
                return b.entry.featureScore - a.entry.featureScore;
            }
            return a.index - b.index; // stable: preserve original order otherwise
        })
        .map(({ entry }) => entry);
    const catalogCategories = deriveCatalogCategories(mergedCatalogEntries);
    const filterGroups = [
        {
            key: 'ownership',
            label: 'Publisher',
            type: 'single',
            options: [
                { value: 'serverkit', label: 'By ServerKit' },
                { value: 'community', label: 'Community' },
            ],
        },
        {
            key: 'category',
            label: 'Categories',
            type: 'multi',
            options: catalogCategories.map((item) => ({ value: item, label: titleCase(item) })),
        },
    ];
    const catalogEntries = mergedCatalogEntries
        // Category is handled below (multi-select), so pass '' to the shared matcher.
        .filter((entry) => catalogEntryMatches(entry, search, ''))
        .filter((entry) => {
            if (filters.ownership === 'serverkit') return entry.firstParty;
            if (filters.ownership === 'community') return !entry.firstParty;
            return true;
        })
        .filter((entry) => {
            const cats = filters.category || [];
            return cats.length === 0 || cats.includes(entry.category);
        });
    const hasFilters = Boolean(search.trim() || activeFilterCount > 0);

    return (
        <div className="sk-tabgroup__inner marketplace-page">
            {!installedView ? (
                <>
                    {hasFilters && (
                        <div className="marketplace-resultbar">
                            <Button variant="ghost" size="sm" onClick={resetFilters}>
                                Reset filters
                            </Button>
                        </div>
                    )}

                    <section className="marketplace-section">
                        {catalogEntries.length > 0 ? (
                            <div className="extensions-grid">
                                {catalogEntries.map((entry) => (
                                    <CatalogExtensionCard
                                        key={entry.key}
                                        entry={entry}
                                        installing={installing}
                                        onInstall={() => installEntry(entry)}
                                        onOpenDetail={setDetailEntry}
                                        statusVariant={pluginStatusVariant}
                                    />
                                ))}
                            </div>
                        ) : (
                            <EmptyState
                                icon={Package}
                                title="No catalog entries found"
                                description={hasFilters ? 'No built-in or registry entries match the current filter.' : 'No extension entries are available yet.'}
                            />
                        )}
                    </section>
                </>
            ) : (
                <section className="marketplace-section">
                    <SectionHeader
                        kicker="Installed"
                        title="Installed extensions"
                        meta={`${plugins.length} installed`}
                    />
                    {plugins.length > 0 ? (
                        <div className="installed-list">
                            {plugins.map((plugin) => (
                                <PluginRow
                                    key={plugin.id}
                                    plugin={plugin}
                                    update={updatesByKey.get(String(plugin.id))}
                                    busy={busyPlugin?.id === plugin.id ? busyPlugin.action : null}
                                    onToggle={handlePluginToggle}
                                    onUpdate={handlePluginUpdate}
                                    onUninstall={requestPluginUninstall}
                                    onConfigure={setConfigTarget}
                                    statusVariant={pluginStatusVariant}
                                />
                            ))}
                        </div>
                    ) : (
                        <EmptyState
                            icon={PackageCheck}
                            title="No extensions installed"
                            description="Install one from Extensions or use Install manually."
                        />
                    )}
                </section>
            )}

            <FilterDrawer
                open={filtersOpen}
                onOpenChange={setFiltersOpen}
                groups={filterGroups}
                value={filters}
                onChange={setFilters}
                title="Filter extensions"
            />

            {manualInstallSource && (
                <ManualInstallModal
                    defaultSource={manualInstallSource}
                    onClose={() => setManualInstallSource(null)}
                    onInstalled={handleManualInstalled}
                />
            )}

            {detailEntry && (
                <ExtensionDetailModal
                    entry={detailEntry}
                    installing={installing}
                    statusVariant={pluginStatusVariant}
                    onClose={() => setDetailEntry(null)}
                    onInstall={() => {
                        installEntry(detailEntry);
                        setDetailEntry(null);
                    }}
                />
            )}

            {uninstallTarget && (
                <PluginUninstallDialog
                    plugin={uninstallTarget}
                    onCancel={() => setUninstallTarget(null)}
                    onConfirm={confirmPluginUninstall}
                />
            )}

            {riskTarget && (
                <Modal
                    open
                    onClose={() => setRiskTarget(null)}
                    title={riskTarget.reason === 'unverified'
                        ? 'Install without checksum verification?'
                        : riskTarget.reason === 'untrusted_key'
                            ? 'Install with an unverifiable signature?'
                            : riskTarget.reason === 'unsigned'
                                ? `${riskTarget.updatePluginId ? 'Update' : 'Install'} an unsigned release?`
                                : 'Install unreviewed extension?'}
                    size="sm"
                    footer={
                        <>
                            <Button variant="ghost" onClick={() => setRiskTarget(null)}>Cancel</Button>
                            <Button variant="destructive" onClick={confirmRiskyInstall}>
                                {riskTarget.updatePluginId ? 'Update anyway' : 'Install anyway'}
                            </Button>
                        </>
                    }
                >
                    <div className="extension-risk-dialog">
                        <p>
                            {riskTarget.reason === 'unverified'
                                ? 'This extension has no pinned checksum, so the panel '
                                  + 'cannot verify the artifact it would download.'
                                : riskTarget.reason === 'untrusted_key'
                                    ? 'This extension is signed, but the publisher key is not '
                                      + 'pinned by this panel, so the signature cannot be '
                                      + 'verified. Treat it as unsigned.'
                                    : riskTarget.reason === 'unsigned'
                                        ? 'This release is not signed with a publisher key pinned '
                                          + 'by this panel, so its origin cannot be verified. It '
                                          + 'may be a legitimate older release — or a downgrade attempt.'
                                        : 'This is a community extension whose exact code has not '
                                          + 'been reviewed by the ServerKit maintainers.'}
                        </p>
                        <p className="text-muted">
                            It runs with full panel privileges. Only install it if you trust
                            the author.
                        </p>
                    </div>
                </Modal>
            )}

            {configTarget && (
                <PluginConfigDialog
                    plugin={configTarget}
                    onClose={() => setConfigTarget(null)}
                />
            )}
        </div>
    );
};

// Cover chrome differs by artwork: illustrated raster icons get a clean light
// tile (their 3D art is drawn for a light backdrop and fills the space), while
// glyph / Simple-Icons brand-mark fallbacks keep the deterministic gradient so
// the white mark stays legible. `base` is the cover class ('extension-card__cover'
// or 'extension-detail__cover').
const coverProps = (base, entry, category) => {
    if (resolveExtensionIcon(entry.installKey, category)) {
        return { className: `${base} ${base}--icon` };
    }
    return { className: base, style: extensionCoverStyle(entry.installKey, category) };
};

const SectionHeader = ({ kicker, title, meta }) => (
    <div className="marketplace-section__header">
        <div>
            <p className="marketplace-kicker">{kicker}</p>
            <h2>{title}</h2>
        </div>
        {meta && <Badge variant="outline">{meta}</Badge>}
    </div>
);

// Cover artwork with a deterministic fallback chain:
// registry logo image -> bundled illustrated icon -> Simple Icons brand mark ->
// manifest icon SVG -> category lucide glyph. Kept as its own component so both
// the card and the detail modal share the exact same resolution order.
const ExtensionCover = ({ entry, category, brandSize = 34 }) => {
    const [logoFailed, setLogoFailed] = useState(false);
    const Icon = getCategoryIcon(category);
    const iconSvg = entry.icon ? sanitizeSvgInner(entry.icon) : '';
    const rasterIcon = resolveExtensionIcon(entry.installKey, category);

    if (entry.logo && !logoFailed) {
        return (
            <img
                src={entry.logo}
                loading="lazy"
                alt=""
                className="extension-card__logo"
                onError={() => setLogoFailed(true)}
            />
        );
    }
    if (rasterIcon) {
        return (
            <img
                src={rasterIcon}
                loading="lazy"
                alt=""
                aria-hidden="true"
                className="extension-card__icon"
            />
        );
    }
    if (hasBrandMark(entry.installKey)) {
        return (
            <ExtensionBrandMark
                slug={entry.installKey}
                size={brandSize}
                className="extension-card__brand"
            />
        );
    }
    if (iconSvg) {
        return (
            <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                aria-hidden="true"
                focusable="false"
                className="extension-card__glyph"
                /* sink-safe: iconSvg = sanitizeSvgInner(entry.icon) above */
                dangerouslySetInnerHTML={{ __html: iconSvg }}
            />
        );
    }
    return <Icon aria-hidden="true" className="extension-card__glyph" />;
};

const CatalogExtensionCard = ({ entry, installing, onInstall, onOpenDetail, statusVariant }) => {
    const category = entry.category || 'utility';
    const isLocal = entry.source === 'local';
    const installedLabel = entry.status && entry.status !== 'active'
        ? titleCase(entry.status)
        : 'Installed';

    const openDetail = () => onOpenDetail(entry);
    const handleKeyDown = (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openDetail();
        }
    };

    return (
        <article
            className={`extension-card extension-card--${entry.source} extension-card--${category} extension-card--clickable card${entry.featured ? ' extension-card--featured' : ''}`}
            role="button"
            tabIndex={0}
            onClick={openDetail}
            onKeyDown={handleKeyDown}
        >
            <div {...coverProps('extension-card__cover', entry, category)}>
                <ExtensionCover entry={entry} category={category} brandSize={34} />
                {entry.featured && (
                    <span className="extension-featured-badge">
                        <Star aria-hidden="true" /> Featured
                    </span>
                )}
            </div>
            <div className="extension-card__badges">
                <Badge variant={sourceBadgeVariant(entry.source)}>{entry.sourceLabel}</Badge>
                <Badge variant="outline">{titleCase(category)}</Badge>
            </div>
            <div className="extension-card__body">
                <h3>{entry.displayName}</h3>
                <p className="extension-card__desc">{entry.description}</p>
            </div>
            <div className="extension-card__footer">
                <div className="extension-card__info">
                    <span>v{entry.version}</span>
                    {entry.firstParty ? (
                        <Badge variant="secondary" className="extension-firstparty">by ServerKit</Badge>
                    ) : (
                        entry.author && <span>by {entry.author}</span>
                    )}
                    <TrustBadge entry={entry} />
                </div>
                <div className="extension-card__actions">
                    {entry.installed ? (
                        <Badge variant={isLocal ? statusVariant(entry.status) : 'success'}>
                            <CheckCircle2 aria-hidden="true" />
                            {installedLabel}
                        </Badge>
                    ) : (
                        <Button
                            size="sm"
                            disabled={installing}
                            onClick={(event) => {
                                event.stopPropagation();
                                onInstall(entry.installKey);
                            }}
                        >
                            <DownloadCloud aria-hidden="true" />
                            {installing ? 'Installing...' : 'Install'}
                        </Button>
                    )}
                </div>
            </div>
        </article>
    );
};

const ExtensionDetailModal = ({ entry, installing, statusVariant, onClose, onInstall }) => {
    const category = entry.category || 'utility';
    const isLocal = entry.source === 'local';
    const screenshots = entry.screenshots || [];
    const permissions = Array.isArray(entry.permissions) ? entry.permissions : [];
    const configKeys = entry.configSchema && typeof entry.configSchema === 'object'
        ? Object.keys(entry.configSchema)
        : [];
    const installedLabel = entry.status && entry.status !== 'active'
        ? titleCase(entry.status)
        : 'Installed';

    return (
        <Modal open onClose={onClose} title={entry.displayName} size="lg">
            <div className="extension-detail">
                <div {...coverProps('extension-detail__cover', entry, category)}>
                    <ExtensionCover entry={entry} category={category} brandSize={46} />
                </div>
                <div className="extension-detail__header">
                    <div className="extension-detail__heading">
                        <div className="extension-detail__badges">
                            {entry.firstParty && (
                                <Badge variant="secondary" className="extension-firstparty">by ServerKit</Badge>
                            )}
                            <TrustBadge entry={entry} />
                            <SignatureBadge entry={entry} />
                            <Badge variant={sourceBadgeVariant(entry.source)}>{entry.sourceLabel}</Badge>
                            <Badge variant="outline">{titleCase(category)}</Badge>
                        </div>
                        <div className="extension-detail__meta">
                            <span>v{entry.version}</span>
                            {entry.author && <span>by {entry.author}</span>}
                            <span>{isLocal ? 'built-in' : entry.extensionType}</span>
                            {entry.repo && (
                                <a
                                    href={entry.repo}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="extension-detail__repo-link"
                                >
                                    <ExternalLink aria-hidden="true" />
                                    Source repo
                                </a>
                            )}
                        </div>
                    </div>
                </div>

                <p className="extension-detail__desc">{entry.description}</p>

                {permissions.length > 0 && (
                    <div className="extension-detail__consent">
                        <p className="extension-detail__section-label">This extension requests:</p>
                        <div className="extension-detail__chips">
                            {permissions.map((permission) => (
                                <Badge key={permission} variant="outline">{permission}</Badge>
                            ))}
                        </div>
                    </div>
                )}

                {configKeys.length > 0 && (
                    <div className="extension-detail__config">
                        <p className="extension-detail__section-label">Configuration</p>
                        <ul className="extension-detail__config-list">
                            {configKeys.map((key) => (
                                <li key={key}><code>{key}</code></li>
                            ))}
                        </ul>
                    </div>
                )}

                {screenshots.length > 0 && (
                    <div className="extension-detail__gallery" aria-label="Screenshots">
                        {screenshots.map((src, index) => (
                            <img
                                key={src}
                                src={src}
                                alt={`${entry.displayName} screenshot ${index + 1}`}
                                className="extension-detail__shot"
                                loading="lazy"
                            />
                        ))}
                    </div>
                )}

                <div className="extension-detail__actions">
                    {entry.installed ? (
                        <Badge variant={isLocal ? statusVariant(entry.status) : 'success'}>
                            <CheckCircle2 aria-hidden="true" />
                            {installedLabel}
                        </Badge>
                    ) : (
                        <Button disabled={installing} onClick={onInstall}>
                            <DownloadCloud aria-hidden="true" />
                            {installing ? 'Installing...' : 'Install'}
                        </Button>
                    )}
                </div>
            </div>
        </Modal>
    );
};

// Install-origin badge for installed rows. url/local/upload all collapse to
// "Manual" — the raw origin lives in the tooltip.
const PLUGIN_SOURCE_BADGES = {
    builtin: { label: 'Built-in', variant: 'warning' },
    registry: { label: 'Registry', variant: 'info' },
};

const PluginRow = ({ plugin, update, busy, onToggle, onUpdate, onUninstall, onConfigure, statusVariant }) => {
    const updateAvailable = Boolean(update?.update_available);
    const compatible = update?.compatible !== false;
    // A row action is in flight — disable every action on this row so it can't
    // be double-fired, and surface which one via the button copy.
    const isBusy = Boolean(busy);
    const configurable = plugin.config_schema
        && typeof plugin.config_schema === 'object'
        && Object.keys(plugin.config_schema).length > 0;
    const sourceBadge = PLUGIN_SOURCE_BADGES[plugin.source_type]
        || { label: 'Manual', variant: 'outline', title: plugin.source_url || undefined };

    return (
        <article className={`installed-item installed-item--plugin card ${plugin.status === 'error' ? 'installed-item--error' : ''}`}>
            <div className="installed-item__main">
                <div className="installed-item__icon installed-item__icon--plugin">
                    <PlugZap aria-hidden="true" />
                </div>
                <div className="installed-item__content">
                    <div className="installed-item__title-line">
                        <strong>{plugin.display_name}</strong>
                        <span className="text-muted">v{plugin.version}</span>
                        <Badge variant={statusVariant(plugin.status)}>{plugin.status}</Badge>
                        <Badge variant={sourceBadge.variant} title={sourceBadge.title}>{sourceBadge.label}</Badge>
                        {plugin.has_backend && <Badge variant="secondary">Backend</Badge>}
                        {plugin.has_frontend && <Badge variant="secondary">Frontend</Badge>}
                        {plugin.restart_required && (
                            <Badge
                                variant="warning"
                                title="This extension's API is not mounted yet — restart the panel to activate it"
                            >
                                Restart required
                            </Badge>
                        )}
                        {updateAvailable && (
                            <Badge variant="info" className="plugin-update-badge">
                                Update available → v{update.available_version}
                            </Badge>
                        )}
                    </div>
                    {plugin.description && <p className="installed-item__description">{plugin.description}</p>}
                    {plugin.error_message && <p className="installed-item__error">{plugin.error_message}</p>}
                    {plugin.restart_required && (
                        <p className="installed-item__notice">
                            Installed, but its backend routes are not served yet. Restart the panel
                            to activate them — until then this extension&apos;s API returns 404.
                        </p>
                    )}
                </div>
            </div>
            <div className="installed-item__actions">
                {updateAvailable && (
                    <Button
                        size="sm"
                        disabled={!compatible || isBusy}
                        title={compatible ? undefined : 'Panel version is too old for this update'}
                        onClick={() => onUpdate(plugin.id)}
                    >
                        <DownloadCloud aria-hidden="true" />
                        {busy === 'update' ? 'Updating…' : 'Update'}
                    </Button>
                )}
                {configurable && (
                    <Button size="sm" variant="outline" disabled={isBusy} onClick={() => onConfigure(plugin)}>
                        Configure
                    </Button>
                )}
                <Button
                    size="sm"
                    variant={plugin.status === 'active' ? 'outline' : 'default'}
                    disabled={isBusy}
                    onClick={() => onToggle(plugin)}
                >
                    {busy === 'enable' ? 'Enabling…'
                        : busy === 'disable' ? 'Disabling…'
                        : plugin.status === 'active' ? 'Disable' : 'Enable'}
                </Button>
                <Button size="sm" variant="destructive" disabled={isBusy} onClick={() => onUninstall(plugin)}>
                    {busy === 'uninstall' ? 'Uninstalling…' : 'Uninstall'}
                </Button>
            </div>
        </article>
    );
};

// Config editor for an installed plugin (#49). Fields come from the manifest's
// config_schema (top-level keys, or JSON-schema `properties`); values persist
// via PUT /plugins/<id>/config and the plugin reads them on the backend via
// plugins_sdk.config(slug).
const PluginConfigDialog = ({ plugin, onClose }) => {
    const toast = useToast();
    const [values, setValues] = useState(null);
    const [saving, setSaving] = useState(false);

    const schema = plugin.config_schema || {};
    const fields = schema.properties && typeof schema.properties === 'object'
        ? schema.properties
        : schema;

    useEffect(() => {
        api.getPluginConfig(plugin.id)
            .then((data) => setValues(data.config || {}))
            .catch(() => setValues({}));
    }, [plugin.id]);

    const setField = (key, v) => setValues((prev) => ({ ...prev, [key]: v }));

    const save = async () => {
        setSaving(true);
        try {
            await api.updatePluginConfig(plugin.id, values || {});
            toast.success('Extension configuration saved');
            onClose();
        } catch (err) {
            toast.error(err.message || 'Failed to save configuration');
        } finally {
            setSaving(false);
        }
    };

    return (
        <Modal
            open
            onClose={onClose}
            title={`Configure ${plugin.display_name}`}
            size="sm"
            footer={
                <>
                    <Button variant="ghost" onClick={onClose}>Cancel</Button>
                    <Button onClick={save} disabled={saving || values === null}>
                        {saving ? 'Saving…' : 'Save'}
                    </Button>
                </>
            }
        >
            {values === null ? (
                <p className="text-muted">Loading…</p>
            ) : (
                <div className="plugin-config-form">
                    {Object.entries(fields).map(([key, spec]) => {
                        const s = spec && typeof spec === 'object' ? spec : {};
                        const type = s.type || 'string';
                        const isNumber = type === 'number' || type === 'integer';
                        const value = values[key] ?? s.default ?? (type === 'boolean' ? false : '');
                        return (
                            <label key={key} className="plugin-config-form__field">
                                <span className="plugin-config-form__label">{s.title || key}</span>
                                {type === 'boolean' ? (
                                    <input
                                        type="checkbox"
                                        checked={Boolean(value)}
                                        onChange={(e) => setField(key, e.target.checked)}
                                    />
                                ) : Array.isArray(s.enum) ? (
                                    <select
                                        className="ui-input"
                                        value={value}
                                        onChange={(e) => setField(key, e.target.value)}
                                    >
                                        {s.enum.map((opt) => (
                                            <option key={opt} value={opt}>{opt}</option>
                                        ))}
                                    </select>
                                ) : (
                                    <input
                                        className="ui-input"
                                        type={isNumber ? 'number' : (s.secret ? 'password' : 'text')}
                                        value={value}
                                        onChange={(e) => setField(
                                            key,
                                            isNumber
                                                ? (e.target.value === '' ? '' : Number(e.target.value))
                                                : e.target.value
                                        )}
                                    />
                                )}
                                {s.description && (
                                    <span className="plugin-config-form__hint text-muted">{s.description}</span>
                                )}
                            </label>
                        );
                    })}
                    {Object.keys(fields).length === 0 && (
                        <p className="text-muted">This extension declares no configuration fields.</p>
                    )}
                </div>
            )}
        </Modal>
    );
};

// Data-policy dialog for plugin uninstall. Keeping data (default) leaves the
// extension's tables intact for a later reinstall; purging drops them.
const PluginUninstallDialog = ({ plugin, onCancel, onConfirm }) => (
    <Modal
        open
        onClose={onCancel}
        title={`Uninstall ${plugin.display_name}?`}
        size="sm"
        footer={
            <>
                <Button variant="ghost" onClick={onCancel}>Cancel</Button>
                <Button variant="outline" onClick={() => onConfirm(false)}>Keep data</Button>
                <Button variant="destructive" onClick={() => onConfirm(true)}>Purge data</Button>
            </>
        }
    >
        <div className="plugin-uninstall-dialog">
            <p>Removing this extension stops its routes and UI contributions.</p>
            <p className="text-muted">
                <strong>Keep data</strong> leaves the extension&apos;s database tables intact so you can
                reinstall later. <strong>Purge data</strong> permanently drops the extension&apos;s tables
                and cannot be undone.
            </p>
        </div>
    </Modal>
);

export default Marketplace;
