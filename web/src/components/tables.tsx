import { fmt, METRIC_DEFS, sign } from "../lib/format";
import type { Episode, Metrics, Monthly, Num } from "../lib/types";

export interface MetricCol {
  key: string;
  name: string;
  color?: string;
  metrics: Partial<Metrics> | null | undefined;
}

const DEFAULT_KEYS = [
  "ann_ret", "cagr", "ann_vol", "sharpe", "sortino", "calmar", "max_dd", "max_dd_days", "cur_dd",
  "win_rate", "profit_factor", "skew", "kurt", "var", "cvar", "best", "worst", "n", "first_date", "last_date",
];

export function MetricsTable({ cols, keys = DEFAULT_KEYS }: { cols: MetricCol[]; keys?: string[] }) {
  const defs = keys.map((k) => METRIC_DEFS.find((d) => d.key === k)!).filter(Boolean);
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th />
          {cols.map((c) => (
            <th key={c.key} className="num">
              <span className="inline-flex items-center gap-1.5">
                {c.color && <span className="swatch" style={{ background: c.color }} />}
                {c.name}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {defs.map((d) => (
          <tr key={d.key}>
            <td className="text-ink2">{d.label}</td>
            {cols.map((c) => {
              const v = (c.metrics as any)?.[d.key] as Num | string | undefined;
              return (
                <td key={c.key} className={`num ${d.signed && typeof v === "number" ? sign(v) : ""}`}>
                  {fmt(v, d.fmt)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Year"];

function tint(v: number | null, scale: number): React.CSSProperties | undefined {
  if (v === null || v === 0 || !scale) return undefined;
  const a = Math.min(Math.abs(v) / scale, 1) * 0.42 + 0.04;
  return { background: `rgba(var(${v > 0 ? "--up-bg" : "--down-bg"}), ${a.toFixed(3)})` };
}

export function MonthlyHeatmap({ data }: { data: Monthly }) {
  const flat = data.rows.flatMap((r) => r.slice(0, 12)).filter((v): v is number => v !== null).map(Math.abs).sort((a, b) => a - b);
  const scale = flat.length ? flat[Math.floor(flat.length * 0.95)] || flat[flat.length - 1] : 0;
  const yflat = data.rows.map((r) => Math.abs(r[12] ?? 0)).sort((a, b) => a - b);
  const yscale = yflat.length ? yflat[Math.floor(yflat.length * 0.95)] || yflat[yflat.length - 1] : 0;
  return (
    <table className="tbl text-[11.5px]">
      <thead>
        <tr>
          <th />
          {MONTHS.map((m) => (
            <th key={m} className="num">
              {m}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {[...data.years].reverse().map((y) => {
          const row = data.rows[data.years.indexOf(y)];
          return (
            <tr key={y}>
              <td className="text-ink2">{y}</td>
              {row.map((v, i) => (
                <td key={i} className={`num ${i === 12 ? "font-semibold" : ""}`} style={tint(v, i === 12 ? yscale : scale)}>
                  {v === null ? "" : (v * 100).toFixed(1)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function DrawdownTable({ eps }: { eps: Episode[] }) {
  return (
    <table className="tbl">
      <thead>
        <tr>
          <th className="num">Depth</th>
          <th>Peak</th>
          <th>Trough</th>
          <th>Recovery</th>
          <th className="num">Days</th>
        </tr>
      </thead>
      <tbody>
        {eps.map((e) => (
          <tr key={e.peak}>
            <td className="num down">{(e.depth * 100).toFixed(2)}%</td>
            <td>{e.peak}</td>
            <td>{e.trough}</td>
            <td className={e.recovery ? "" : "text-warn"}>{e.recovery ?? "open"}</td>
            <td className="num">{e.days}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function CorrMatrix({ keys, corr, n, names, colors }: { keys: string[]; corr: Num[][]; n: number[][]; names?: Record<string, string>; colors?: Record<string, string> }) {
  const bg = (v: Num) => {
    if (v === null) return undefined;
    const a = Math.min(Math.abs(v), 1) * 0.55;
    return { background: `rgba(${v >= 0 ? "227, 73, 72" : "var(--blue-bg)"}, ${a.toFixed(3)})` };
  };
  return (
    <table className="tbl text-[11.5px]">
      <thead>
        <tr>
          <th />
          {keys.map((k, j) => (
            <th key={k} className="num" title={k}>
              <span className="inline-flex items-center gap-1">
                {colors?.[k] && <span className="swatch" style={{ background: colors[k] }} />}
                {j + 1}
              </span>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {keys.map((k, i) => (
          <tr key={k}>
            <td className="max-w-[220px] overflow-hidden text-ellipsis" title={k}>
              <span className="inline-flex items-center gap-1.5">
                <span className="num w-4 text-muted">{i + 1}</span>
                {colors?.[k] && <span className="swatch" style={{ background: colors[k] }} />}
                {names?.[k] ?? k}
              </span>
            </td>
            {keys.map((k2, j) => (
              <td key={k2} className="num" style={i === j ? undefined : bg(corr[i][j])} title={`n=${n[i][j]}`}>
                {i === j ? <span className="text-muted">1</span> : corr[i][j] === null ? <span className="text-muted">·</span> : (corr[i][j] as number).toFixed(2)}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
