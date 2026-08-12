/**
 * Plugin contribution loader.
 *
 * Source of truth for what an installed plugin contributes to the host UI:
 *   - sidebar items
 *   - SPA routes
 *   - page titles
 *   - command-palette entries
 *   - global widgets
 *
 * The backend exposes the merged contribution envelope at
 * /api/v1/plugins/contributions. Each contribution carries the source
 * `plugin` slug; we resolve `component` strings against the plugin's
 * own index module (discovered at build time via import.meta.glob).
 *
 * Build-time discovery means a freshly installed plugin's frontend code
 * still requires `npm run build` to ship — that's the existing constraint
 * of the plugin system, not new here. Contribution metadata is dynamic
 * though, so toggling plugins on/off updates the UI without a rebuild.
 */
import { useEffect, useMemo, useState } from 'react';
import api from '../services/api';
import pluginsManifest from './plugins-manifest.json';
import { loadRuntimeFrontends, getRuntimeModule } from './runtime/loader';

// Discover every plugin module at build time. Each plugin is expected to
// expose its components from src/plugins/<slug>/index.{js,jsx}.
const pluginModules = import.meta.glob('../plugins/*/index.{js,jsx}', { eager: true });
const pluginManifestModules = import.meta.glob('../plugins/*/plugin.json', { eager: true });

function slugFromPluginPath(path, filenamePattern) {
    const m = path.match(new RegExp(`(?:^\\./|/plugins/)([^/]+)/${filenamePattern}$`));
    return m ? m[1] : null;
}

const moduleBySlug = (() => {
    const out = {};
    for (const [path, mod] of Object.entries(pluginModules)) {
        const slug = slugFromPluginPath(path, 'index\\.(?:js|jsx)');
        if (!slug || slug === 'sdk') continue;
        out[slug] = mod;
    }
    return out;
})();

const localManifestBySlug = (() => {
    const out = {};
    for (const [path, mod] of Object.entries(pluginManifestModules)) {
        const slug = slugFromPluginPath(path, 'plugin\\.json');
        if (!slug) continue;
        out[slug] = mod.default || mod;
    }
    return out;
})();

export function getPluginModule(slug) {
    // Build-time (baked) module first, then a runtime-loaded bundle (Decision 4).
    return moduleBySlug[slug] || getRuntimeModule(slug) || null;
}

// Resolve a contribution's `component` string to an actual React component.
// `component` may be:
//   - "default"  → mod.default
//   - "Foo"      → mod.Foo (named export)
// Returns null if no match; caller decides whether to skip + warn.
export function resolveComponent(slug, name) {
    // Baked module (build-time glob) first; fall back to a runtime-loaded
    // bundle's namespace (Decision 4). Same name-matching for both.
    const mod = moduleBySlug[slug] || getRuntimeModule(slug);
    if (!mod) return null;
    if (!name || name === 'default') return mod.default || null;
    return mod[name] || null;
}

const EMPTY = {
    nav: [],
    routes: [],
    page_titles: {},
    command_palette: [],
    widgets: [],
    // Placeable dashboard widget TYPES: { id, name, component, icon?,
    // category?, description?, w?, h?, min?, default_cfg? }. Distinct from
    // `widgets` above, which mounts a component at a fixed host slot. These
    // show up in the dashboard widget library and the user positions, sizes
    // and configures instances of them. Consumed by
    // components/dashboard/widgets/registry.js via useWidgetTypes().
    dashboard_widgets: [],
    layouts: [],
    // Tabs contributed into core-owned TabGroupLayout groups (#43):
    // { group, to, label, icon?, end?, order? }. `group` is the core group id
    // (== the sidebar item id: files | servers | monitoring). Consumed by
    // TabGroupLayout (tab strip merge) + Sidebar (group item stays lit).
    tabs: [],
    // AI assistant contributions: per-route suggested prompts + custom
    // tool-result renderers. Consumed by the core AIAssistant via
    // useContributions().ai.
    ai: { suggested_prompts: [], tool_renderers: [] },
    // Runtime-frontend descriptor map { slug: { entry, hashes } } (plan 25).
    // Present only for installed extensions shipping a prebuilt ESM bundle;
    // empty for baked builtins and when the kill switch is off.
    frontends: {},
};

// Merge a raw contribution envelope onto the empty shape so every
// consumer sees the full set of keys. Extension-specific values (nav,
// routes, titles, palette entries) now come entirely from each plugin's
// manifest — there are no host-side compatibility rewrites here.
function normalizeContributions(value) {
    return { ...EMPTY, ...(value || {}) };
}

function tagItems(items, slug) {
    return (items || [])
        .filter((item) => item && typeof item === 'object')
        .map((item) => ({ ...item, plugin: slug }));
}

