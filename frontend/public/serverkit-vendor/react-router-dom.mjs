// Runtime-extension shim for `react-router-dom` — re-exports the HOST instance,
// so an extension's <Link>/useNavigate() drive the panel's own router rather
// than a second copy with its own (empty) history.
//
// Covers the router surface an extension realistically reaches for. If one needs
// an export that isn't here, add it: a missing name fails at load with
// "does not provide an export named ...", not silently.
const m = (globalThis.__SK_VENDOR__ || {})['react-router-dom'];
if (!m) {
    throw new Error('[serverkit] host react-router-dom unavailable — vendorShare did not run');
}
export default m.default ?? m;

// Navigation hooks
export const useNavigate = m.useNavigate;
export const useLocation = m.useLocation;
export const useParams = m.useParams;
export const useSearchParams = m.useSearchParams;
export const useHref = m.useHref;
export const useMatch = m.useMatch;
export const useMatches = m.useMatches;
export const useResolvedPath = m.useResolvedPath;
export const useNavigationType = m.useNavigationType;
export const useInRouterContext = m.useInRouterContext;
export const useOutlet = m.useOutlet;
export const useOutletContext = m.useOutletContext;
export const useRoutes = m.useRoutes;
export const useRouteError = m.useRouteError;

// Components
export const Link = m.Link;
export const NavLink = m.NavLink;
export const Navigate = m.Navigate;
export const Outlet = m.Outlet;
export const Route = m.Route;
export const Routes = m.Routes;
export const Router = m.Router;
export const BrowserRouter = m.BrowserRouter;
export const HashRouter = m.HashRouter;
export const MemoryRouter = m.MemoryRouter;

// Path helpers
export const generatePath = m.generatePath;
export const matchPath = m.matchPath;
export const matchRoutes = m.matchRoutes;
export const resolvePath = m.resolvePath;
export const createSearchParams = m.createSearchParams;
export const isRouteErrorResponse = m.isRouteErrorResponse;
