import type { Palette } from "./colors";
import type { Num } from "./types";

export const AXIS_FONT = 11;
export const CHART_FONT = '"Noto Sans Variable", "Noto Sans", "Noto Sans SC", "Microsoft YaHei UI", system-ui, sans-serif';

export function baseText(p: Palette) {
  return { color: p.ink2, fontSize: AXIS_FONT, fontFamily: CHART_FONT };
}

export function catAxis(p: Palette, dates: string[], gridIndex = 0, showLabels = true) {
  return {
    type: "category" as const,
    data: dates,
    gridIndex,
    boundaryGap: false,
    axisLine: { lineStyle: { color: p.edge } },
    axisTick: { show: false },
    axisLabel: { show: showLabels, color: p.muted, fontSize: AXIS_FONT, hideOverlap: true },
    axisPointer: { label: { show: false } },
  };
}

export function valAxis(p: Palette, gridIndex = 0, fmt?: (v: number) => string, extra: object = {}) {
  return {
    type: "value" as const,
    gridIndex,
    scale: true,
    splitNumber: 3,
    axisLine: { show: false },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: p.grid, width: 1 } },
    axisLabel: { color: p.muted, fontSize: AXIS_FONT, formatter: fmt },
    ...extra,
  };
}

export function tooltip(p: Palette, formatter?: (params: any) => string) {
  return {
    trigger: "axis" as const,
    confine: true,
    backgroundColor: p.surface,
    borderColor: p.edge,
    borderWidth: 1,
    padding: [6, 8],
    textStyle: { ...baseText(p), color: p.ink },
    axisPointer: { type: "line" as const, lineStyle: { color: p.muted, width: 1 } },
    order: "valueDesc" as const,
    formatter,
  };
}

export function line(name: string, data: Num[], color: string, extra: object = {}) {
  return {
    type: "line" as const,
    name,
    data,
    showSymbol: false,
    symbolSize: 8,
    lineStyle: { width: 1.5, color },
    itemStyle: { color },
    emphasis: { focus: "series" as const, lineStyle: { width: 2 } },
    connectNulls: false,
    ...extra,
  };
}

export const pctFmt = (d = 0) => (v: number) => `${(v * 100).toFixed(d)}%`;

/** NAV labels: two decimals below 10, fewer as values grow. */
export const navFmt = (v: number) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));

/** NAV labels with enough decimals for a given tick step (0.005 steps need three). */
export const navFmtFor = (step: number) => {
  const d = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  return (v: number) => (d > 2 && v < 10 ? v.toFixed(d) : navFmt(v));
};

/** A step of 1, 2 or 5 × 10^k, the smallest at least `raw`. */
export function niceStep(raw: number): number {
  const e = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * e;
}

/**
 * Range and step for a linear axis over [lo, hi] in 1-2-5 steps. ECharts' own nice range also uses
 * steps of 3 (0.93, 0.96, 0.99), which read worse.
 */
export function linAxisFit(lo: number, hi: number, target = 5): { min: number; max: number; interval: number } {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, interval: 0.25 };
  if (hi - lo < 1e-9) {
    lo -= Math.abs(lo) * 0.01 || 0.01;
    hi += Math.abs(hi) * 0.01 || 0.01;
  }
  const interval = niceStep((hi - lo) / target);
  const round = (x: number) => +x.toPrecision(12);
  return { min: round(Math.floor(lo / interval + 1e-9) * interval), max: round(Math.ceil(hi / interval - 1e-9) * interval), interval };
}

/** Carry the last value over gaps inside a series' life (e.g. weekends for a 5-day series on a 7-day calendar). */
export function ffillInside(a: Num[]): Num[] {
  let last: Num = null;
  let end = a.length - 1;
  while (end >= 0 && (a[end] === null || a[end] === undefined)) end--;
  return a.map((v, i) => {
    if (v !== null && v !== undefined) return (last = v);
    return i <= end ? last : null;
  });
}

/**
 * Range and tick values for a log axis fitted to [lo, hi]. ECharts would widen a log axis to whole
 * powers of ten (0.1–10 for a NAV between 0.6 and 2) and tick only those, flattening the curve.
 */
