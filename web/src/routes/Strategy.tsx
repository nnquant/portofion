import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ErrorLine, Seg, StageDot } from "../components/bits";
import { NavChart, unionAlign, type ExtraPanel, type NavSeries } from "../components/NavChart";
import { DrawdownTable, MetricsTable, MonthlyHeatmap } from "../components/tables";
import { api, qs, ws as wsPath } from "../lib/api";
import { slotVar, useSlots } from "../lib/colors";
import { useWs } from "../lib/ctx";
import { ago, compact, int, pct, ratio, sign } from "../lib/format";
import type { Analytics, BranchInfo, SeriesData, Stage, StrategyDetail, VersionInfo } from "../lib/types";
import type { StratSearch } from "../router";

const MAX_OVERLAY = 8;

export function StrategyPage() {
  const { slug: ws, palette, canEdit, isAdmin } = useWs();
  const { slug } = useParams({ from: "/w/$ws/s/$slug" });
  const search = useSearch({ from: "/w/$ws/s/$slug" }) as StratSearch;
  const nav = useNavigate({ from: "/w/$ws/s/$slug" });
  const qc = useQueryClient();
  const set = (patch: Partial<StratSearch>) => nav({ search: (s: StratSearch) => ({ ...s, ...patch }), replace: true });

  const detail = useQuery({
    queryKey: ["strategy", ws, slug],
    queryFn: () => api<StrategyDetail>(wsPath(ws, `/strategies/${encodeURIComponent(slug)}`)),
  });
  const d = detail.data;

  // resolve the primary version from ?b & ?v (default: default branch head)
  const { branch, primary, byId } = useMemo(() => {
    const byId = new Map<number, { v: VersionInfo; b: BranchInfo }>();
    if (!d) return { branch: undefined, primary: undefined, byId };
    for (const b of d.branches) for (const v of b.versions) byId.set(v.id, { v, b });
    const branch = d.branches.find((b) => b.name === search.b) ?? d.branches.find((b) => b.is_default) ?? d.branches[0];
    const primary = branch?.versions.find((v) => v.label === search.v) ?? branch?.versions.find((v) => v.id === branch.head_version_id) ?? branch?.versions[0];
    return { branch, primary, byId };
  }, [d, search.b, search.v]);

  const overlayIds = useMemo(() => {
    const ids = (search.sel ?? "").split(",").map(Number).filter((x) => byId.has(x));
    const list = primary ? [primary.id, ...ids.filter((x) => x !== primary.id)] : ids;
    return list.slice(0, MAX_OVERLAY);
  }, [search.sel, primary, byId]);

  const slots = useSlots(overlayIds.map(String), `pfn.slots.s.${ws}.${slug}`);
  const win = search.win ?? "all";
  const analytics = useQueries({
    queries: overlayIds.map((id) => ({
      queryKey: ["analytics", ws, id, win],
      queryFn: () => api<Analytics>(wsPath(ws, `/versions/${id}/analytics${qs({ window: win, rolling: 126 })}`)),
    })),
  });
  const extraNames = (primary?.series ?? []).map((s) => s.name).filter((n) => n !== "returns" && n !== "nav");
  const extras = useQuery({
    queryKey: ["series", ws, primary?.id, extraNames.join(",")],
    queryFn: () => api<SeriesData>(wsPath(ws, `/versions/${primary!.id}/series${qs({ names: extraNames.join(",") })}`)),
    enabled: !!primary && extraNames.length > 0,
  });

  const label = (id: number) => {
    const e = byId.get(id);
    if (!e) return String(id);
    return e.b.name === branch?.name ? e.v.label : `${e.b.name}@${e.v.label}`;
  };

  const chart = useMemo(() => {
    const ok = analytics.map((a, i) => ({ a: a.data, id: overlayIds[i] })).filter((x) => x.a);
    if (!ok.length) return null;
    const { dates, cols } = unionAlign(ok.map((x) => ({ dates: x.a!.dates, values: x.a!.returns })));
    const series: NavSeries[] = ok.map((x, i) => ({
      key: String(x.id),
      name: label(x.id),
      color: palette.series[slots[String(x.id)] ?? i],
      returns: cols[i],
      width: x.id === primary?.id ? 2 : 1.25,
    }));
    let panels: ExtraPanel[] = [];
    if (extras.data && primary) {
      const pos = new Map(dates.map((dd, i) => [dd, i]));
      panels = Object.entries(extras.data.series).map(([name, vals]) => {
        const out = new Array(dates.length).fill(null);
        extras.data!.dates.forEach((dd, i) => {
          const k = pos.get(dd);
          if (k !== undefined) out[k] = vals[i];
        });
        return { name, data: out, color: palette.ink2, bar: extras.data!.meta[name]?.agg === "sum" };
      });
    }
    const markers = primary?.live_since ? [{ date: nearest(dates, primary.live_since), label: "live" }] : [];
    return { dates, series, panels, markers };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analytics.map((a) => a.dataUpdatedAt).join(), extras.data, palette, slots, primary?.id]);

  if (detail.error) return <div className="p-4"><ErrorLine error={detail.error} /></div>;
  if (!d || !branch || !primary) return null;

  const pa = analytics[0]?.data;
  const toggleOverlay = (id: number) => {
    const cur = overlayIds.filter((x) => x !== primary.id);
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id].slice(0, MAX_OVERLAY - 1);
    set({ sel: next.length ? next.join(",") : undefined });
  };
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["strategy", ws, slug] });
    qc.invalidateQueries({ queryKey: ["library", ws] });
    qc.invalidateQueries({ queryKey: ["analytics", ws] });
  };
  const tab = search.tab ?? "overview";
  const compareRefs = overlayIds.map((id) => {
    const e = byId.get(id)!;
    return `${slug}/${e.b.name}@${e.v.label}`;
  });

  return (
    <div className="flex h-full">
      <aside className="w-[260px] flex-none overflow-auto border-r border-edge bg-surface">
        <div className="border-b border-edge px-3 py-2">
          <div className="flex items-baseline justify-between gap-2">
            <span className="truncate text-[14px] font-semibold" title={d.strategy.name}>
              {d.strategy.slug}
            </span>
            {d.strategy.archived ? <span className="text-muted">archived</span> : d.strategy.kind === "benchmark" && <span className="text-muted">benchmark</span>}
          </div>
          {d.strategy.name !== d.strategy.slug && <div className="truncate text-ink2">{d.strategy.name}</div>}
          {d.strategy.tags.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{d.strategy.tags.map((t) => <Link key={t} to="/w/$ws" params={{ ws }} search={{ tag: t }} className="tag" data-c={d.strategy.tag_colors[t] ?? undefined}>{t}</Link>)}</div>}
        </div>
        {d.branches.map((b) => (
          <div key={b.id} className="border-b border-line py-1">
            <div className="flex h-6 items-center gap-1.5 px-3 font-semibold">
              <span className={b.archived ? "text-muted line-through" : ""}>{b.name}</span>
              {b.is_default && <span className="text-muted" title="default branch">★</span>}
              {b.head_locked && <span title="head locked">🔒</span>}
            </div>
            {b.versions.map((v) => {
              const isPrimary = v.id === primary.id;
              const inOverlay = overlayIds.includes(v.id);
              const slot = slots[String(v.id)];
              return (
                <div
                  key={v.id}
                  className={`group flex h-6 cursor-pointer items-center gap-1.5 pl-3 pr-2 ${isPrimary ? "bg-sel" : "hover:bg-hover"}`}
                  onClick={() => set({ b: b.name, v: v.label })}
                >
                  <input
                    type="checkbox"
                    checked={inOverlay}
                    disabled={isPrimary}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleOverlay(v.id)}
                  />
                  <span className="swatch" style={{ background: inOverlay && slot !== undefined ? slotVar(slot) : "transparent" }} />
                  <StageDot stage={v.stage} withLabel={false} />
                  <span className={`flex-1 truncate ${v.archived ? "text-muted line-through" : ""}`}>
                    {v.label}
                    {v.id === b.head_version_id && <span className="ml-1 text-accent" title="head">●</span>}
                  </span>
                  <span className="num w-[70px] text-[11px] text-muted">{v.metrics?.last_date?.slice(2) ?? ""}</span>
                  <span className={`num w-9 ${sign(v.metrics?.sharpe ?? null)}`}>{ratio(v.metrics?.sharpe ?? null, 1)}</span>
                </div>
              );
            })}
          </div>
        ))}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-[34px] flex-none items-center gap-1 border-b border-edge bg-surface pl-2 pr-3">
          {[
            ["overview", "Overview"],
            ["params", "Params"],
            ["series", "Series"],
            ["revisions", "Revisions"],
            ["flags", "Flags"],
            ["provenance", "Provenance"],
            ...(canEdit ? [["manage", "Manage"]] : []),
          ].map(([k, l]) => (
            <button key={k} className="tab" aria-selected={tab === k} onClick={() => set({ tab: k === "overview" ? undefined : k })}>
              {l}
              {k === "flags" && primary.summary?.n_open_flags ? <span className="ml-1 text-warn">{primary.summary.n_open_flags}</span> : null}
              {k === "revisions" && primary.summary?.n_revisions ? <span className="ml-1 text-muted">{primary.summary.n_revisions}</span> : null}
            </button>
          ))}
          <div className="flex-1" />
          <span className="mr-2 text-ink2">
            <b className="text-ink">{branch.name}@{primary.label}</b> · <StageDot stage={primary.stage} /> {primary.code_ref && <span className="font-mono text-[11px]">· {primary.code_ref}</span>}
          </span>
          <Seg value={win} options={[["all", "All"], ["live", "Live"], ["ytd", "YTD"], ["1y", "1Y"], ["3y", "3Y"]]} onChange={(w) => set({ win: w === "all" ? undefined : w })} />
          <Link className="btn ml-1 flex items-center" to="/w/$ws/compare" params={{ ws }} search={{ refs: compareRefs.join(",") }}>
            Compare
          </Link>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-3">
          {tab === "overview" && (
            <div className="flex flex-col gap-3 2xl:flex-row">
              <div className="card min-w-0 flex-1 p-2">
                {chart ? (
                  <NavChart dates={chart.dates} series={chart.series} extras={chart.panels} markers={chart.markers} navHeight={320} />
                ) : (
                  <div className="h-[460px]" />
                )}
              </div>
              <div className="flex flex-col gap-3 2xl:w-[560px]">
                <div className="card overflow-auto">
                  <MetricsTable
                    cols={overlayIds.map((id, i) => ({
                      key: String(id),
                      name: label(id),
                      color: overlayIds.length > 1 ? palette.series[slots[String(id)] ?? i] : undefined,
                      metrics: analytics[i]?.data?.metrics,
                    }))}
                  />
                </div>
              </div>
            </div>
          )}
          {tab === "overview" && pa && (
            <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
              <div className="card overflow-auto">
                <MonthlyHeatmap data={pa.monthly} />
              </div>
              <div className="card overflow-auto">
                <DrawdownTable eps={pa.top_dd} />
              </div>
            </div>
          )}
          {tab === "params" && <ParamsDiff versions={overlayIds.map((id) => byId.get(id)!)} label={label} slots={slots} />}
          {tab === "series" && <SeriesTab ws={ws} v={primary} />}
          {tab === "revisions" && <Revisions ws={ws} id={primary.id} />}
          {tab === "flags" && <Flags ws={ws} id={primary.id} canEdit={canEdit} onAck={refresh} />}
          {tab === "provenance" && <Provenance ws={ws} id={primary.id} />}
          {tab === "manage" && canEdit && <Manage d={d} branch={branch} v={primary} isAdmin={isAdmin} onChange={refresh} />}
        </div>
      </section>
    </div>
  );
}

