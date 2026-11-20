import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";
import { Popover, Seg, Spark, StageDot } from "../components/bits";
import { api, qs, ws as wsPath } from "../lib/api";
import { useWs } from "../lib/ctx";
import { ago, compact, daysBetween, int, pct, ratio, sign } from "../lib/format";
import { useLocalState } from "../lib/hooks";
import type { Library as LibraryData, LibRow } from "../lib/types";
import type { LibSearch } from "../router";

type Getter = (r: LibRow) => number | string | null | undefined;
interface Col {
  id: string;
  label: string;
  get: Getter;
  render?: (r: LibRow, ctx: { stale: number; today: string; tag?: string; tagColors: Record<string, string | null>; onTag: (t: string) => void }) => React.ReactNode;
  num?: boolean;
  width?: number;
  asc?: boolean; // default sort direction is ascending (text, dates)
}

const sgn = (fmtF: (v: number | null) => string, get: (r: LibRow) => number | null) => (r: LibRow) => {
  const v = get(r);
  return <span className={sign(v)}>{fmtF(v)}</span>;
};

const COLS: Col[] = [
  { id: "strategy", label: "Strategy", get: (r) => r.strategy, asc: true, width: 200 },
  { id: "branch", label: "Branch", get: (r) => r.branch, asc: true, width: 110 },
  { id: "version", label: "Version", get: (r) => r.seq, asc: false, width: 70 },
  { id: "vers", label: "Vers", get: (r) => r.n_versions, num: true, width: 44 },
  { id: "stage", label: "Stage", get: (r) => r.stage, asc: true, width: 84, render: (r) => <StageDot stage={r.stage} /> },
  {
    id: "tags",
    label: "Tags",
    get: (r) => r.tags.join(","),
    asc: true,
    width: 160,
    render: (r, c) => (
      <span className="inline-flex gap-1 align-middle" title={r.tags.join(", ")}>
        {r.tags.map((t) => (
          <button key={t} className="tag" data-c={c.tagColors[t] ?? undefined} aria-pressed={c.tag === t} onClick={() => c.onTag(t)}>
            {t}
          </button>
        ))}
      </span>
    ),
  },
  { id: "spark", label: "", get: () => null, width: 96 },
  { id: "start", label: "Start", get: (r) => r.m.first_date, asc: true, width: 84, num: true },
  {
    id: "last",
    label: "Last",
    get: (r) => r.m.last_date,
    width: 84,
    num: true,
    render: (r, c) => {
      const lag = r.m.last_date ? daysBetween(r.m.last_date, c.today) : 0;
      return <span className={lag > c.stale ? "text-warn" : ""}>{r.m.last_date ?? "—"}</span>;
    },
  },
  { id: "n", label: "N", get: (r) => r.m.n, num: true, width: 56, render: (r) => int(r.m.n) },
  { id: "ann_ret", label: "AnnRet", get: (r) => r.m.ann_ret, num: true, render: sgn(pct, (r) => r.m.ann_ret) },
  { id: "cagr", label: "CAGR", get: (r) => r.m.cagr, num: true, render: sgn(pct, (r) => r.m.cagr) },
  { id: "ann_vol", label: "Vol", get: (r) => r.m.ann_vol, num: true, render: (r) => pct(r.m.ann_vol) },
  { id: "sharpe", label: "Sharpe", get: (r) => r.m.sharpe, num: true, render: (r) => <b className="font-semibold">{ratio(r.m.sharpe)}</b> },
  { id: "sortino", label: "Sortino", get: (r) => r.m.sortino, num: true, render: (r) => ratio(r.m.sortino) },
  { id: "max_dd", label: "MaxDD", get: (r) => r.m.max_dd, num: true, render: (r) => pct(r.m.max_dd) },
  { id: "max_dd_days", label: "DDd", get: (r) => r.m.max_dd_days, num: true, render: (r) => int(r.m.max_dd_days) },
  { id: "cur_dd", label: "CurDD", get: (r) => r.m.cur_dd, num: true, render: (r) => pct(r.m.cur_dd) },
  { id: "calmar", label: "Calmar", get: (r) => r.m.calmar, num: true, render: (r) => ratio(r.m.calmar) },
  { id: "win_rate", label: "Win", get: (r) => r.m.win_rate, num: true, render: (r) => pct(r.m.win_rate, 1) },
  { id: "ret_1d", label: "1D", get: (r) => r.ret_1d, num: true, render: sgn(pct, (r) => r.ret_1d) },
  { id: "ret_mtd", label: "MTD", get: (r) => r.ret_mtd, num: true, render: sgn(pct, (r) => r.ret_mtd) },
  { id: "ret_ytd", label: "YTD", get: (r) => r.ret_ytd, num: true, render: sgn(pct, (r) => r.ret_ytd) },
  { id: "ret_1y", label: "1Y", get: (r) => r.ret_1y, num: true, render: sgn(pct, (r) => r.ret_1y) },
  {
    id: "flags",
    label: "Flags",
    get: (r) => r.flags,
    num: true,
    width: 48,
    render: (r) => (r.flags ? <span className="font-semibold text-warn">{r.flags}</span> : <span className="text-muted">·</span>),
  },
  { id: "revisions", label: "Revs", get: (r) => r.revisions, num: true, width: 48, render: (r) => (r.revisions ? r.revisions : <span className="text-muted">·</span>) },
  { id: "code", label: "Code", get: (r) => r.code_ref, asc: true, width: 76, render: (r) => <span className="font-mono text-[11px] text-ink2">{r.code_ref ?? ""}</span> },
  { id: "reported", label: "Reported", get: (r) => r.last_report_at, width: 64, num: true, render: (r) => <span className="text-ink2">{ago(r.last_report_at)}</span> },
];

