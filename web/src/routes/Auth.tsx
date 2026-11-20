import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { Me } from "../lib/types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), retry: false, staleTime: 60_000 });
}

export function Home() {
  const me = useMe();
  const nav = useNavigate();
  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) nav({ to: "/login", search: { next: undefined } });
    if (me.data) {
      const last = localStorage.getItem("pfn.ws");
      const slugs = me.data.workspaces.map((w) => w.slug);
      const team = me.data.workspaces.find((w) => w.kind === "team")?.slug;
      const target = last && slugs.includes(last) ? last : (team ?? slugs[0]);
      if (target) nav({ to: "/w/$ws", params: { ws: target }, search: {} });
    }
  }, [me.data, me.error, nav]);
  return null;
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="card w-[300px] p-5">
        <div className="mb-4 text-[15px] font-semibold tracking-tight">portofion</div>
        {children}
      </div>
    </div>
  );
}

export function Login() {
  const { next } = useSearch({ from: "/login" });
  const nav = useNavigate();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const me = await api<Me>("/auth/login", { body: { email, password: pw } });
      qc.setQueryData(["me"], me);
      if (next && next.startsWith("/")) window.location.assign(next);
      else nav({ to: "/" });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Frame>
      <form onSubmit={submit} className="flex flex-col gap-2">
        <input className="ctl h-8" placeholder="Email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        <input className="ctl h-8" placeholder="Password" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <div className="text-crit">{err}</div>}
        <button className="btn btn-primary h-8" disabled={busy || !email || !pw}>
          Sign in
        </button>
      </form>
    </Frame>
  );
}

export function Invite() {
  const { token } = useParams({ from: "/invite/$token" });
  const me = useMe();
  const qc = useQueryClient();
  const nav = useNavigate();
  const info = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api<{ workspace: string; workspace_slug: string; role: string; email: string | null }>(`/invites/${token}`),
    retry: false,
  });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (info.data?.email) setEmail(info.data.email);
  }, [info.data?.email]);
  async function accept(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    try {
      const body = me.data ? {} : { email, password: pw, name };
      const r = await api<{ workspace: string; me: Me }>(`/invites/${token}/accept`, { body });
      qc.setQueryData(["me"], r.me);
      nav({ to: "/w/$ws", params: { ws: r.workspace }, search: {} });
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }
  if (info.error) return <Frame><div className="text-crit">{(info.error as Error).message}</div></Frame>;
  if (!info.data) return null;
  return (
    <Frame>
      <form onSubmit={accept} className="flex flex-col gap-2">
        <div className="mb-1 flex items-baseline justify-between">
          <span className="font-semibold">{info.data.workspace}</span>
          <span className="text-muted">{info.data.role}</span>
        </div>
        {me.data ? (
          <div className="text-ink2">{me.data.email}</div>
        ) : (
          <>
            <input className="ctl h-8" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} disabled={!!info.data.email} />
            <input className="ctl h-8" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
            <input className="ctl h-8" placeholder="Password (8+)" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
          </>
        )}
        {err && <div className="text-crit">{err}</div>}
        <button className="btn btn-primary h-8">Join</button>
      </form>
    </Frame>
  );
}