function getBuildTimeContributions() {
    const installed = Array.isArray(pluginsManifest?.plugins)
        ? pluginsManifest.plugins
        : [];

    const nav = [];
    const routes = [];
    const page_titles = {};
    const command_palette = [];
    const widgets = [];
    const dashboard_widgets = [];
    const layouts = [];
    const tabs = [];
    const ai = { suggested_prompts: [], tool_renderers: [] };

    for (const entry of installed) {
        const slug = entry?.slug || entry?.name;
        if (!slug) continue;

        const manifest = localManifestBySlug[slug];
        const contrib = manifest?.contributions;
        if (!contrib || typeof contrib !== 'object') continue;

        nav.push(...tagItems(contrib.nav, slug));
        routes.push(...tagItems(contrib.routes, slug));
        command_palette.push(...tagItems(contrib.command_palette, slug));
        widgets.push(...tagItems(contrib.widgets, slug));
        dashboard_widgets.push(...tagItems(contrib.dashboard_widgets, slug));
        layouts.push(...tagItems(contrib.layouts, slug));
        tabs.push(...tagItems(contrib.tabs, slug));

        if (contrib.ai && typeof contrib.ai === 'object') {
            ai.suggested_prompts.push(...tagItems(contrib.ai.suggested_prompts, slug));
            ai.tool_renderers.push(...tagItems(contrib.ai.tool_renderers, slug));
        }

        if (contrib.page_titles && typeof contrib.page_titles === 'object') {
            Object.assign(page_titles, contrib.page_titles);
        }
    }

    return {
        nav,
        routes,
        page_titles,
        command_palette,
        widgets,
        dashboard_widgets,
        layouts,
        tabs,
        ai,
    };
}

// Built-in layout ids. These are reserved — a plugin can't redefine them.
//   padded  → routes go inside DashboardLayout, normal padding (default)
//   full    → routes go inside DashboardLayout, no padding (FULL_PAGE_ROUTES)
//   bare    → routes go OUTSIDE DashboardLayout, just the page + auth guard
export const BUILTIN_LAYOUTS = new Set(['padded', 'full', 'bare']);

// True iff this layout is rendered inside the dashboard chrome. The
// other case (bare + custom plugin layouts) gets its own top-level
// route tree in App.jsx.
export function isInsideDashboard(layoutId) {
    if (!layoutId || layoutId === 'padded' || layoutId === 'full') return true;
    return false;
}

// Resolve a custom (plugin-contributed) layout id to a React component.
// Built-ins return null — App.jsx handles them directly. Returns null
// if the id is unknown or the referenced component can't be found.
export function resolveCustomLayout(layoutId, layouts) {
    if (!layoutId || BUILTIN_LAYOUTS.has(layoutId)) return null;
    const decl = (layouts || []).find((l) => l && l.id === layoutId);
    if (!decl) return null;
    return resolveComponent(decl.plugin, decl.component);
}

let cachedPromise = null;
let cachedValue = null;
const subscribers = new Set();

function notify(value) {
    cachedValue = value;
    for (const cb of subscribers) {
        try { cb(value); } catch { /* swallow subscriber errors */ }
    }
}

export function refreshContributions() {
    cachedPromise = api.getPluginContributions()
        .then(async (data) => {
            const merged = normalizeContributions({ ...EMPTY, ...(data || {}) });
            // Load any runtime ESM bundles BEFORE notifying, so contributed
            // routes render with their components already available (fail-soft:
            // a bundle that can't load records an error state, never throws).
            // Pass the panel's SDK version so the loader can refuse a bundle
            // built against an incompatible SDK before fetching it (plan 32 #1).
            await loadRuntimeFrontends(
                merged.frontends, merged.panel_sdk_version || merged.sdk_version);
            // Mark the envelope as loaded so consumers (e.g. the NotFound page)
            // can tell "contributions still loading" from "genuinely no route".
            merged.__ready = true;
            notify(merged);
            return merged;
        })
        .catch(() => {
            // If the backend contribution endpoint is unavailable (common
            // while running only the Vite dev server), use the active plugin
            // manifest baked into this frontend build instead of leaving
            // contributed routes blank.
            const fallback = normalizeContributions(getBuildTimeContributions());
            fallback.__ready = true;
            notify(fallback);
            return fallback;
        });
    return cachedPromise;
}

export function useContributions() {
    const [value, setValue] = useState(cachedValue || EMPTY);

    useEffect(() => {
        subscribers.add(setValue);
        if (!cachedPromise) refreshContributions();
        else if (cachedValue) setValue(cachedValue);
        return () => subscribers.delete(setValue);
    }, []);

    return value;
}

/**
 * Is the extension `slug` present on this panel?
 *
 * Core UI sometimes fronts an API that an extension owns (a Connections card for
 * /api/v1/email/*, a server-detail tab for /api/v1/tunnels/*). Gating those on
 * anything other than the extension itself — an agent capability, a settings flag
 * — leaves the control visible on a panel where its route does not exist, and the
 * operator gets a bare "Request failed (405)" from the SPA catch-all with nothing
 * naming the real cause. Gate on this instead.
 *
 * Returns TRUE until contributions have loaded (`__ready`), so a control never
 * flickers away and back on first paint. Callers that must not act on a
 * false-positive should check `ready` as well.
 */
export function useHasPlugin(slug) {
    const contributions = useContributions();

    return useMemo(() => {
        if (!contributions.__ready) return true;
        if (!slug) return false;
        for (const bucket of [
            contributions.routes, contributions.nav, contributions.tabs,
            contributions.widgets, contributions.command_palette, contributions.layouts,
        ]) {
            for (const item of bucket || []) {
                if (item && item.plugin === slug) return true;
            }
        }
        return Object.prototype.hasOwnProperty.call(contributions.frontends || {}, slug);
    }, [contributions, slug]);
}
