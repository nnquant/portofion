import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate, useParams, useRouterState } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { Palette as CommandPalette } from "../components/Palette";
import { api, ApiError, ws as wsPath } from "../lib/api";
import { usePalette } from "../lib/colors";
import { WsCtx } from "../lib/ctx";
import type { WsInfo } from "../lib/types";
import { useMe } from "./Auth";

export function WsLayout() {
  const { ws } = useParams({ from: "/w/$ws" });
  const me = useMe();
  const nav = useNavigate();
  const qc = useQueryClient();
  const path = useRouterState({ select: (s) => s.location.pathname });
  const info = useQuery({ queryKey: ["ws", ws], queryFn: () => api<WsInfo>(wsPath(ws)), retry: false });
  const upColor = info.data?.settings.up_color ?? "red";
  const palette = usePalette(upColor);
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const err = me.error ?? info.error;
    if (err instanceof ApiError && err.status === 401) {
      nav({ to: "/login", search: { next: window.location.pathname + window.location.search } });
    }
  }, [me.error, info.error, nav]);

  useEffect(() => {
    document.documentElement.classList.toggle("up-green", upColor === "green");
  }, [upColor]);

  useEffect(() => {
    if (info.data) localStorage.setItem("pfn.ws", ws);
  }, [info.data, ws]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const ctx = useMemo(() => {
    if (!me.data || !info.data) return null;
    const role = info.data.role;
    return { slug: ws, info: info.data, me: me.data, palette, canEdit: role !== "viewer", isAdmin: role === "admin" };
  }, [ws, me.data, info.data, palette]);

  if (info.error && !(info.error instanceof ApiError && info.error.status === 401)) {
    return (
      <div className="p-6 text-crit">
        {(info.error as Error).message} · <Link to="/">home</Link>
      </div>
    );
  }
  if (!ctx) return null;

  const base = `/w/${ws}`;
  const tab = (to: string, label: string, exact = false) => {
    const active = exact ? path === to || path === `${to}/` : path.startsWith(to);
    return (
      <Link
        to={to}
        className={`flex h-full items-center border-b-2 px-2.5 ${active ? "border-accent font-semibold text-ink" : "border-transparent text-ink2 hover:text-ink"}`}
      >
        {label}
      </Link>
    );
  };

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    qc.clear();
    nav({ to: "/login", search: { next: undefined } });
  }

  return (
    <WsCtx.Provider value={ctx}>
      <div className="flex h-full flex-col">
        <header className="grid h-9 flex-none grid-cols-[1fr_auto_1fr] items-stretch gap-3 border-b border-edge bg-surface px-3">
          <div className="flex min-w-0 items-stretch gap-1">
            <Link to="/" className="mr-2 flex items-center text-[13px] font-semibold tracking-tight">
              portofion
            </Link>
            <select
              className="ctl my-auto max-w-[160px] min-w-0"
              value={ws}
              onChange={(e) => nav({ to: "/w/$ws", params: { ws: e.target.value }, search: {} })}
            >
              {me.data!.workspaces.map((w) => (
                <option key={w.slug} value={w.slug}>
                  {w.name}
                </option>
              ))}
            </select>
          </div>
          <nav className="flex items-stretch">
            {tab(base, "Library", true)}
            {tab(`${base}/compare`, "Compare")}
            {tab(`${base}/p`, "Portfolios")}
          </nav>
          <div className="flex min-w-0 items-stretch justify-end gap-1">
            <button className="ctl my-auto flex w-[240px] min-w-0 shrink items-center justify-between text-muted" onClick={() => setPaletteOpen(true)}>
              <span>Search</span>
              <kbd className="text-[10.5px]">Ctrl K</kbd>
            </button>
            {tab(`${base}/settings`, "Settings")}
            <button className="px-2 whitespace-nowrap text-ink2 hover:text-ink" onClick={logout} title={me.data!.email}>
              {me.data!.email.split("@")[0]} ⏻
            </button>
          </div>
        </header>
        <main className="min-h-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
    </WsCtx.Provider>
  );
}
