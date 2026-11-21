import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { ErrorLine } from "../components/bits";
import { RangeChart } from "../components/RangeChart";
import { NavChart } from "../components/NavChart";
import { RefPicker } from "../components/RefPicker";
import { DrawdownTable, MetricsTable, MonthlyHeatmap } from "../components/tables";
import { api, ApiError, ws as wsPath } from "../lib/api";
import { catAxis, legend, line, navFmt, pctFmt, tooltip, valAxis } from "../lib/chart";
import { useSlots } from "../lib/colors";
import { useWs } from "../lib/ctx";
import { pct, sign } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import type { PfDetail, PfResult, PortfolioSpec } from "../lib/types";

const DEFAULT_SPEC: PortfolioSpec = {
  scheme: "fixed",
  rebalance: "monthly",
  lookback: 63,
  start: "common",
  cost_bps: 0,
  gross: 1,
  renormalize: true,
  shrinkage: 0.1,
};

interface MemberRow {
  ref: string;
  weight: number | null;
}

export function PortfolioPage() {
  const { slug: ws, palette: p, canEdit } = useWs();
  const { slug } = useParams({ from: "/w/$ws/p/$slug" });
  const search = useSearch({ from: "/w/$ws/p/$slug" });
  const isNew = slug === "new";
  const nav = useNavigate();
  const qc = useQueryClient();

  const detail = useQuery({
    queryKey: ["portfolio", ws, slug],
    queryFn: () => api<PfDetail>(wsPath(ws, `/portfolios/${slug}`)),
    enabled: !isNew,
  });

  const [form, setForm] = useState<{ slug: string; name: string; spec: PortfolioSpec; members: MemberRow[] } | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saveErr, setSaveErr] = useState<unknown>(null);

  useEffect(() => {
    if (isNew) {
      const refs = (search.refs ?? "").split(",").filter(Boolean);
      setForm({ slug: "", name: "", spec: DEFAULT_SPEC, members: refs.map((r) => ({ ref: r, weight: refs.length ? +(1 / refs.length).toFixed(4) : null })) });
      setDirty(true);
    } else if (detail.data) {
      setForm({
        slug: detail.data.slug,
        name: detail.data.name,
        spec: { ...DEFAULT_SPEC, ...detail.data.spec },
        members: detail.data.members.map((m) => ({ ref: m.key, weight: m.weight })),
      });
      setDirty(false);
    }
  }, [isNew, detail.data, search.refs]);

  const saved = useQuery({
    queryKey: ["pfresult", ws, slug, detail.data?.spec_rev, detail.data?.weights_rev],
    queryFn: () => api<PfResult>(wsPath(ws, `/portfolios/${slug}/result`)),
    enabled: !isNew && !dirty,
  });
  const previewBody = useDebounced(form && dirty ? JSON.stringify({ spec: form.spec, members: form.members, slug: isNew ? null : slug }) : "", 400);
  const preview = useQuery({
    queryKey: ["pfpreview", ws, previewBody],
    queryFn: () => api<PfResult>(wsPath(ws, "/portfolios/preview"), { body: JSON.parse(previewBody) }),
    enabled: !!previewBody && (form?.members.length ?? 0) > 0,
    retry: false,
  });
  const res = dirty ? preview.data : saved.data;
  const resErr = dirty ? preview.error : saved.error;

  const keys = useMemo(() => (form?.members ?? []).map((m) => m.ref), [form?.members]);
  const slots = useSlots(keys, `pfn.slots.pf.${ws}.${slug}`);
  const mcolor = (ref: string) => {
    const s = slots[ref];
    return s !== undefined && s < 8 ? p.series[s] : p.muted;
  };

  const update = (patch: Partial<NonNullable<typeof form>>) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
    setDirty(true);
  };
  const setSpec = (patch: Partial<PortfolioSpec>) => form && update({ spec: { ...form.spec, ...patch } });
  const setMember = (i: number, patch: Partial<MemberRow>) => form && update({ members: form.members.map((m, k) => (k === i ? { ...m, ...patch } : m)) });

  async function save() {
    if (!form) return;
    setSaveErr(null);
    const body = { slug: form.slug, name: form.name || form.slug, spec: form.spec, members: form.members };
    try {
      if (isNew) await api(wsPath(ws, "/portfolios"), { body });
      else await api(wsPath(ws, `/portfolios/${slug}`), { method: "PUT", body });
      qc.invalidateQueries({ queryKey: ["portfolios", ws] });
      qc.invalidateQueries({ queryKey: ["portfolio", ws] });
      setDirty(false);
      if (isNew || form.slug !== slug) nav({ to: "/w/$ws/p/$slug", params: { ws, slug: form.slug }, search: { refs: undefined } });
    } catch (e) {
      setSaveErr(e);
    }
  }
  async function remove() {
    if (!confirm(`Delete pf:${slug}?`)) return;
    try {
      await api(wsPath(ws, `/portfolios/${slug}`), { method: "DELETE" });
      qc.invalidateQueries({ queryKey: ["portfolios", ws] });
      nav({ to: "/w/$ws/p", params: { ws } });
    } catch (e) {
      setSaveErr(e);
    }
  }

  const weightsOpt = useMemo(() => {
    if (!res || !form) return null;
    const w = res.weights;
    const ks = Object.keys(w.series).filter((k) => k !== "cash");
    const series = ks.map((k, i) => {
      const ref = form.members[i]?.ref ?? k;
      const c = mcolor(ref);
      return { ...line(ref, w.series[k], c), stack: "w", areaStyle: { color: c, opacity: 0.7 }, lineStyle: { width: 0 }, step: "end" };
    });
    const cashMax = Math.max(...(w.series.cash ?? []).map((x) => Math.abs(x ?? 0)));
    const gross = Math.max(...w.dates.map((_, t) => ks.reduce((a, k) => a + Math.max(0, w.series[k][t] ?? 0), 0)));
    if (cashMax > 1e-6) series.push({ ...line("cash", w.series.cash, p.grid), stack: "w", areaStyle: { color: p.grid, opacity: 1 }, lineStyle: { width: 0 }, step: "end" });
    return {
      animation: false,
      grid: { left: 44, right: 12, top: 32, bottom: 22 },
      legend: legend(p, { right: 40 }),
      xAxis: catAxis(p, w.dates),
      yAxis: valAxis(p, 0, pctFmt(0), gross <= 1.0001 ? { scale: false, min: 0, max: 1, interval: 0.25 } : { scale: false, splitNumber: 4 }),
      dataZoom: [{ type: "inside", filterMode: "filter" }],
      tooltip: tooltip(p, (ps: any[]) =>
        `<div style="color:${p.muted}">${ps[0]?.axisValue ?? ""}</div>` +
        ps.map((x) => `<div style="display:flex;gap:10px;justify-content:space-between"><span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${x.color};margin-right:6px"></span>${x.seriesName}</span><b>${pct(x.value, 1)}</b></div>`).join(""),
      ),
      series,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [res, slots, p]);

  const membersOpt = useMemo(() => {
    if (!res || !form || !res.member_nav.dates.length) return null;
    const md = res.member_nav;
    const pos = new Map(res.dates.map((d, i) => [d, i]));
    const pfNav = md.dates.map((d) => res.nav[pos.get(d) ?? -1] ?? null);
    const ms = res.members.map((m, i) => {
      const ref = form.members[i]?.ref ?? m.key;
      return line(ref, md.series[m.key] ?? [], mcolor(ref), { lineStyle: { width: 1, color: mcolor(ref), opacity: 0.85 } });
    });
    return {
      animation: false,
      grid: { left: 44, right: 12, top: 32, bottom: 22 },
      legend: legend(p, { right: 40 }),
      xAxis: catAxis(p, md.dates),
      yAxis: valAxis(p, 0, navFmt, { splitNumber: 4 }),
      dataZoom: [{ type: "inside", filterMode: "filter" }],
      tooltip: tooltip(p, (ps: any[]) =>
        `<div style="color:${p.muted}">${ps[0]?.axisValue ?? ""}</div>` +
        ps.filter((x) => x.value != null).map((x) => `<div style="display:flex;gap:10px;justify-content:space-between"><span><span style="display:inline-block;width:10px;height:3px;border-radius:2px;background:${x.color};vertical-align:middle;margin-right:6px"></span>${x.seriesName}</span><b>${Number(x.value).toFixed(3)}</b></div>`).join(""),
      ),
      series: [line("portfolio", pfNav, p.ink, { lineStyle: { width: 2.25, color: p.ink }, z: 5 }), ...ms],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [res, slots, p]);

  if (!isNew && detail.error) return <div className="p-4"><ErrorLine error={detail.error} /></div>;
  if (!form) return null;
  const spec = form.spec;
  const volScheme = spec.scheme === "inverse_vol" || spec.scheme === "risk_parity";
  const wsum = form.members.reduce((a, m) => a + (m.weight ?? 0), 0);
  const ro = !canEdit;

  return (
    <div className="flex h-full">
      <aside className="flex w-[460px] flex-none flex-col overflow-auto border-r border-edge bg-surface">
        <div className="flex items-center gap-2 border-b border-edge px-3 py-2">
          <span className="text-muted">pf:</span>
          <input className="ctl w-[130px] font-semibold" placeholder="slug" value={form.slug} disabled={ro} onChange={(e) => update({ slug: e.target.value.toLowerCase() })} />
          <input className="ctl min-w-0 flex-1" placeholder="Name" value={form.name} disabled={ro} onChange={(e) => update({ name: e.target.value })} />
        </div>
        <div className="grid grid-cols-[repeat(3,minmax(0,1fr))] gap-x-2 gap-y-1.5 border-b border-edge px-3 py-2">
          <Field label="Scheme">
            <select className="ctl w-full" value={spec.scheme} disabled={ro} onChange={(e) => setSpec({ scheme: e.target.value as PortfolioSpec["scheme"] })}>
              <option value="fixed">fixed</option>
              <option value="equal">equal</option>
              <option value="inverse_vol">inverse vol</option>
              <option value="risk_parity">risk parity</option>
              <option value="reported">reported</option>
            </select>
          </Field>
          <Field label="Rebalance">
            <select className="ctl w-full" value={spec.rebalance} disabled={ro || spec.scheme === "reported"} onChange={(e) => setSpec({ rebalance: e.target.value as PortfolioSpec["rebalance"] })}>
              {["daily", "weekly", "monthly", "quarterly", "none"].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </Field>
          <Field label="Start">
            <select className="ctl w-full" value={["common", "earliest"].includes(spec.start) ? spec.start : "date"} disabled={ro} onChange={(e) => setSpec({ start: e.target.value === "date" ? new Date().toISOString().slice(0, 10) : e.target.value })}>
              <option value="common">common</option>
              <option value="earliest">earliest</option>
              <option value="date">date…</option>
            </select>
          </Field>
          {!["common", "earliest"].includes(spec.start) && (
            <Field label="Start date">
              <input type="date" className="ctl w-full" value={spec.start} disabled={ro} onChange={(e) => setSpec({ start: e.target.value })} />
            </Field>
          )}
          {volScheme && (
            <Field label="Lookback">
              <input type="number" className="ctl w-full" min={5} value={spec.lookback} disabled={ro} onChange={(e) => setSpec({ lookback: Number(e.target.value) })} />
            </Field>
          )}
          <Field label="Cost bps">
            <input type="number" className="ctl w-full" min={0} step={0.5} value={spec.cost_bps} disabled={ro} onChange={(e) => setSpec({ cost_bps: Number(e.target.value) })} />
          </Field>
          {spec.scheme !== "fixed" && spec.scheme !== "reported" && (
            <Field label="Gross">
              <input type="number" className="ctl w-full" min={0.1} step={0.1} value={spec.gross} disabled={ro} onChange={(e) => setSpec({ gross: Number(e.target.value) })} />
            </Field>
          )}
          {spec.scheme === "fixed" && (
            <Field label="Renormalize">
              <input type="checkbox" className="mt-1.5" checked={spec.renormalize} disabled={ro} onChange={(e) => setSpec({ renormalize: e.target.checked })} />
            </Field>
          )}
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th />
              <th>Member</th>
              <th />
              {spec.scheme === "fixed" && <th className="num">Target</th>}
              <th className="num">Now</th>
              <th className="num">Risk</th>
              <th className="num">Contrib</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {form.members.map((m, i) => {
              const r = res?.members[i];
              const pinned = m.ref.includes("@");
              const isPf = m.ref.startsWith("pf:");
              return (
                <tr key={m.ref}>
                  <td className="w-4">
                    <span className="swatch" style={{ background: mcolor(m.ref) }} />
                  </td>
                  <td className="max-w-[150px] overflow-hidden text-ellipsis" title={r?.resolved ?? m.ref}>
                    {isPf ? (
                      <Link to="/w/$ws/p/$slug" params={{ ws, slug: m.ref.slice(3) }} search={{ refs: undefined }} className="hover:underline">
                        {m.ref}
                      </Link>
                    ) : (
                      m.ref
                    )}
                    {r?.lagging && <span className="ml-1 text-warn" title="lagging">⏱</span>}
                    {r?.excluded && <span className="ml-1 text-crit" title="no data">∅</span>}
                  </td>
                  <td>
                    {!isPf && (
                      <button
                        className="text-[11px] text-ink2 hover:text-ink"
                        disabled={ro || (!pinned && !r?.resolved)}
                        title={pinned ? "pinned version" : "follows branch head"}
                        onClick={() => setMember(i, { ref: pinned ? m.ref.split("@")[0] : (r?.resolved ?? m.ref) })}
                      >
                        {pinned ? "pin" : "head"}
                      </button>
                    )}
                  </td>
                  {spec.scheme === "fixed" && (
                    <td className="num">
                      <input
                        type="number"
                        step={0.01}
                        className="ctl w-[64px] text-right"
                        value={m.weight ?? ""}
                        disabled={ro}
                        onChange={(e) => setMember(i, { weight: e.target.value === "" ? null : Number(e.target.value) })}
                      />
                    </td>
                  )}
                  <td className="num">{pct(r?.current_weight ?? null, 1)}</td>
                  <td className="num text-ink2">{pct(r?.risk_contrib ?? null, 1)}</td>
                  <td className={`num ${sign(r?.contribution ?? null)}`}>{pct(r?.contribution ?? null, 1)}</td>
                  <td>
                    {!ro && (
                      <button className="px-1 text-muted hover:text-crit" onClick={() => update({ members: form.members.filter((_, k) => k !== i) })}>
                        ×
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {spec.scheme === "fixed" && form.members.length > 0 && (
              <tr>
                <td />
                <td colSpan={2} />
                <td className={`num ${Math.abs(wsum - 1) > 1e-6 ? "text-warn" : "text-muted"}`}>{wsum.toFixed(3)}</td>
                <td colSpan={4} />
              </tr>
            )}
          </tbody>
        </table>
        {!ro && (
          <div className="flex items-center gap-2 px-3 py-2">
            <RefPicker width={260} placeholder="Add member…" exclude={form.members.map((m) => m.ref)} onPick={(ref) => update({ members: [...form.members, { ref, weight: spec.scheme === "fixed" ? 0 : null }] })} />
            {spec.scheme === "fixed" && form.members.length > 0 && (
              <button className="btn" onClick={() => update({ members: form.members.map((m) => ({ ...m, weight: +(1 / form.members.length).toFixed(6) })) })}>
                Equalize
              </button>
            )}
          </div>
        )}
        {spec.scheme === "reported" && detail.data && <div className="px-3 pb-2 text-ink2">{detail.data.n_weight_dates} weight reports</div>}
        <div className="mt-auto flex flex-col gap-2 border-t border-edge px-3 py-2">
          {res?.warnings?.length ? (
            <div className="flex flex-wrap gap-1">
              {res.warnings.map((w, i) => (
                <span key={i} className="rounded bg-hover px-1.5 text-[11px] text-warn" title={JSON.stringify(w)}>
                  {w.code}
                  {w.member ? ` ${w.member}` : ""}
                </span>
              ))}
            </div>
          ) : null}
          <ErrorLine error={saveErr} />
          {!ro && (
            <div className="flex items-center gap-2">
              <button className="btn btn-primary" disabled={!dirty || !form.slug || !form.members.length} onClick={save}>
                {isNew ? "Create" : "Save"}
              </button>
              {dirty && !isNew && (
                <button className="btn" onClick={() => detail.data && (setForm({ slug: detail.data.slug, name: detail.data.name, spec: { ...DEFAULT_SPEC, ...detail.data.spec }, members: detail.data.members.map((m) => ({ ref: m.key, weight: m.weight })) }), setDirty(false))}>
                  Revert
                </button>
              )}
              <div className="flex-1" />
              {!isNew && (
                <button className="btn text-crit" onClick={remove}>
                  Delete
                </button>
              )}
            </div>
          )}
        </div>
      </aside>

      <section className="min-w-0 flex-1 overflow-auto p-3">
        <div className="mb-2 flex items-center gap-3 text-ink2">
          {res && (
            <>
              <span>
                {res.start} → <b className="text-ink">{res.asof}</b>
              </span>
              {dirty ? <span className="text-warn">preview</span> : res.cached === false ? null : <span className="text-muted">cached</span>}
              {detail.data?.parents.length ? <span>in {detail.data.parents.map((x) => `pf:${x}`).join(", ")}</span> : null}
              <Link className="btn ml-auto flex items-center" to="/w/$ws/compare" params={{ ws }} search={{ refs: isNew ? form.members.map((m) => m.ref).join(",") : [`pf:${slug}`, ...form.members.map((m) => m.ref)].slice(0, 8).join(",") }}>
                Compare
              </Link>
            </>
          )}
        </div>
        {resErr && !(resErr instanceof ApiError && resErr.status === 404) ? <ErrorLine error={resErr} /> : null}
        {res && (
          <div className="flex flex-col gap-3" style={{ opacity: (dirty ? preview.isFetching : saved.isFetching) ? 0.6 : 1 }}>
            <div className="card p-2">
              <NavChart dates={res.dates} series={[{ key: "pf", name: form.slug || "portfolio", color: p.series[0], returns: res.returns, width: 2 }]} navHeight={260} ddHeight={80} />
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
              <div className="card p-2">{membersOpt && <RangeChart option={membersOpt} height={266} />}</div>
              <div className="card p-2">{weightsOpt && <RangeChart option={weightsOpt} height={266} shadow={false} />}</div>
            </div>
            <div className="grid gap-3 xl:grid-cols-[360px_minmax(0,1fr)]">
              <div className="card overflow-auto">
                <MetricsTable cols={[{ key: "pf", name: form.slug || "portfolio", metrics: res.metrics }]} />
              </div>
              <div className="flex flex-col gap-3">
                <div className="card overflow-auto">
                  <MonthlyHeatmap data={res.monthly} />
                </div>
                <div className="card overflow-auto">
                  <DrawdownTable eps={res.top_dd} />
                </div>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[11px] text-muted">{label}</span>
      {children}
    </label>
  );
}
