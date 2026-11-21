import { useMemo, useRef, useState } from "react";
import { RANGE_H, rangeZoom } from "../lib/chart";
import { useWs } from "../lib/ctx";
import { RangeToggle } from "./bits";
import { EChart, type ECharts } from "./EChart";

type Opt = Record<string, any>;
type Win = { dates: unknown; start: number; end: number };

const WINDOW_KEYS = ["start", "end", "startValue", "endValue"] as const;

/**
 * A dated chart with a range selector under it, shown from a button at its top right. The first
 * dataZoom in `option` (the inside one) sets the initial window; the window then survives toggling
 * the selector and option rebuilds over the same dates.
 */
export function RangeChart({ option, height, group, shadow = true }: { option: Opt; height: number; group?: string; shadow?: boolean }) {
  const { palette: p } = useWs();
  const [range, setRange] = useState(false);
  const dates = [option.xAxis].flat()[0]?.data;
  const win = useRef<Win | null>(null);
  if (win.current && win.current.dates !== dates) win.current = null;

  const full = useMemo(() => {
    const w = win.current;
    const inside: Opt = [option.dataZoom ?? []].flat()[0] ?? { type: "inside", filterMode: "filter" };
    const at: Opt = w ? { start: w.start, end: w.end } : Object.fromEntries(WINDOW_KEYS.filter((k) => inside[k] !== undefined).map((k) => [k, inside[k]]));
    const base = Object.fromEntries(Object.entries(inside).filter(([k]) => !(WINDOW_KEYS as readonly string[]).includes(k)));
    const axes = [option.xAxis].flat().map((_, i) => i);
    // the selector adds height below; grids anchored to the bottom move up with it so plots keep their size
    const grid = [option.grid].flat().map((g: Opt) => (range && typeof g?.bottom === "number" ? { ...g, bottom: g.bottom + RANGE_H } : g));
    return {
      ...option,
      grid,
      dataZoom: [{ ...base, id: "in", ...at }, rangeZoom(p, range, height, axes, { ...at, showDataShadow: shadow ? "auto" : false })],
    };
  }, [option, range, p, height, shadow]);

  const onZoom = (_: unknown, c: ECharts) => {
    const z = (c.getOption() as Opt).dataZoom?.[0];
    if (z && typeof z.start === "number") win.current = { dates, start: z.start, end: z.end };
  };

  return (
    <div className="relative">
      <div className="absolute right-0 top-0 z-10">
        <RangeToggle on={range} onChange={setRange} />
      </div>
      <EChart option={full} height={height + (range ? RANGE_H : 0)} group={group} onEvents={{ datazoom: onZoom }} />
    </div>
  );
}