const DEFAULT_COLS = ["strategy", "branch", "version", "stage", "tags", "spark", "last", "ann_ret", "ann_vol", "sharpe", "max_dd", "calmar", "ret_mtd", "ret_ytd", "flags"];
const ROW_H = 24;

// column choices saved before Tags became a default column carry over, with Tags switched on
const initialCols = () => {
  try {
    const old = JSON.parse(localStorage.getItem("pfn.lib.cols") ?? "null") as string[] | null;
    if (Array.isArray(old)) return old.includes("tags") ? old : [...old, "tags"];
  } catch {
    /* ignore */
  }
  return DEFAULT_COLS;
};

export function Library() {
  const { slug, info } = useWs();
  const search = useSearch({ from: "/w/$ws/" }) as LibSearch;
  const nav = useNavigate({ from: "/w/$ws/" });
  const level = (search.level ?? "branch") as "branch" | "version";
  const window_ = search.window ?? "all";
  const [visible, setVisible] = useLocalState<string[]>("pfn.lib.cols.v2", initialCols);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [q, setQ] = useState(search.q ?? "");
  const searchBox = useRef<HTMLInputElement>(null);

  const lib = useQuery({
    queryKey: ["library", slug, level, window_],
    queryFn: () => api<LibraryData>(wsPath(slug, `/library${qs({ level, window: window_ })}`)),
  });

  const set = (patch: Partial<LibSearch>) => nav({ search: (s: LibSearch) => ({ ...s, ...patch }), replace: true });
  useEffect(() => {
    const t = setTimeout(() => {
      if ((search.q ?? "") !== q) set({ q: q || undefined });
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const scalarCols: Col[] = useMemo(
    () =>
      (lib.data?.scalar_names ?? []).map((n) => ({
        id: `s:${n}`,
        label: n,
        get: (r: LibRow) => r.scalars[n] ?? null,
        num: true,
        render: (r: LibRow) => compact(r.scalars[n] ?? null),
      })),
    [lib.data?.scalar_names],
  );
  const allCols = useMemo(() => [...COLS, ...scalarCols], [scalarCols]);
  const cols = useMemo(() => allCols.filter((c) => visible.includes(c.id)), [allCols, visible]);

  const [sortId, sortDir] = (search.sort ?? "sharpe:desc").split(":") as [string, "asc" | "desc"];
  const rows = useMemo(() => {
    let r = lib.data?.rows ?? [];
    if (!search.bench) r = r.filter((x) => x.kind === "strategy");
    if (search.tag) r = r.filter((x) => x.tags.includes(search.tag!));
    if (search.stage) r = r.filter((x) => x.stage === search.stage);
    const needle = (search.q ?? "").trim().toLowerCase();
    if (needle) {
      const terms = needle.split(/\s+/);
      r = r.filter((x) => {
        const hay = `${x.strategy} ${x.name} ${x.branch} ${x.version} ${x.tags.join(" ")} ${x.code_ref ?? ""}`.toLowerCase();
        return terms.every((t) => hay.includes(t));
      });
    }
    const col = allCols.find((c) => c.id === sortId);
    if (col) {
      const dir = sortDir === "asc" ? 1 : -1;
      r = [...r].sort((a, b) => {
        const va = col.get(a);
        const vb = col.get(b);
        const na = va === null || va === undefined || va === "";
        const nb = vb === null || vb === undefined || vb === "";
        if (na || nb) return na === nb ? 0 : na ? 1 : -1;
        if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
        return String(va).localeCompare(String(vb)) * dir;
      });
    }
    return r;
  }, [lib.data, search.bench, search.tag, search.stage, search.q, sortId, sortDir, allCols]);

  const scroller = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({ count: rows.length, getScrollElement: () => scroller.current, estimateSize: () => ROW_H, overscan: 20 });

  const selRows = rows.filter((r) => selected.has(r.version_id));
  const refOf = (r: LibRow) => (level === "branch" ? `${r.strategy}/${r.branch}` : `${r.strategy}/${r.branch}@${r.version}`);
  const compare = () => {
    if (!selRows.length) return;
    nav({ to: "/w/$ws/compare", params: { ws: slug }, search: { refs: selRows.slice(0, 8).map(refOf).join(",") } });
  };
  const newPortfolio = () =>
    nav({ to: "/w/$ws/p/$slug", params: { ws: slug, slug: "new" }, search: { refs: selRows.map(refOf).join(",") } });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;
      if (e.key === "/") {
        e.preventDefault();
        searchBox.current?.focus();
      }
      if (e.key === "c" && selRows.length) compare();
      if (e.key === "Escape") setSelected(new Set());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const toggleSort = (c: Col) => {
    if (c.id === "spark") return;
    const def = c.asc ? "asc" : "desc";
    const next = sortId === c.id ? (sortDir === "desc" ? "asc" : "desc") : def;
    set({ sort: `${c.id}:${next}` });
  };
  const lastClicked = useRef<number | null>(null);
  const toggleRow = (idx: number, shift: boolean) => {
    const anchor = lastClicked.current;
    lastClicked.current = idx;
    setSelected((prev) => {
      const s = new Set(prev);
      const id = rows[idx].version_id;
      if (shift && anchor !== null) {
        const [a, b] = [Math.min(anchor, idx), Math.max(anchor, idx)];
        const on = !s.has(id);
        for (let i = a; i <= b; i++) on ? s.add(rows[i].version_id) : s.delete(rows[i].version_id);
      } else if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });
  };

  const onTag = (t: string) => set({ tag: search.tag === t ? undefined : t });
  const today = lib.data?.today ?? new Date().toISOString().slice(0, 10);
  const items = v.getVirtualItems();
  const padTop = items.length ? items[0].start : 0;
  const padBottom = items.length ? v.getTotalSize() - items[items.length - 1].end : 0;
  const allSel = rows.length > 0 && rows.every((r) => selected.has(r.version_id));

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-edge bg-surface px-3 py-1.5">
        <input ref={searchBox} className="ctl w-[220px]" placeholder="Filter  /" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="ctl" value={search.tag ?? ""} onChange={(e) => set({ tag: e.target.value || undefined })}>
          <option value="">All tags</option>
          {lib.data?.tags.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <select className="ctl" value={search.stage ?? ""} onChange={(e) => set({ stage: e.target.value || undefined })}>
          <option value="">All stages</option>
          {["live", "paper", "research", "retired"].map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <Seg value={window_} options={[["all", "All"], ["live", "Live"], ["ytd", "YTD"], ["1y", "1Y"], ["3y", "3Y"]]} onChange={(w) => set({ window: w === "all" ? undefined : w })} />
        <Seg value={level} options={[["branch", "Branches"], ["version", "Versions"]]} onChange={(l) => set({ level: l === "branch" ? undefined : l })} />
        <button className="btn" aria-pressed={!!search.bench} style={search.bench ? { background: "var(--sel)", color: "var(--series-1)" } : undefined} onClick={() => set({ bench: search.bench ? undefined : "1" })}>
          Benchmarks
        </button>
        <Popover button={<button className="btn">Columns</button>}>
          {allCols
            .filter((c) => c.id !== "strategy" && c.id !== "spark")
            .map((c) => (
              <label key={c.id} className="flex h-6 cursor-pointer items-center gap-2 rounded px-2 hover:bg-hover">
                <input
                  type="checkbox"
                  checked={visible.includes(c.id)}
                  onChange={(e) => setVisible(e.target.checked ? [...visible, c.id] : visible.filter((x) => x !== c.id))}
                />
                {c.label}
              </label>
            ))}
          <button className="btn mt-1 w-full" onClick={() => setVisible(DEFAULT_COLS)}>
            Reset
          </button>
        </Popover>
        <div className="flex-1" />
        {selRows.length > 0 && (
          <>
            <span className="text-ink2">{selRows.length} selected</span>
            <button className="btn" onClick={compare}>
              Compare
            </button>
            {info.role !== "viewer" && (
              <button className="btn" onClick={newPortfolio}>
                New portfolio
              </button>
            )}
          </>
        )}
        <span className="num text-muted">{rows.length}</span>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto bg-surface">
        <table className="tbl" style={{ tableLayout: "fixed", minWidth: 40 + cols.reduce((a, c) => a + (c.width ?? 72), 0) }}>
          <colgroup>
            <col style={{ width: 30 }} />
            {cols.map((c) => (
              <col key={c.id} style={{ width: c.width ?? 72 }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              <th>
                <input type="checkbox" checked={allSel} onChange={() => setSelected(allSel ? new Set() : new Set(rows.map((r) => r.version_id)))} />
              </th>
              {cols.map((c) => (
                <th key={c.id} className={`${c.num ? "num" : ""} ${c.id !== "spark" ? "cursor-pointer hover:text-ink" : ""}`} onClick={() => toggleSort(c)}>
                  {c.label}
                  {sortId === c.id ? (sortDir === "desc" ? " ↓" : " ↑") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {padTop > 0 && (
              <tr style={{ height: padTop }}>
                <td colSpan={cols.length + 1} style={{ border: 0, padding: 0 }} />
              </tr>
            )}
            {items.map((it) => {
              const r = rows[it.index];
              const isSel = selected.has(r.version_id);
              return (
                <tr key={r.version_id} style={isSel ? { background: "var(--sel)" } : undefined}>
                  <td onClick={(e) => toggleRow(it.index, e.shiftKey)} className="cursor-pointer">
                    <input type="checkbox" checked={isSel} readOnly className="pointer-events-none" />
                  </td>
                  {cols.map((c) => (
                    <td key={c.id} className={`${c.num ? "num" : ""} overflow-hidden text-ellipsis`}>
                      {c.id === "strategy" ? (
                        <Link
                          to="/w/$ws/s/$slug"
                          params={{ ws: slug, slug: r.strategy }}
                          search={{ b: r.branch, v: r.version }}
                          className={`hover:text-accent hover:underline ${r.kind === "benchmark" ? "text-ink2 italic" : ""}`}
                          title={r.name}
                        >
                          {r.strategy}
                        </Link>
                      ) : c.id === "branch" ? (
                        <span className={r.is_default ? "" : "text-ink2"}>{r.branch}</span>
                      ) : c.id === "version" ? (
                        <span>
                          {r.version}
                          {r.is_head && level === "version" ? <span className="ml-1 text-accent">●</span> : null}
                          {r.head_locked && level === "branch" ? <span className="ml-1 text-muted" title="head locked">🔒</span> : null}
                        </span>
                      ) : c.id === "spark" ? (
                        <Spark data={r.spark} />
                      ) : c.render ? (
                        c.render(r, { stale: info.settings.stale_days, today, tag: search.tag, tagColors: lib.data?.tag_colors ?? {}, onTag })
                      ) : (
                        String(c.get(r) ?? "—")
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
            {padBottom > 0 && (
              <tr style={{ height: padBottom }}>
                <td colSpan={cols.length + 1} style={{ border: 0, padding: 0 }} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
