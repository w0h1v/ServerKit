// Runtime-extension shim for `react` — re-exports the HOST instance.
// Without this file the import map in vite.config.js resolves `react` to a URL
// nginx answers with index.html (try_files fallback), and the browser rejects
// the module on its text/html MIME — taking down every runtime-loaded
// extension that imports React, which is all of them.
const m = (globalThis.__SK_VENDOR__ || {})['react'];
if (!m) {
    throw new Error('[serverkit] host react unavailable — vendorShare did not run');
}
export default m.default ?? m;

// Hooks
export const useState = m.useState;
export const useEffect = m.useEffect;
export const useLayoutEffect = m.useLayoutEffect;
export const useInsertionEffect = m.useInsertionEffect;
export const useMemo = m.useMemo;
export const useCallback = m.useCallback;
export const useRef = m.useRef;
export const useContext = m.useContext;
export const useReducer = m.useReducer;
export const useImperativeHandle = m.useImperativeHandle;
export const useDebugValue = m.useDebugValue;
export const useId = m.useId;
export const useDeferredValue = m.useDeferredValue;
export const useTransition = m.useTransition;
export const useSyncExternalStore = m.useSyncExternalStore;

// Component model
export const Component = m.Component;
export const PureComponent = m.PureComponent;
export const Fragment = m.Fragment;
export const StrictMode = m.StrictMode;
export const Suspense = m.Suspense;
export const Profiler = m.Profiler;
export const Children = m.Children;

// Element / ref helpers
export const createElement = m.createElement;
export const cloneElement = m.cloneElement;
export const isValidElement = m.isValidElement;
export const createContext = m.createContext;
export const createRef = m.createRef;
export const forwardRef = m.forwardRef;
export const memo = m.memo;
export const lazy = m.lazy;
export const startTransition = m.startTransition;
export const version = m.version;
