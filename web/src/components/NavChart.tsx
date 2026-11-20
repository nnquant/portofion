import { useMemo, useRef, useState } from "react";
import { AXIS_FONT, catAxis, drawdownFrom, navFrom, pctFmt, tooltip, valAxis } from "../lib/chart";
import { useWs } from "../lib/ctx";
import type { Num } from "../lib/types";
import { EChart, type ECharts } from "./EChart";
import { Seg } from "./bits";

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

export function NavChart({ dates, series, navHeight = 300, ddHeight = 90, extras = [], extraHeight = 64, markers = [], toolbar, initialStart, group }: Props) {
  const { palette: p } = useWs();
  const [log, setLog] = useState(false);
  const chartRef = useRef<ECharts | null>(null);
  const many = series.length > 4;
  const right = many || series.length < 2 ? 16 : 96;

  const from0 = useMemo(() => {
    if (!initialStart) return 0;
    const i = dates.findIndex((d) => d >= initialStart);
    return i > 0 ? i : 0;
  }, [dates, initialStart]);

  const option = useMemo(() => {
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
    const yAxes: object[] = [
      valAxis(p, 0, (v) => v.toFixed(2), log ? { type: "log", logBase: 10 } : {}),
      valAxis(p, 1, pctFmt(0), { scale: false, max: 0 }),
      ...extras.map((e, i) =>
        valAxis(p, i + 2, e.fmt ?? ((v: number) => String(+v.toPrecision(3))), {
          name: e.name,
          nameLocation: "end",
          nameGap: 6,
          nameTextStyle: { color: p.ink2, fontSize: AXIS_FONT, align: "left", padding: [0, 0, 0, -44] },
        }),
      ),
    ];
    const navSeries = series.map((s) => {
      const { nav } = rebased(s, from0);
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
    const ddSeries = series.map((s) => {
      const { dd } = rebased(s, from0);
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
    const fmtNav = (v: number) => v.toFixed(4);
    return {
      animation: false,
      textStyle: { fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
      legend:
        series.length > 1
          ? {
              top: 4,
              left: 48,
              icon: "roundRect",
              itemWidth: 12,
              itemHeight: 3,
              itemGap: 14,
              textStyle: { color: p.ink2, fontSize: AXIS_FONT },
              data: series.map((s) => s.name),
            }
          : undefined,
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      dataZoom: [{ type: "inside", xAxisIndex: grids.map((_, i) => i), filterMode: "none", minValueSpan: 10, startValue: from0 }],
      tooltip: tooltip(p, (params: any[]) => {
        if (!params?.length) return "";
        const ax = params[0].axisIndex ?? params[0].componentIndex;
        const head = `<div style="color:${p.muted};margin-bottom:2px">${params[0].axisValue}</div>`;
        const rows = params
          .filter((x) => x.value !== null && x.value !== undefined && x.value !== "-")
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
      series: [...navSeries, ...ddSeries, ...extraSeries],
    };
  }, [dates, series, extras, navHeight, ddHeight, extraHeight, markers, log, p, many, right, from0]);

  const total = PAD_TOP + navHeight + GAP + ddHeight + extras.length * (XGAP + extraHeight) + BOTTOM;

  const onZoom = (_: unknown, chart: ECharts) => {
    const opt = chart.getOption() as any;
    const dz = opt.dataZoom?.[0];
    const from = Math.max(0, Math.floor(dz?.startValue ?? 0));
    chart.setOption(
      {
        series: series.flatMap((s) => {
          const { nav, dd } = rebased(s, from);
          return [
            { id: `nav:${s.key}`, data: nav },
            { id: `dd:${s.key}`, data: dd },
          ];
        }),
      },
      { lazyUpdate: true },
    );
  };

  return (
    <div className="relative">
      <div className="absolute right-2 top-0.5 z-10 flex items-center gap-2">
        {toolbar}
        <Seg value={log ? "log" : "lin"} options={[["lin", "Linear"], ["log", "Log"]]} onChange={(v) => setLog(v === "log")} />
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
