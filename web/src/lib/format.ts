import type { Num } from "./types";

const isNum = (v: Num | undefined): v is number => typeof v === "number" && Number.isFinite(v);

export const pct = (v: Num | undefined, d = 2) => (isNum(v) ? `${(v * 100).toFixed(d)}%` : "—");
export const ratio = (v: Num | undefined, d = 2) => (isNum(v) ? v.toFixed(d) : "—");
export const int = (v: Num | undefined) => (isNum(v) ? Math.round(v).toLocaleString("en-US") : "—");
export const bp = (v: Num | undefined) => (isNum(v) ? `${(v * 1e4).toFixed(1)}bp` : "—");

export function compact(v: Num | undefined): string {
  if (!isNum(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  if (a >= 100) return v.toFixed(0);
  if (a >= 1) return v.toFixed(2);
  return v.toPrecision(3);
}

/** css class for a signed value */
export const sign = (v: Num | undefined) => (isNum(v) ? (v > 0 ? "up" : v < 0 ? "down" : "") : "");

export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (s < 90) return `${Math.max(1, Math.round(s))}s`;
  if (s < 5400) return `${Math.round(s / 60)}m`;
  if (s < 129600) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

export type Fmt = "pct" | "ratio" | "int" | "date" | "compact";
export function fmt(v: Num | string | undefined, kind: Fmt): string {
  if (kind === "date") return (v as string) ?? "—";
  const n = v as Num;
  switch (kind) {
    case "pct":
      return pct(n);
    case "ratio":
      return ratio(n);
    case "int":
      return int(n);
    case "compact":
      return compact(n);
  }
}

export interface MetricDef {
  key: string;
  label: string;
  fmt: Fmt;
  signed?: boolean;
}

export const METRIC_DEFS: MetricDef[] = [
  { key: "ann_ret", label: "AnnRet", fmt: "pct", signed: true },
  { key: "cagr", label: "CAGR", fmt: "pct", signed: true },
  { key: "total_ret", label: "Total", fmt: "pct", signed: true },
  { key: "ann_vol", label: "Vol", fmt: "pct" },
  { key: "sharpe", label: "Sharpe", fmt: "ratio", signed: true },
  { key: "sortino", label: "Sortino", fmt: "ratio", signed: true },
  { key: "calmar", label: "Calmar", fmt: "ratio", signed: true },
  { key: "max_dd", label: "MaxDD", fmt: "pct", signed: true },
  { key: "max_dd_days", label: "MaxDD days", fmt: "int" },
  { key: "longest_dd_days", label: "Longest DD days", fmt: "int" },
  { key: "cur_dd", label: "Current DD", fmt: "pct", signed: true },
  { key: "cur_dd_days", label: "Current DD days", fmt: "int" },
  { key: "win_rate", label: "Win rate", fmt: "pct" },
  { key: "profit_factor", label: "Profit factor", fmt: "ratio" },
  { key: "skew", label: "Skew", fmt: "ratio" },
  { key: "kurt", label: "Kurtosis", fmt: "ratio" },
  { key: "var", label: "VaR", fmt: "pct", signed: true },
  { key: "cvar", label: "CVaR", fmt: "pct", signed: true },
  { key: "best", label: "Best day", fmt: "pct", signed: true },
  { key: "worst", label: "Worst day", fmt: "pct", signed: true },
  { key: "n", label: "N", fmt: "int" },
  { key: "first_date", label: "Start", fmt: "date" },
  { key: "last_date", label: "Last", fmt: "date" },
];
