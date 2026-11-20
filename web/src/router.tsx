import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { Home, Invite, Login } from "./routes/Auth";
import { Compare } from "./routes/Compare";
import { Library } from "./routes/Library";
import { PortfolioPage } from "./routes/Portfolio";
import { Portfolios } from "./routes/Portfolios";
import { Settings } from "./routes/Settings";
import { StrategyPage } from "./routes/Strategy";
import { WsLayout } from "./routes/WsLayout";

const str = (v: unknown) => (typeof v === "string" && v ? v : undefined);

const rootRoute = createRootRoute({ component: () => <Outlet /> });

const indexRoute = createRoute({ getParentRoute: () => rootRoute, path: "/", component: Home });
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: (s: Record<string, unknown>): { next?: string } => ({ next: str(s.next) }),
  component: Login,
});
const inviteRoute = createRoute({ getParentRoute: () => rootRoute, path: "/invite/$token", component: Invite });

export const wsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/w/$ws", component: WsLayout });

export interface LibSearch {
  q?: string;
  tag?: string;
  stage?: string;
  window?: string;
  level?: string;
  sort?: string;
  bench?: string;
  arch?: string;
}
export const libraryRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/",
  validateSearch: (s: Record<string, unknown>): LibSearch => ({
    q: str(s.q),
    tag: str(s.tag),
    stage: str(s.stage),
    window: str(s.window),
    level: str(s.level),
    sort: str(s.sort),
    bench: str(s.bench),
    arch: str(s.arch),
  }),
  component: Library,
});

export interface StratSearch {
  b?: string;
  v?: string;
  sel?: string;
  tab?: string;
  win?: string;
}
export const strategyRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/s/$slug",
  validateSearch: (s: Record<string, unknown>): StratSearch => ({
    b: str(s.b),
    v: str(s.v),
    sel: str(s.sel),
    tab: str(s.tab),
    win: str(s.win),
  }),
  component: StrategyPage,
});

export const compareRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/compare",
  validateSearch: (s: Record<string, unknown>): { refs?: string; freq?: string } => ({ refs: str(s.refs), freq: str(s.freq) }),
  component: Compare,
});

export const portfoliosRoute = createRoute({ getParentRoute: () => wsRoute, path: "/p", component: Portfolios });
export const portfolioRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/p/$slug",
  validateSearch: (s: Record<string, unknown>): { refs?: string } => ({ refs: str(s.refs) }),
  component: PortfolioPage,
});

export const settingsRoute = createRoute({
  getParentRoute: () => wsRoute,
  path: "/settings",
  validateSearch: (s: Record<string, unknown>): { tab?: string } => ({ tab: str(s.tab) }),
  component: Settings,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  inviteRoute,
  wsRoute.addChildren([libraryRoute, strategyRoute, compareRoute, portfoliosRoute, portfolioRoute, settingsRoute]),
]);

export const router = createRouter({ routeTree, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