function nearest(dates: string[], d: string): string {
  for (const x of dates) if (x >= d) return x;
  return dates[dates.length - 1];
}

function ParamsDiff({ versions, label, slots }: { versions: { v: VersionInfo; b: BranchInfo }[]; label: (id: number) => string; slots: Record<string, number> }) {
  const keys = [...new Set(versions.flatMap((x) => Object.keys(x.v.params ?? {})))].sort();
  const show = (x: unknown) => (x === undefined ? "" : typeof x === "string" ? x : JSON.stringify(x));
  const rows: [string, (v: VersionInfo) => unknown][] = [
    ...keys.map((k) => [k, (v: VersionInfo) => v.params?.[k]] as [string, (v: VersionInfo) => unknown]),
  ];
  const meta: [string, (v: VersionInfo) => unknown][] = [
    ["code_ref", (v) => v.code_ref ?? ""],
    ["code_repo", (v) => v.code_repo ?? ""],
    ["stage", (v) => v.stage],
    ["live_since", (v) => v.live_since ?? ""],
    ["created", (v) => v.created_at.slice(0, 16).replace("T", " ")],
  ];
  const Row = ([k, f]: [string, (v: VersionInfo) => unknown], isParam: boolean) => {
    const vals = versions.map((x) => show(f(x.v)));
    const differs = new Set(vals).size > 1;
    return (
      <tr key={k}>
        <td className={isParam ? "font-mono text-[11.5px]" : "text-ink2"}>{k}</td>
        {vals.map((x, i) => (
          <td key={i} className="font-mono text-[11.5px]" style={differs && isParam ? { background: "var(--sel)" } : undefined}>
            {x}
          </td>
        ))}
      </tr>
    );
  };
  return (
    <div className="card overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th />
            {versions.map((x) => (
              <th key={x.v.id}>
                <span className="inline-flex items-center gap-1.5">
                  {versions.length > 1 && <span className="swatch" style={{ background: slotVar(slots[String(x.v.id)] ?? 0) }} />}
                  {label(x.v.id)}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => Row(r, true))}
          {meta.map((r) => Row(r, false))}
        </tbody>
      </table>
    </div>
  );
}

function SeriesTab({ ws, v }: { ws: string; v: VersionInfo }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="card overflow-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>Series</th>
              <th>Agg</th>
              <th>Unit</th>
              <th className="num">N</th>
              <th className="num">Start</th>
              <th className="num">Last</th>
            </tr>
          </thead>
          <tbody>
            {v.series.map((s) => (
              <tr key={s.name}>
                <td className="font-mono text-[11.5px]">{s.name}</td>
                <td className="text-ink2">{s.agg}</td>
                <td className="text-ink2">{s.unit ?? ""}</td>
                <td className="num">{int(s.n_points)}</td>
                <td className="num">{s.first_date}</td>
                <td className="num">{s.last_date}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="p-2">
          <a className="btn inline-flex items-center" href={`/api/v1${wsPath(ws, `/versions/${v.id}/series?format=csv`)}`}>
            CSV
          </a>
        </div>
      </div>
      <div className="card overflow-auto">
        <table className="tbl">
          <thead>
            <tr>
              <th>Scalar</th>
              <th className="num">Value</th>
              <th className="num">As of</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(v.scalars).map(([k, s]) => (
              <tr key={k}>
                <td className="font-mono text-[11.5px]">{k}</td>
                <td className="num">{compact(s.value)}</td>
                <td className="num text-ink2">{s.as_of ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Revisions({ ws, id }: { ws: string; id: number }) {
  const q = useQuery({
    queryKey: ["revisions", ws, id],
    queryFn: () => api<{ series: string; date: string; old: number | null; new: number | null; batch_id: number; at: string }[]>(wsPath(ws, `/versions/${id}/revisions`)),
  });
  return (
    <div className="card overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th>Series</th>
            <th className="num">Date</th>
            <th className="num">Old</th>
            <th className="num">New</th>
            <th className="num">Δ</th>
            <th className="num">Batch</th>
            <th className="num">At</th>
          </tr>
        </thead>
        <tbody>
          {(q.data ?? []).map((r, i) => (
            <tr key={i}>
              <td className="font-mono text-[11.5px]">{r.series}</td>
              <td className="num">{r.date}</td>
              <td className="num">{r.old === null ? "—" : r.series === "returns" ? pct(r.old, 3) : compact(r.old)}</td>
              <td className="num">{r.new === null ? <span className="text-crit">deleted</span> : r.series === "returns" ? pct(r.new, 3) : compact(r.new)}</td>
              <td className={`num ${sign(r.new !== null && r.old !== null ? r.new - r.old : null)}`}>
                {r.new !== null && r.old !== null ? (r.series === "returns" ? pct(r.new - r.old, 3) : compact(r.new - r.old)) : ""}
              </td>
              <td className="num text-ink2">#{r.batch_id}</td>
              <td className="num text-ink2">{r.at.slice(0, 16).replace("T", " ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Flags({ ws, id, canEdit, onAck }: { ws: string; id: number; canEdit: boolean; onAck: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["flags", ws, id],
    queryFn: () => api<{ series: string; date: string; code: string; value: number | null; acked: boolean }[]>(wsPath(ws, `/versions/${id}/flags`)),
  });
  const ack = async (dates?: string[]) => {
    await api(wsPath(ws, "/flags/ack"), { body: { version_id: id, dates } });
    qc.invalidateQueries({ queryKey: ["flags", ws, id] });
    onAck();
  };
  const open = (q.data ?? []).filter((f) => !f.acked).length;
  return (
    <div className="card overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th>Code</th>
            <th>Series</th>
            <th className="num">Date</th>
            <th className="num">Value</th>
            <th className="num">{canEdit && open > 0 && <button className="btn" onClick={() => ack()}>Ack all</button>}</th>
          </tr>
        </thead>
        <tbody>
          {(q.data ?? []).map((f, i) => (
            <tr key={i} className={f.acked ? "text-muted" : ""}>
              <td className={f.acked ? "" : "font-semibold text-warn"}>{f.code}</td>
              <td className="font-mono text-[11.5px]">{f.series}</td>
              <td className="num">{f.date}</td>
              <td className="num">{f.code === "OUTLIER" ? pct(f.value) : f.code === "GAP" ? `${f.value}d` : ratio(f.value, 3)}</td>
              <td className="num">{f.acked ? "✓" : canEdit && <button className="btn" onClick={() => ack([f.date])}>Ack</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Provenance({ ws, id }: { ws: string; id: number }) {
  const q = useQuery({
    queryKey: ["batches", ws, id],
    queryFn: () =>
      api<{ id: number; at: string; key: string | null; user: string | null; client: Record<string, string>; series: Record<string, Record<string, number>>; warnings: number }[]>(
        wsPath(ws, `/versions/${id}/batches`),
      ),
  });
  return (
    <div className="card overflow-auto">
      <table className="tbl">
        <thead>
          <tr>
            <th className="num">Batch</th>
            <th className="num">At</th>
            <th>By</th>
            <th>Agent</th>
            <th>Host</th>
            <th>Series</th>
            <th className="num">Warn</th>
          </tr>
        </thead>
        <tbody>
          {(q.data ?? []).map((b) => (
            <tr key={b.id}>
              <td className="num text-ink2">#{b.id}</td>
              <td className="num" title={b.at}>{b.at.slice(0, 19).replace("T", " ")} <span className="text-muted">{ago(b.at)}</span></td>
              <td>{b.key ? `key:${b.key}` : b.user}</td>
              <td>{b.client?.agent ?? ""}</td>
              <td className="text-ink2">{b.client?.host ?? ""}</td>
              <td className="text-ink2">
                {Object.entries(b.series)
                  .map(([n, s]) => `${n} +${s.inserted ?? 0} ~${s.updated ?? 0}${s.deleted ? ` −${s.deleted}` : ""}`)
                  .join("  ")}
              </td>
              <td className={`num ${b.warnings ? "text-warn" : "text-muted"}`}>{b.warnings || "·"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Manage({ d, branch, v, isAdmin, onChange }: { d: StrategyDetail; branch: BranchInfo; v: VersionInfo; isAdmin: boolean; onChange: () => void }) {
  const { slug: ws } = useWs();
  const nav = useNavigate();
  const [err, setErr] = useState<unknown>(null);
  const [name, setName] = useState(d.strategy.name);
  const [tags, setTags] = useState(d.strategy.tags.join(", "));
  const [ppy, setPpy] = useState(d.strategy.periods_per_year ? String(d.strategy.periods_per_year) : "");
  const [liveSince, setLiveSince] = useState(v.live_since ?? "");
  const run = async (fn: () => Promise<unknown>) => {
    setErr(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setErr(e);
    }
  };
  const s = encodeURIComponent(d.strategy.slug);
  const patchS = (body: object) => run(() => api(wsPath(ws, `/strategies/${s}`), { method: "PATCH", body }));
  const patchB = (body: object) => run(() => api(wsPath(ws, `/strategies/${s}/branches/${encodeURIComponent(branch.name)}`), { method: "PATCH", body }));
  const patchV = (body: object) => run(() => api(wsPath(ws, `/versions/${v.id}`), { method: "PATCH", body }));
  const row = "grid grid-cols-[120px_1fr] items-center gap-2";
  // fields save on blur; Enter commits too
  const commitOnEnter = (e: React.KeyboardEvent<HTMLInputElement>) => e.key === "Enter" && e.currentTarget.blur();
  const saveTags = () => {
    const next = [...new Set(tags.split(/[,，、;；]/).map((t) => t.trim()).filter(Boolean))];
    setTags(next.join(", "));
    if ([...next].sort().join() !== [...d.strategy.tags].sort().join()) patchS({ tags: next });
  };
  // archiving hides it from the Library, so ask first; unarchiving does not
  const archive = (what: string, archived: boolean, patch: (b: object) => unknown) =>
    (archived || confirm(`Archive ${what}? It will be hidden from the Library.`)) && patch({ archived: !archived });
  return (
    <div className="grid max-w-[1100px] gap-3 lg:grid-cols-3">
      <div className="card flex flex-col gap-2 p-3">
        <div className="font-semibold">{d.strategy.slug}</div>
        <div className={row}>
          <span className="text-ink2">Name</span>
          <input className="ctl" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={commitOnEnter} onBlur={() => name !== d.strategy.name && patchS({ name })} />
        </div>
        <div className={row}>
          <span className="text-ink2">Tags</span>
          <input className="ctl" value={tags} onChange={(e) => setTags(e.target.value)} onKeyDown={commitOnEnter} onBlur={saveTags} />
        </div>
        <div className={row}>
          <span className="text-ink2">Periods / year</span>
          <input
            className="ctl"
            placeholder={String(d.settings.periods_per_year)}
            value={ppy}
            onChange={(e) => setPpy(e.target.value)}
            onBlur={() => patchS(ppy ? { periods_per_year: Number(ppy) } : { clear_periods_per_year: true })}
          />
        </div>
        <div className={row}>
          <span className="text-ink2">Kind</span>
          <select className="ctl" value={d.strategy.kind} onChange={(e) => patchS({ kind: e.target.value })}>
            <option>strategy</option>
            <option>benchmark</option>
          </select>
        </div>
        <div className={row}>
          <span className="text-ink2">Default branch</span>
          <select className="ctl" value={d.branches.find((b) => b.is_default)?.name} onChange={(e) => patchS({ default_branch: e.target.value })}>
            {d.branches.map((b) => (
              <option key={b.id}>{b.name}</option>
            ))}
          </select>
        </div>
        <div className="flex gap-2 pt-1">
          <button className="btn" onClick={() => archive(d.strategy.slug, d.strategy.archived, patchS)}>
            {d.strategy.archived ? "Unarchive" : "Archive"}
          </button>
          {isAdmin && (
            <button
              className="btn text-crit"
              onClick={() =>
                confirm(`Delete ${d.strategy.slug} and all its data?`) &&
                run(async () => {
                  await api(wsPath(ws, `/strategies/${s}`), { method: "DELETE" });
                  nav({ to: "/w/$ws", params: { ws }, search: {} });
                })
              }
            >
              Delete
            </button>
          )}
        </div>
      </div>
      <div className="card flex flex-col gap-2 p-3">
        <div className="font-semibold">{branch.name}</div>
        <div className={row}>
          <span className="text-ink2">Head</span>
          <select className="ctl" value={branch.versions.find((x) => x.id === branch.head_version_id)?.label ?? ""} onChange={(e) => patchB({ head: e.target.value })}>
            {branch.versions.map((x) => (
              <option key={x.id}>{x.label}</option>
            ))}
          </select>
        </div>
        <label className={row}>
          <span className="text-ink2">Lock head</span>
          <input type="checkbox" className="justify-self-start" checked={branch.head_locked} onChange={(e) => patchB({ head_locked: e.target.checked })} />
        </label>
        <div className="flex gap-2 pt-1">
          <button className="btn" onClick={() => archive(`${d.strategy.slug}/${branch.name}`, branch.archived, patchB)}>
            {branch.archived ? "Unarchive" : "Archive"}
          </button>
        </div>
      </div>
      <div className="card flex flex-col gap-2 p-3">
        <div className="font-semibold">
          {branch.name}@{v.label}
        </div>
        <div className={row}>
          <span className="text-ink2">Stage</span>
          <select className="ctl" value={v.stage} onChange={(e) => patchV({ stage: e.target.value as Stage })}>
            {["research", "paper", "live", "retired"].map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </div>
        <div className={row}>
          <span className="text-ink2">Live since</span>
          <input
            type="date"
            className="ctl"
            value={liveSince}
            onChange={(e) => setLiveSince(e.target.value)}
            onBlur={() => liveSince !== (v.live_since ?? "") && patchV(liveSince ? { live_since: liveSince } : { clear_live_since: true })}
          />
        </div>
        <div className="flex gap-2 pt-1">
          {branch.head_version_id !== v.id && (
            <button className="btn" onClick={() => patchB({ head: v.label })}>
              Make head
            </button>
          )}
          <button className="btn" onClick={() => archive(`${d.strategy.slug}/${branch.name}@${v.label}`, v.archived, patchV)}>
            {v.archived ? "Unarchive" : "Archive"}
          </button>
          {isAdmin && (
            <button className="btn text-crit" onClick={() => confirm(`Delete ${branch.name}@${v.label}?`) && run(() => api(wsPath(ws, `/versions/${v.id}`), { method: "DELETE" }))}>
              Delete
            </button>
          )}
        </div>
      </div>
      <div className="lg:col-span-3">
        <ErrorLine error={err} />
      </div>
    </div>
  );
}
