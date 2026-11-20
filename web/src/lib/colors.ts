import { useEffect, useMemo, useState } from "react";

export const N_SLOTS = 8;

export function readVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface Palette {
  series: string[];
  ink: string;
  ink2: string;
  muted: string;
  grid: string;
  edge: string;
  surface: string;
  up: string;
  down: string;
  dark: boolean;
}

function read(): Palette {
  return {
    series: Array.from({ length: N_SLOTS }, (_, i) => readVar(`--series-${i + 1}`)),
    ink: readVar("--text-primary"),
    ink2: readVar("--text-secondary"),
    muted: readVar("--text-muted"),
    grid: readVar("--grid"),
    edge: readVar("--edge"),
    surface: readVar("--surface-1"),
    up: readVar("--red"),
    down: readVar("--green"),
    dark: window.matchMedia("(prefers-color-scheme: dark)").matches,
  };
}

/** Resolved palette; re-reads on OS theme change and when the up/down convention flips. */
export function usePalette(upColor: "red" | "green" = "red"): Palette {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setTick((t) => t + 1);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return useMemo(() => {
    const p = read();
    const red = readVar("--red");
    const green = readVar("--green");
    return { ...p, up: upColor === "red" ? red : green, down: upColor === "red" ? green : red };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, upColor]);
}

/**
 * Stable color slots: an entity keeps its slot while it stays selected, so removing one
 * series never repaints the survivors. New entities take the lowest free slot.
 */
export function useSlots(keys: string[], storageKey: string): Record<string, number> {
  const [slots, setSlots] = useState<Record<string, number>>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(storageKey) ?? "{}");
    } catch {
      return {};
    }
  });
  const sig = keys.join("\u0001");
  const next = useMemo(() => {
    const out: Record<string, number> = {};
    for (const k of keys) if (slots[k] !== undefined) out[k] = slots[k];
    const used = new Set(Object.values(out));
    for (const k of keys) {
      if (out[k] !== undefined) continue;
      let s = 0;
      while (used.has(s) && s < N_SLOTS) s++;
      out[k] = s < N_SLOTS ? s : keys.indexOf(k) % N_SLOTS;
      used.add(out[k]);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, slots]);
  useEffect(() => {
    if (JSON.stringify(next) !== JSON.stringify(slots)) {
      setSlots(next);
      try {
        sessionStorage.setItem(storageKey, JSON.stringify(next));
      } catch {
        /* ignore */
      }
    }
  }, [next, slots, storageKey]);
  return next;
}

export const slotVar = (i: number) => `var(--series-${(i % N_SLOTS) + 1})`;
