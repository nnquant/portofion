import { BarChart, HeatmapChart, LineChart } from "echarts/charts";
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import type { EChartsCoreOption } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { CHART_FONT } from "../lib/chart";

echarts.use([
  LineChart,
  BarChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  MarkLineComponent,
  AxisPointerComponent,
  CanvasRenderer,
]);

echarts.registerTheme("pfn", { textStyle: { fontFamily: CHART_FONT } });

export type ECharts = echarts.ECharts;

interface Props {
  option: EChartsCoreOption;
  height: number | string;
  onReady?: (chart: echarts.ECharts) => void;
  onEvents?: Record<string, (params: any, chart: echarts.ECharts) => void>;
  className?: string;
  group?: string; // charts in one group share zoom and crosshair
}

export function EChart({ option, height, onReady, onEvents, className, group }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const events = useRef(onEvents);
  events.current = onEvents;

  useEffect(() => {
    if (!el.current) return;
    const c = echarts.init(el.current, "pfn", { renderer: "canvas" });
    chart.current = c;
    if (group) {
      c.group = group;
      echarts.connect(group);
    }
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(el.current);
    onReady?.(c);
    return () => {
      ro.disconnect();
      c.dispose();
      chart.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true, lazyUpdate: true });
  }, [option]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !onEvents) return;
    const names = Object.keys(onEvents);
    for (const n of names) c.on(n, (p: any) => events.current?.[n]?.(p, c));
    return () => {
      for (const n of names) c.off(n);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onEvents ? Object.keys(onEvents).join() : ""]);

  return <div ref={el} className={className} style={{ height, width: "100%" }} />;
}
