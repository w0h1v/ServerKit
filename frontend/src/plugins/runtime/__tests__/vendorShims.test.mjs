// Proving test for the runtime-extension import map (plan 25 Decision 2).
//
// vite.config.js injects a static import map mapping each externalized bare
// specifier to a `/serverkit-vendor/<name>.mjs` shim served from public/. If a
// mapped file is MISSING, nothing fails at build time: nginx answers the URL
// with index.html via its SPA try_files fallback, and the failure only shows up
// in the browser as "Expected a JavaScript-or-Wasm module script but the server
// responded with a MIME type of text/html" — one console line, per extension,
// at runtime. That is how `react.mjs` and `react-router-dom.mjs` stayed missing
// while the map advertised them, breaking every runtime-loaded extension.
//
// This asserts the map and the shim directory agree, in both directions.
//
// Run: node --test src/plugins/runtime/__tests__/vendorShims.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

const viteConfigPath = new URL('../../../../vite.config.js', import.meta.url);
const shimDir = new URL('../../../../public/serverkit-vendor/', import.meta.url);

// Read vite.config.js as TEXT rather than importing it — importing pulls in the
// vite/plugin-react toolchain, which this dependency-free test must not need.
const mappedFiles = (() => {
    const src = readFileSync(viteConfigPath, 'utf8');
    const names = [...src.matchAll(/['"]\/serverkit-vendor\/([A-Za-z0-9._-]+\.mjs)['"]/g)]
        .map((m) => m[1]);
    return [...new Set(names)];
})();

const shimFiles = readdirSync(shimDir).filter((f) => f.endsWith('.mjs'));

test('the import map is not empty (the regex still matches the config)', () => {
    assert.ok(
        mappedFiles.length > 0,
        'no /serverkit-vendor/*.mjs entries found in vite.config.js — if the map moved, '
        + 'update this test rather than deleting it',
    );
});

test('every import-map entry has a shim file on disk', () => {
    const missing = mappedFiles.filter((f) => !shimFiles.includes(f));
    assert.deepEqual(
        missing, [],
        `import map advertises shims that do not exist in public/serverkit-vendor/: ${missing.join(', ')}`,
    );
});

test('every shim file is reachable through the import map', () => {
    const orphaned = shimFiles.filter((f) => !mappedFiles.includes(f));
    assert.deepEqual(
        orphaned, [],
        `shim files nothing maps to (dead weight, or a missing import-map entry): ${orphaned.join(', ')}`,
    );
});

test('each shim re-exports the host instance instead of importing the package', () => {
    // A shim that `import`s react directly would hand the extension a SECOND
    // React copy — the "Invalid hook call" crash vendorShare.js exists to avoid.
    for (const f of shimFiles) {
        const src = readFileSync(new URL(f, shimDir), 'utf8');
        assert.match(
            src, /globalThis\.__SK_VENDOR__/,
            `${f} does not read globalThis.__SK_VENDOR__`,
        );
        assert.doesNotMatch(
            src, /^\s*import\s/m,
            `${f} has a static import — it must re-export the host namespace, not load its own copy`,
        );
    }
});
