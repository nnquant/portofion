import type { Palette } from "./colors";
import type { Num } from "./types";

export const AXIS_FONT = 11;

export function baseText(p: Palette) {
  return { color: p.ink2, fontSize: AXIS_FONT, fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" };
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
    sampling: "lttb" as const,
    lineStyle: { width: 1.5, color },
    itemStyle: { color },
    emphasis: { focus: "series" as const, lineStyle: { width: 2 } },
    connectNulls: false,
    ...extra,
  };
}

export const pctFmt = (d = 0) => (v: number) => `${(v * 100).toFixed(d)}%`;

export function zoom(xAxisIndex: number[] | number, start = 0) {
  return [
    { type: "inside" as const, xAxisIndex, start, end: 100, filterMode: "none" as const },
  ];
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
