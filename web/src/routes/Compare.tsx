import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ErrorLine, Seg } from "../components/bits";
import { EChart } from "../components/EChart";
import { NavChart, type NavSeries } from "../components/NavChart";
import { RefPicker } from "../components/RefPicker";
import { CorrMatrix, MetricsTable } from "../components/tables";
import { api, qs, ws as wsPath } from "../lib/api";
import { catAxis, line, tooltip, valAxis } from "../lib/chart";
import { slotVar, useSlots } from "../lib/colors";
import { useWs } from "../lib/ctx";
import type { CompareData, Num } from "../lib/types";

const MAX = 8;

function rollingSharpe(r: Num[], w: number, ppy: number): Num[] {
  const out: Num[] = new Array(r.length).fill(null);
  const buf: number[] = [];
  let s = 0;
  let s2 = 0;
  for (let i = 0; i < r.length; i++) {
    const v = r[i];
    if (v === null || v === undefined) {
      buf.length = 0;
      s = s2 = 0;
      continue;
    }
    buf.push(v);
    s += v;
    s2 += v * v;
    if (buf.length > w) {
      const x = buf.shift()!;
      s -= x;
      s2 -= x * x;
    }
    if (buf.length === w) {
      const m = s / w;
      const sd = Math.sqrt(Math.max((s2 - w * m * m) / (w - 1), 0));
      out[i] = sd > 1e-12 ? (m / sd) * Math.sqrt(ppy) : null;
    }
  }
  return out;
}

export function Compare() {
  const { slug: ws, palette: p } = useWs();
  const search = useSearch({ from: "/w/$ws/compare" });
  const nav = useNavigate({ from: "/w/$ws/compare" });
  const refs = useMemo(() => (search.refs ?? "").split(",").filter(Boolean).slice(0, MAX), [search.refs]);
  const freq = (search.freq ?? "D") as "D" | "W" | "M";
  const [mwin, setMwin] = useState<"common" | "own">("common");
  const slots = useSlots(refs, `pfn.slots.cmp.${ws}`);
  const setRefs = (r: string[]) => nav({ search: (s: any) => ({ ...s, refs: r.length ? r.join(",") : undefined }), replace: true });

  const q = useQuery({
    queryKey: ["compare", ws, refs.join(","), freq],
    queryFn: () => api<CompareData>(wsPath(ws, `/compare${qs({ refs: refs.join(","), corr_freq: freq, rolling: 63 })}`)),
    enabled: refs.length > 0,
  });
  const d = q.data;
  const color = (k: string) => p.series[slots[k] ?? 0];

  const navSeries: NavSeries[] = useMemo(
    () => (d ? d.keys.map((k) => ({ key: k, name: k, color: color(k), returns: d.returns[k] })) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [d, slots, p],
  );

  const rollOpt = useMemo(() => {
    if (!d) return null;
    const ppy = Math.max(...Object.values(d.periods));
    const sh = d.keys.map((k) => line(k, rollingSharpe(d.returns[k], 126, d.periods[k] ?? ppy), color(k)));
    const rc = d.keys.slice(1).map((k) => ({ ...line(k, d.rolling_corr[k] ?? [], color(k)), xAxisIndex: 1, yAxisIndex: 1 }));
    return {
      animation: false,
      grid: [
        { left: 44, right: 12, top: 10, height: 120 },
        { left: 44, right: 12, top: 160, height: 100 },
      ],
      xAxis: [catAxis(p, d.dates, 0, false), catAxis(p, d.dates, 1, true)],
      yAxis: [
        valAxis(p, 0, (v: number) => v.toFixed(1), { name: "Sharpe 126d", nameTextStyle: { color: p.ink2, align: "left", padding: [0, 0, 0, -36] }, nameGap: 4 }),
        valAxis(p, 1, (v: number) => v.toFixed(1), { min: -1, max: 1, scale: false, name: d.keys.length > 1 ? `Corr 63d vs ${d.keys[0]}` : "", nameTextStyle: { color: p.ink2, align: "left", padding: [0, 0, 0, -36] }, nameGap: 4 }),
      ],
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      dataZoom: [{ type: "inside", xAxisIndex: [0, 1], filterMode: "none", startValue: Math.max(0, d.dates.findIndex((x) => x >= (d.common_start ?? ""))) }],
      tooltip: tooltip(p, (ps: any[]) => {
        if (!ps?.length) return "";
        return (
          `<div style="color:${p.muted}">${ps[0].axisValue}</div>` +
          ps
            .filter((x) => x.value !== null && x.value !== undefined)
            .map((x) => `<div style="display:flex;gap:10px;justify-content:space-between"><span><span style="display:inline-block;width:10px;height:3px;border-radius:2px;background:${x.color};vertical-align:middle;margin-right:6px"></span>${x.seriesName}</span><b>${Number(x.value).toFixed(2)}</b></div>`)
            .join("")
        );
      }),
      series: [...sh, ...rc],
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d, slots, p]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-none flex-wrap items-center gap-2 border-b border-edge bg-surface px-3 py-1.5">
        {refs.map((r) => (
          <span key={r} className="inline-flex h-6 items-center gap-1.5 rounded border border-edge pl-2 pr-1">
            <span className="swatch" style={{ background: slotVar(slots[r] ?? 0) }} />
            {r}
            <button className="px-1 text-muted hover:text-ink" onClick={() => setRefs(refs.filter((x) => x !== r))}>
              ×
            </button>
          </span>
        ))}
        {refs.length < MAX && <RefPicker exclude={refs} onPick={(r) => setRefs([...refs, r])} />}
        <div className="flex-1" />
        <Seg value={freq} options={[["D", "Daily"], ["W", "Weekly"], ["M", "Monthly"]]} onChange={(f) => nav({ search: (s: any) => ({ ...s, freq: f === "D" ? undefined : f }), replace: true })} />
        <button
          className="btn"
          disabled={!refs.length}
          onClick={() => nav({ to: "/w/$ws/p/$slug", params: { ws, slug: "new" }, search: { refs: refs.join(",") } })}
        >
          Save as portfolio
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        <ErrorLine error={q.error} />
        {d && (
          <div className="flex flex-col gap-3">
            <div className="card p-2">
              <NavChart dates={d.dates} series={navSeries} navHeight={320} ddHeight={100} initialStart={d.common_start} group={`cmp:${refs.join()}`} />
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
              <div className="card overflow-auto">
                <div className="flex items-center justify-end border-b border-line px-2 py-1">
                  <Seg
                    value={mwin}
                    options={[["common", d.common_start && d.common_end ? `${d.common_start} → ${d.common_end}` : "Common"], ["own", "Full history"]]}
                    onChange={setMwin}
                  />
                </div>
                <MetricsTable
                  cols={d.keys.map((k) => ({ key: k, name: k, color: color(k), metrics: (mwin === "common" ? d.metrics_common : d.metrics_own)[k] }))}
                />
              </div>
              <div className="flex flex-col gap-3">
                <div className="card overflow-auto">
                  <CorrMatrix keys={d.corr.keys} corr={d.corr.corr} n={d.corr.n} colors={Object.fromEntries(d.keys.map((k) => [k, color(k)]))} />
                </div>
                <div className="card p-2">{rollOpt && <EChart option={rollOpt} height={290} group={`cmp:${refs.join()}`} />}</div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
