import { useEffect, useRef, useState } from "react";
import { copyText } from "../lib/clipboard";
import { sign } from "../lib/format";
import type { Num, Stage } from "../lib/types";

export function Spark({ data, width = 84, height = 18, color = "var(--series-1)" }: { data: number[]; width?: number; height?: number; color?: string }) {
  if (!data || data.length < 2) return <svg width={width} height={height} />;
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of data) {
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  const span = hi - lo || 1;
  const pts = data
    .map((v, i) => `${((i / (data.length - 1)) * (width - 2) + 1).toFixed(1)},${(height - 2 - ((v - lo) / span) * (height - 4)).toFixed(1)}`)
    .join(" ");
  const base = height - 2 - ((1 - lo) / span) * (height - 4);
  return (
    <svg width={width} height={height} className="block">
      {base > 0 && base < height && <line x1={0} x2={width} y1={base} y2={base} stroke="var(--grid)" strokeWidth={1} />}
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.25} strokeLinejoin="round" />
    </svg>
  );
}

const STAGE_COLOR: Record<Stage, string> = {
  live: "var(--series-3)",
  paper: "var(--series-1)",
  research: "var(--text-muted)",
  retired: "var(--edge)",
};

export function StageDot({ stage, withLabel = true }: { stage: Stage; withLabel?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5" title={stage}>
      <span className="dot" style={{ background: STAGE_COLOR[stage] }} />
      {withLabel && <span className="text-ink2">{stage}</span>}
    </span>
  );
}

export function Signed({ v, children }: { v: Num | undefined; children: React.ReactNode }) {
  return <span className={sign(v)}>{children}</span>;
}

export function CopyButton({ text, label = "Copy", className = "btn" }: { text: string; label?: string; className?: string }) {
  const [state, setState] = useState<"ok" | "fail" | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const click = async () => {
    const ok = await copyText(text);
    setState(ok ? "ok" : "fail");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState(null), 1500);
  };
  // all three labels share one grid cell so the button keeps its width
  const cell = (s: typeof state, t: string) => (
    <span style={{ gridArea: "1 / 1" }} className={state === s ? "" : "invisible"}>
      {t}
    </span>
  );
  return (
    <button className={`${className} inline-grid place-items-center`} onClick={click}>
      {cell(null, label)}
      {cell("ok", "Copied")}
      {cell("fail", "Ctrl+C")}
    </button>
  );
}

export function Seg<T extends string>({ value, options, onChange }: { value: T; options: (T | [T, string])[]; onChange: (v: T) => void }) {
  return (
    <div className="seg">
      {options.map((o) => {
        const [v, label] = Array.isArray(o) ? o : [o, o];
        return (
          <button key={v} aria-pressed={v === value} onClick={() => onChange(v)}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

/** Shows or hides a chart's date range selector. */
export function RangeToggle({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  return (
    <button className="btn btn-icon" aria-pressed={on} title="Date range" onClick={() => onChange(!on)}>
      <svg width="16" height="12" viewBox="0 0 16 12" fill="none" stroke="currentColor" strokeLinecap="round">
        <rect x="0.75" y="3.75" width="14.5" height="4.5" rx="1" strokeWidth="1.2" opacity="0.55" />
        <path d="M5 1.5v9M11 1.5v9" strokeWidth="1.7" />
      </svg>
    </button>
  );
}

export function Popover({ button, children, align = "left" }: { button: React.ReactNode; children: React.ReactNode; align?: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const on = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", on);
    return () => window.removeEventListener("mousedown", on);
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <span onClick={() => setOpen((o) => !o)}>{button}</span>
      {open && (
        <div className={`card absolute top-[28px] z-40 max-h-[60vh] min-w-[180px] overflow-auto p-1 shadow-lg ${align === "right" ? "right-0" : "left-0"}`}>
          {children}
        </div>
      )}
    </div>
  );
}

export function ErrorLine({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as { message?: string; hint?: string };
  return (
    <div className="text-crit">
      {e.message ?? String(error)}
      {e.hint ? <span className="text-muted"> · {e.hint}</span> : null}
    </div>
  );
}

export function Kv({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <>
      <div className="text-muted">{k}</div>
      <div className="num text-left">{children}</div>
    </>
  );
}
