import { useMemo, useRef, useState } from "react";
import { AXIS_FONT, catAxis, drawdownFrom, extentOf, legend, linAxisFit, logAxisFit, navFmt, navFmtFor, navFrom, pctFmt, RANGE_H, rangeZoom, tooltip, valAxis } from "../lib/chart";
import type { Palette } from "../lib/colors";
import { useWs } from "../lib/ctx";
import type { Num } from "../lib/types";
import { EChart, type ECharts } from "./EChart";
import { RangeToggle, Seg } from "./bits";

export interface NavSeries {
  key: string;
  name: string;
  color: string;
  returns: Num[]; // aligned to `dates`; null = not alive
  width?: number;
  dashed?: boolean;
}
export interface ExtraPanel {
  name: string;
  data: Num[];
  color: string;
  fmt?: (v: number) => string;
  bar?: boolean;
}

interface Props {
  dates: string[];
  series: NavSeries[];
  navHeight?: number;
  ddHeight?: number;
  extras?: ExtraPanel[];
  extraHeight?: number;
  markers?: { date: string; label: string }[];
  toolbar?: React.ReactNode;
  initialStart?: string | null; // open zoomed from this date, curves rebased there
  group?: string;
}

const PAD_TOP = 30;
const GAP = 22;
const XGAP = 34; // extra panels carry a title above the plot
const BOTTOM = 24;

function rebased(s: NavSeries, from: number): { nav: Num[]; dd: Num[] } {
  const sliced = s.returns.map((r, i) => (i < from ? null : r));
  const nav = navFrom(sliced);
  // start each curve at 1 on its first visible day
  const first = sliced.findIndex((r) => r !== null && r !== undefined);
  if (first >= 0) {
    const r0 = sliced[first] as number;
    for (let i = first; i < nav.length; i++) if (nav[i] !== null) nav[i] = (nav[i] as number) / (1 + r0);
  }
  return { nav, dd: drawdownFrom(nav) };
}

/** NAV axis fitted to the curves in the visible window: 1-2-5 steps when linear, fitted marks when log. */
function navAxis(p: Palette, log: boolean, navs: Num[][], from: number, to: number) {
  const [lo, hi] = extentOf(navs, from, to);
  if (!log) {
    const { min, max, interval } = linAxisFit(lo, hi);
    const fmt = navFmtFor(interval);
    return valAxis(p, 0, fmt, { scale: false, min, max, interval });
  }
  const { min, max, ticks } = logAxisFit(lo, hi);
  return valAxis(p, 0, navFmt, {
    type: "log",
    logBase: 10,
    scale: false,
    min,
    max,
    axisLabel: { color: p.muted, fontSize: AXIS_FONT, formatter: navFmt, customValues: ticks },
    axisTick: { show: false, customValues: ticks },
  });
}

/** Small panels: three labels at most; bars (summed flows) keep their zero baseline. */
function extraAxis(p: Palette, e: ExtraPanel, i: number) {
  return valAxis(p, i + 2, e.fmt ?? ((v: number) => String(+v.toPrecision(3))), {
    splitNumber: 2,
    scale: !e.bar,
    name: e.name,
    nameLocation: "end",
    nameGap: 8,
    nameTextStyle: { color: p.ink2, fontSize: AXIS_FONT, align: "left", padding: [0, 0, 0, -44] },
  });
}

