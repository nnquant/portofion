import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { CopyButton, ErrorLine, Seg } from "../components/bits";
import { api, ws as wsPath } from "../lib/api";
import { useWs } from "../lib/ctx";
import { ago, pct } from "../lib/format";
import type { Role, WsInfo, WsSettings } from "../lib/types";

export function Settings() {
  const { isAdmin } = useWs();
  const search = useSearch({ from: "/w/$ws/settings" });
  const nav = useNavigate({ from: "/w/$ws/settings" });
  const tabs: [string, string][] = [
    ["keys", "API keys"],
    ["members", "Members"],
    ["workspace", "Workspace"],
    ["flags", "Flags"],
    ...(isAdmin ? ([["audit", "Audit"]] as [string, string][]) : []),
  ];
  const tab = search.tab ?? "keys";
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[34px] flex-none items-center border-b border-edge bg-surface pl-2">
        {tabs.map(([k, l]) => (
          <button key={k} className="tab" aria-selected={tab === k} onClick={() => nav({ search: { tab: k === "keys" ? undefined : k }, replace: true })}>
            {l}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {tab === "keys" && <Keys />}
        {tab === "members" && <Members />}
        {tab === "workspace" && <Workspace />}
        {tab === "flags" && <WsFlags />}
        {tab === "audit" && isAdmin && <Audit />}
      </div>
    </div>
  );
}

interface KeyRow {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  created_by: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked: boolean;
}

function Keys() {
  const { slug: ws, canEdit } = useWs();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["keys", ws], queryFn: () => api<KeyRow[]>(wsPath(ws, "/keys")) });
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["ingest", "read"]);
  const [created, setCreated] = useState<{ key: string; name: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const base = window.location.origin;
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api<{ sdk_wheel: string | null }>("/meta"), staleTime: Infinity });
  const create = async () => {
    setErr(null);
    try {
      const r = await api<{ key: string; name: string }>(wsPath(ws, "/keys"), { body: { name, scopes } });
      setCreated(r);
      setName("");
      qc.invalidateQueries({ queryKey: ["keys", ws] });
    } catch (e) {
      setErr(e);
    }
  };
  const revoke = async (id: number) => {
    if (!confirm("Revoke this key? Agents using it stop working immediately.")) return;
    await api(wsPath(ws, `/keys/${id}`), { method: "DELETE" });
    qc.invalidateQueries({ queryKey: ["keys", ws] });
  };
  const [via, setVia] = useState<"sh" | "ps1" | "prompt">(navigator.userAgent.includes("Windows") ? "ps1" : "sh");
  const install = created
    ? {
        sh: `curl -fsSL ${base}/skill/install.sh | PORTOFION_API_KEY=${created.key} sh`,
        ps1: `$env:PORTOFION_API_KEY='${created.key}'; irm ${base}/skill/install.ps1 | iex`,
      }
    : null;
  const snippet = created && install
    ? {
        ...install,
        prompt: `Report daily strategy returns to portofion (${base}, workspace ${ws}).

Install the portofion skill; the installer also saves the API key:
  macOS / Linux:  ${install.sh}
  Windows:        ${install.ps1}
Then use the skill. Without skill support, read ${base}/llms.txt and use:
  PORTOFION_URL=${base}
  PORTOFION_API_KEY=${created.key}${meta.data?.sdk_wheel ? `
  Python SDK: pip install ${base}${meta.data.sdk_wheel}` : ""}`,
      }[via]
    : "";
  return (
    <div className="flex max-w-[1100px] flex-col gap-3">
      {canEdit && (
        <div className="card flex flex-wrap items-center gap-2 p-3">
          <input className="ctl w-[220px]" placeholder="Key name (agent / host)" value={name} onChange={(e) => setName(e.target.value)} />
          {["ingest", "read"].map((s) => (
            <label key={s} className="flex items-center gap-1">
              <input type="checkbox" checked={scopes.includes(s)} onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
              {s}
            </label>
          ))}
          <button className="btn btn-primary" disabled={!name || !scopes.length} onClick={create}>
            Create key
          </button>
          <a className="ml-auto text-accent hover:underline" href="/skill/portofion.zip">
            skill.zip
          </a>
          <a className="text-accent hover:underline" href="/llms.txt" target="_blank" rel="noreferrer">
            llms.txt
          </a>
          <a className="text-accent hover:underline" href="/docs" target="_blank" rel="noreferrer">
            API docs
          </a>
          <ErrorLine error={err} />
        </div>
      )}
      {created && (
        <div className="card flex flex-col gap-2 border-accent p-3" style={{ borderColor: "var(--series-1)" }}>
          <div className="flex items-center gap-2">
            <code className="flex-1 select-all rounded bg-hover px-2 py-1 font-mono text-[12px]">{created.key}</code>
            <CopyButton text={created.key} label="Copy key" />
          </div>
          <div>
            <Seg value={via} options={[["sh", "macOS / Linux"], ["ps1", "Windows"], ["prompt", "Prompt"]]} onChange={setVia} />
          </div>
          <pre className="select-all whitespace-pre-wrap break-all rounded bg-hover p-2 font-mono text-[11.5px] text-ink">{snippet}</pre>
          <div className="flex gap-2">
            <CopyButton text={snippet} className="btn btn-primary" />
            <button className="btn" onClick={() => setCreated(null)}>
              Done
            </button>
          </div>
        </div>
      )}
      <div className="card overflow-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Scopes</th>
              <th>Created by</th>
              <th className="num">Created</th>
              <th className="num">Last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((k) => (
              <tr key={k.id} className={k.revoked ? "text-muted line-through" : ""}>
                <td>{k.name}</td>
                <td className="font-mono text-[11.5px]">{k.prefix}…</td>
                <td>{k.scopes.join(" ")}</td>
                <td className="text-ink2">{k.created_by}</td>
                <td className="num">{k.created_at.slice(0, 10)}</td>
                <td className="num">{ago(k.last_used_at)}</td>
                <td className="num">{!k.revoked && canEdit && <button className="btn text-crit" onClick={() => revoke(k.id)}>Revoke</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Members() {
  const { slug: ws, isAdmin, me } = useWs();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["members", ws],
    queryFn: () => api<{ user_id: number; email: string; name: string; role: Role; since: string }[]>(wsPath(ws, "/members")),
  });
  const inv = useQuery({
    queryKey: ["invites", ws],
    queryFn: () => api<{ id: number; email: string | null; role: Role; expires_at: string; expired: boolean }[]>(wsPath(ws, "/invites")),
    enabled: isAdmin,
  });
  const [role, setRole] = useState<Role>("member");
  const [email, setEmail] = useState("");
  const [link, setLink] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      qc.invalidateQueries({ queryKey: ["members", ws] });
      qc.invalidateQueries({ queryKey: ["invites", ws] });
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <div className="flex max-w-[900px] flex-col gap-3">
      {isAdmin && (
        <div className="card flex flex-wrap items-center gap-2 p-3">
          <input className="ctl w-[220px]" placeholder="Email (optional)" value={email} onChange={(e) => setEmail(e.target.value)} />
          <select className="ctl" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option>member</option>
            <option>viewer</option>
            <option>admin</option>
          </select>
          <button
            className="btn btn-primary"
            onClick={() =>
              act(async () => {
                const r = await api<{ url: string }>(wsPath(ws, "/invites"), { body: { email: email || null, role } });
                setLink(window.location.origin + new URL(r.url).pathname);
                setEmail("");
              })
            }
          >
            Invite link
          </button>
          {link && (
            <>
              <code className="select-all rounded bg-hover px-2 py-1 font-mono text-[11.5px]">{link}</code>
              <CopyButton text={link} />
            </>
          )}
          <ErrorLine error={err} />
        </div>
      )}
      <div className="card overflow-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Role</th>
              <th className="num">Since</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(q.data ?? []).map((m) => (
              <tr key={m.user_id}>
                <td>{m.email}</td>
                <td className="text-ink2">{m.name}</td>
                <td>
                  {isAdmin ? (
                    <select className="ctl" value={m.role} onChange={(e) => act(() => api(wsPath(ws, `/members/${m.user_id}`), { method: "PATCH", body: { role: e.target.value } }))}>
                      <option>admin</option>
                      <option>member</option>
                      <option>viewer</option>
                    </select>
                  ) : (
                    m.role
                  )}
                </td>
                <td className="num">{m.since.slice(0, 10)}</td>
                <td className="num">
                  {(isAdmin || m.user_id === me.id) && (
                    <button className="btn" onClick={() => confirm(`Remove ${m.email}?`) && act(() => api(wsPath(ws, `/members/${m.user_id}`), { method: "DELETE" }))}>
                      {m.user_id === me.id ? "Leave" : "Remove"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {(inv.data ?? []).map((i) => (
              <tr key={`inv${i.id}`} className="text-muted">
                <td>{i.email ?? "invite link"}</td>
                <td />
                <td>{i.role}</td>
                <td className="num">{i.expired ? "expired" : `until ${i.expires_at.slice(0, 10)}`}</td>
                <td className="num">
                  <button className="btn" onClick={() => act(() => api(wsPath(ws, `/invites/${i.id}`), { method: "DELETE" }))}>
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Workspace() {
  const { slug: ws, info, isAdmin } = useWs();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [s, setS] = useState<WsSettings & { name: string }>({ ...info.settings, name: info.name });
  const [err, setErr] = useState<unknown>(null);
  const [ok, setOk] = useState(false);
  const [newWs, setNewWs] = useState({ slug: "", name: "" });
  useEffect(() => setS({ ...info.settings, name: info.name }), [info]);
  const save = async () => {
    setErr(null);
    setOk(false);
    try {
      const r = await api<WsInfo>(wsPath(ws, "/settings"), { method: "PATCH", body: s });
      qc.setQueryData(["ws", ws], r);
      qc.invalidateQueries({ queryKey: ["library", ws] });
      setOk(true);
    } catch (e) {
      setErr(e);
    }
  };
  const num = (k: keyof WsSettings, step = 1) => (
    <input type="number" step={step} className="ctl w-full" disabled={!isAdmin} value={s[k] as number} onChange={(e) => setS({ ...s, [k]: Number(e.target.value) })} />
  );
  const row = "grid grid-cols-[150px_1fr] items-center gap-2";
  return (
    <div className="flex max-w-[900px] flex-col gap-3">
      <div className="card flex w-[440px] flex-col gap-2 p-3">
        <div className={row}>
          <span className="text-ink2">Name</span>
          <input className="ctl" disabled={!isAdmin} value={s.name} onChange={(e) => setS({ ...s, name: e.target.value })} />
        </div>
        <div className={row}>
          <span className="text-ink2">Periods / year</span>
          {num("periods_per_year")}
        </div>
        <div className={row}>
          <span className="text-ink2">Risk-free (annual)</span>
          {num("rf", 0.001)}
        </div>
        <div className={row}>
          <span className="text-ink2">VaR level</span>
          {num("var_level", 0.01)}
        </div>
        <div className={row}>
          <span className="text-ink2">Outlier |r| &gt;</span>
          {num("outlier_abs", 0.05)}
        </div>
        <div className={row}>
          <span className="text-ink2">Stale after (days)</span>
          {num("stale_days")}
        </div>
        <div className={row}>
          <span className="text-ink2">Up color</span>
          <select className="ctl" disabled={!isAdmin} value={s.up_color} onChange={(e) => setS({ ...s, up_color: e.target.value as "red" | "green" })}>
            <option value="red">red up / green down</option>
            <option value="green">green up / red down</option>
          </select>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-2 pt-1">
            <button className="btn btn-primary" onClick={save}>
              Save
            </button>
            {ok && <span className="text-ink2">✓</span>}
            <ErrorLine error={err} />
          </div>
        )}
      </div>
      <div className="card flex w-[440px] items-center gap-2 p-3">
        <input className="ctl w-[120px]" placeholder="slug" value={newWs.slug} onChange={(e) => setNewWs({ ...newWs, slug: e.target.value.toLowerCase() })} />
        <input className="ctl min-w-0 flex-1" placeholder="Name" value={newWs.name} onChange={(e) => setNewWs({ ...newWs, name: e.target.value })} />
        <button
          className="btn"
          disabled={!newWs.slug}
          onClick={async () => {
            try {
              const r = await api<{ slug: string }>("/workspaces", { body: { slug: newWs.slug, name: newWs.name || newWs.slug } });
              qc.invalidateQueries({ queryKey: ["me"] });
              nav({ to: "/w/$ws", params: { ws: r.slug }, search: {} });
            } catch (e) {
              setErr(e);
            }
          }}
        >
          New workspace
        </button>
      </div>
    </div>
  );
}

function WsFlags() {
  const { slug: ws } = useWs();
  const q = useQuery({
    queryKey: ["wsflags", ws],
    queryFn: () =>
      api<{ strategy: string; branch: string; version: string; version_id: number; series: string; date: string; code: string; value: number | null }[]>(wsPath(ws, "/flags")),
  });
  return (
    <div className="card max-w-[1000px] overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th>Code</th>
            <th>Version</th>
            <th>Series</th>
            <th className="num">Date</th>
            <th className="num">Value</th>
          </tr>
        </thead>
        <tbody>
          {(q.data ?? []).map((f, i) => (
            <tr key={i}>
              <td className="font-semibold text-warn">{f.code}</td>
              <td>
                <Link to="/w/$ws/s/$slug" params={{ ws, slug: f.strategy }} search={{ b: f.branch, v: f.version, tab: "flags" }} className="hover:underline">
                  {f.strategy}/{f.branch}@{f.version}
                </Link>
              </td>
              <td className="font-mono text-[11.5px]">{f.series}</td>
              <td className="num">{f.date}</td>
              <td className="num">{f.code === "OUTLIER" ? pct(f.value) : f.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Audit() {
  const { slug: ws } = useWs();
  const q = useQuery({
    queryKey: ["audit", ws],
    queryFn: () =>
      api<{ id: number; at: string; actor: string | null; action: string; target_kind: string | null; target_id: number | null; before: unknown; after: unknown }[]>(wsPath(ws, "/audit")),
  });
  return (
    <div className="card overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th className="num">At</th>
            <th>Actor</th>
            <th>Action</th>
            <th>Target</th>
            <th>Change</th>
          </tr>
        </thead>
        <tbody>
          {(q.data ?? []).map((a) => (
            <tr key={a.id}>
              <td className="num">{a.at.slice(0, 19).replace("T", " ")}</td>
              <td>{a.actor ?? <span className="text-muted">system</span>}</td>
              <td className="font-mono text-[11.5px]">{a.action}</td>
              <td className="text-ink2">
                {a.target_kind}
                {a.target_id ? ` #${a.target_id}` : ""}
              </td>
              <td className="max-w-[600px] overflow-hidden text-ellipsis font-mono text-[11px] text-ink2" title={JSON.stringify({ before: a.before, after: a.after })}>
                {a.after ? JSON.stringify(a.after) : a.before ? JSON.stringify(a.before) : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