export function logAxisFit(lo: number, hi: number, target = 5): { min: number; max: number; ticks: number[] } {
  if (!(lo > 0) || !(hi >= lo)) return { min: 0.5, max: 2, ticks: [0.5, 1, 2] };
  const L = Math.log(lo);
  const H = Math.log(hi);
  const pad = Math.max((H - L) * 0.04, 0.004);
  const min = Math.exp(L - pad);
  const max = Math.exp(H + pad);
  // wide ranges: 1-2-5 style marks per decade, getting denser until there are enough
  for (const set of [[1], [1, 2, 5], [1, 1.5, 2, 3, 5, 7]]) {
    const ticks: number[] = [];
    for (let e = Math.floor(Math.log10(min)); e <= Math.ceil(Math.log10(max)); e++)
      for (const m of set) {
        const v = +(m * 10 ** e).toPrecision(6);
        if (v >= min && v <= max) ticks.push(v);
      }
    if (ticks.length >= target - 1) return { min, max, ticks };
  }
  // narrow ranges (under ~half a decade): evenly stepped values
  const step = niceStep((max - min) / target);
  const ticks: number[] = [];
  for (let i = Math.ceil(min / step); i * step <= max; i++) ticks.push(+(i * step).toPrecision(6));
  return { min, max, ticks };
}

/** Lowest and highest non-null value over columns, within [from, to]. */
export function extentOf(cols: Num[][], from = 0, to = Infinity): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const c of cols)
    for (let i = from; i <= Math.min(to, c.length - 1); i++) {
      const v = c[i];
      if (v === null || v === undefined) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  return [lo, hi];
}

export function zoom(xAxisIndex: number[] | number, start = 0) {
  return [
    { type: "inside" as const, xAxisIndex, start, end: 100, filterMode: "filter" as const },
  ];
}

/** Room a range selector takes under the plots. */
export const RANGE_H = 44;

/**
 * Range selector under the plots: the whole history in miniature with draggable ends, and dragging
 * across it picks a new window. It stays in the option while hidden so charts linked by `group` keep
 * following it. Inset from the plot so the dates beside its handles fit.
 */
export function rangeZoom(p: Palette, show: boolean, top: number, xAxisIndex: number[] | number, extra: object = {}) {
  return {
    id: "range",
    type: "slider" as const,
    show,
    xAxisIndex,
    filterMode: "filter" as const,
    top: top + 6,
    height: 22,
    left: 76,
    right: 76,
    borderColor: p.edge,
    borderRadius: 3,
    backgroundColor: "transparent",
    fillerColor: p.sel,
    dataBackground: { lineStyle: { color: p.muted, width: 1, opacity: 0.6 }, areaStyle: { color: p.muted, opacity: 0.1 } },
    selectedDataBackground: { lineStyle: { color: p.series[0], width: 1 }, areaStyle: { color: p.series[0], opacity: 0.14 } },
    handleStyle: { color: p.surface, borderColor: p.muted },
    moveHandleSize: 6,
    moveHandleStyle: { color: p.edge, opacity: 1 },
    brushStyle: { color: p.series[0], opacity: 0.15 },
    handleLabel: { show: true },
    textStyle: { color: p.ink2, fontSize: AXIS_FONT },
    emphasis: { handleLabel: { show: true }, handleStyle: { borderColor: p.ink2 }, moveHandleStyle: { color: p.muted, opacity: 1 } },
    ...extra,
  };
}

/** One-line legend that pages instead of wrapping over the plot. */
export function legend(p: Palette, extra: object = {}) {
  return {
    type: "scroll" as const,
    top: 0,
    left: 40,
    right: 8,
    icon: "roundRect",
    itemWidth: 12,
    itemHeight: 3,
    itemGap: 14,
    textStyle: { color: p.ink2, fontSize: AXIS_FONT },
    pageIconColor: p.ink2,
    pageIconInactiveColor: p.grid,
    pageIconSize: 9,
    pageTextStyle: { color: p.muted, fontSize: 10 },
    ...extra,
  };
}

/** Rebase NAV curves so each starts at 1 on the first visible index. */
export function rebase(nav: Num[], from: number): Num[] {
  let base: number | null = null;
  for (let i = from; i < nav.length; i++) {
    if (nav[i] !== null && nav[i] !== undefined) {
      base = nav[i] as number;
      break;
    }
  }
  if (!base) return nav;
  return nav.map((v, i) => (i < from || v === null ? null : (v as number) / base!));
}

/** NAV from aligned returns (null outside life). */
export function navFrom(returns: Num[]): Num[] {
  let acc = 1;
  let started = false;
  return returns.map((r) => {
    if (r === null || r === undefined) return started ? acc : null;
    started = true;
    acc *= 1 + r;
    return acc;
  });
}

export function lastNonNull(a: Num[]): number {
  for (let i = a.length - 1; i >= 0; i--) if (a[i] !== null && a[i] !== undefined) return i;
  return -1;
}

/** Underwater curve from a NAV array (nulls preserved). */
export function drawdownFrom(nav: Num[]): Num[] {
  let peak = 1;
  return nav.map((v) => {
    if (v === null || v === undefined) return null;
    peak = Math.max(peak, v);
    return v / peak - 1;
  });
}