export function NavChart({ dates, series, navHeight = 300, ddHeight = 90, extras = [], extraHeight = 76, markers = [], toolbar, initialStart, group }: Props) {
  const { palette: p } = useWs();
  const [log, setLog] = useState(false);
  const [range, setRange] = useState(false);
  const chartRef = useRef<ECharts | null>(null);
  const many = series.length > 4;
  const right = many || series.length < 2 ? 16 : 96;

  const from0 = useMemo(() => {
    if (!initialStart) return 0;
    const i = dates.findIndex((d) => d >= initialStart);
    return i > 0 ? i : 0;
  }, [dates, initialStart]);
  // visible window as category indexes; kept across Linear/Log and overlay changes, reset with new dates
  const win = useRef({ from: from0, to: dates.length - 1, dates });
  if (win.current.dates !== dates) win.current = { from: from0, to: dates.length - 1, dates };

  const base = PAD_TOP + navHeight + GAP + ddHeight + extras.length * (XGAP + extraHeight) + BOTTOM;

  const option = useMemo(() => {
    const { from, to } = win.current;
    const grids: object[] = [];
    let top = PAD_TOP;
    grids.push({ left: 52, right, top, height: navHeight });
    top += navHeight + GAP;
    grids.push({ left: 52, right, top, height: ddHeight });
    top += ddHeight;
    for (let i = 0; i < extras.length; i++) {
      top += XGAP;
      grids.push({ left: 52, right, top, height: extraHeight });
      top += extraHeight;
    }
    const nGrid = grids.length;
    const xAxes = grids.map((_, i) => catAxis(p, dates, i, i === nGrid - 1));
    const curves = series.map((s) => rebased(s, from));
    const yAxes: object[] = [
      navAxis(p, log, curves.map((c) => c.nav), from, to),
      valAxis(p, 1, pctFmt(0), { scale: false, max: 0 }),
      ...extras.map((e, i) => extraAxis(p, e, i)),
    ];
    const navSeries = series.map((s, si) => {
      const { nav } = curves[si];
      return {
        id: `nav:${s.key}`,
        type: "line",
        name: s.name,
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: nav,
        showSymbol: false,
        symbolSize: 8,
        lineStyle: { width: s.width ?? 1.5, color: s.color, type: s.dashed ? "dashed" : "solid" },
        itemStyle: { color: s.color },
        emphasis: { focus: "series" },
        endLabel: !many && series.length > 1 ? { show: true, formatter: "{a}", color: p.ink2, fontSize: AXIS_FONT, distance: 6 } : undefined,
        markLine:
          s === series[0] && markers.length
            ? {
                symbol: "none",
                silent: true,
                label: { formatter: "{b}", color: p.muted, fontSize: 10, position: "insideEndTop" },
                lineStyle: { color: p.muted, type: "solid", width: 1, opacity: 0.6 },
                data: markers.filter((m) => dates.includes(m.date)).map((m) => ({ name: m.label, xAxis: m.date })),
              }
            : undefined,
      };
    });
    const ddSeries = series.map((s, si) => {
      const { dd } = curves[si];
      return {
        id: `dd:${s.key}`,
        type: "line",
        name: s.name,
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: dd,
        showSymbol: false,
        lineStyle: { width: 1, color: s.color },
        itemStyle: { color: s.color },
        areaStyle: series.length === 1 ? { color: s.color, opacity: 0.12 } : undefined,
      };
    });
    const extraSeries = extras.map((e, i) => ({
      type: e.bar ? "bar" : "line",
      name: e.name,
      xAxisIndex: i + 2,
      yAxisIndex: i + 2,
      data: e.data,
      showSymbol: false,
      barMaxWidth: 3,
      lineStyle: { width: 1, color: e.color },
      itemStyle: { color: e.color },
    }));
    // the selector previews its first target series: the first curve over its whole life, not rebased
    // and drawn invisibly
    const shadow =
      range && series.length
        ? [{ id: "shadow", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: navFrom(series[0].returns), silent: true, showSymbol: false, lineStyle: { opacity: 0 }, tooltip: { show: false }, z: 0 }]
        : [];
    const axes = grids.map((_, i) => i);
    const fmtNav = (v: number) => v.toFixed(4);
    return {
      animation: false,
      // pages rather than running under the toolbar
      legend: series.length > 1 ? legend(p, { top: 4, left: 48, right: 190, data: series.map((s) => s.name) }) : undefined,
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      // "filter" so every panel's y range fits the zoomed window
      dataZoom: [
        { id: "in", type: "inside", xAxisIndex: axes, filterMode: "filter", minValueSpan: 10, startValue: from, endValue: to },
        rangeZoom(p, range, base, axes, { minValueSpan: 10, startValue: from, endValue: to }),
      ],
      tooltip: tooltip(p, (params: any[]) => {
        if (!params?.length) return "";
        const ax = params[0].axisIndex ?? params[0].componentIndex;
        const head = `<div style="color:${p.muted};margin-bottom:2px">${params[0].axisValue}</div>`;
        const rows = params
          .filter((x) => x.seriesId !== "shadow" && x.value !== null && x.value !== undefined && x.value !== "-")
          .map((x) => {
            const isDd = x.seriesId?.startsWith("dd:");
            const isNav = x.seriesId?.startsWith("nav:");
            const v = Number(x.value);
            const txt = isNav ? fmtNav(v) : isDd ? `${(v * 100).toFixed(2)}%` : String(+v.toPrecision(4));
            return `<div style="display:flex;gap:10px;justify-content:space-between"><span><span style="display:inline-block;width:10px;height:3px;border-radius:2px;background:${x.color};vertical-align:middle;margin-right:6px"></span>${x.seriesName}</span><b style="font-variant-numeric:tabular-nums">${txt}</b></div>`;
          })
          .join("");
        void ax;
        return head + rows;
      }),
      series: [...shadow, ...navSeries, ...ddSeries, ...extraSeries],
    };
  }, [dates, series, extras, navHeight, ddHeight, extraHeight, markers, log, range, base, p, many, right, from0]);

  const total = base + (range ? RANGE_H : 0);

  const onZoom = (_: unknown, chart: ECharts) => {
    const opt = chart.getOption() as any;
    const dz = opt.dataZoom?.[0];
    const from = Math.max(0, Math.floor(dz?.startValue ?? 0));
    const to = Math.min(dates.length - 1, Math.ceil(dz?.endValue ?? dates.length - 1));
    win.current = { from, to, dates };
    const curves = series.map((s) => rebased(s, from));
    chart.setOption(
      {
        series: series.flatMap((s, si) => [
          { id: `nav:${s.key}`, data: curves[si].nav },
          { id: `dd:${s.key}`, data: curves[si].dd },
        ]),
        // the NAV axis has an explicit range, so refit it to what is now visible
        yAxis: [navAxis(p, log, curves.map((c) => c.nav), from, to)],
      },
      { lazyUpdate: true },
    );
  };

  return (
    <div className="relative">
      <div className="absolute right-2 top-0.5 z-10 flex items-center gap-2">
        {toolbar}
        <Seg value={log ? "log" : "lin"} options={[["lin", "Linear"], ["log", "Log"]]} onChange={(v) => setLog(v === "log")} />
        <RangeToggle on={range} onChange={setRange} />
        <button className="btn" title="Reset zoom" onClick={() => chartRef.current?.dispatchAction({ type: "dataZoom", start: 0, end: 100 })}>
          ⟲
        </button>
      </div>
      <EChart option={option} height={total} group={group} onReady={(c) => (chartRef.current = c)} onEvents={{ datazoom: onZoom }} />
    </div>
  );
}

/** Merge per-entity (dates, values) into one union-calendar matrix. */
export function unionAlign(parts: { dates: string[]; values: Num[] }[]): { dates: string[]; cols: Num[][] } {
  const set = new Set<string>();
  for (const p of parts) for (const d of p.dates) set.add(d);
  const dates = [...set].sort();
  const pos = new Map(dates.map((d, i) => [d, i]));
  const cols = parts.map((p) => {
    const out: Num[] = new Array(dates.length).fill(null);
    p.dates.forEach((d, i) => (out[pos.get(d)!] = p.values[i]));
    // inside the life, missing days are flat (0 return)
    const first = out.findIndex((v) => v !== null);
    let last = -1;
    for (let i = out.length - 1; i >= 0; i--)
      if (out[i] !== null) {
        last = i;
        break;
      }
    for (let i = first; i >= 0 && i <= last; i++) if (out[i] === null) out[i] = 0;
    return out;
  });
  return { dates, cols };
}
