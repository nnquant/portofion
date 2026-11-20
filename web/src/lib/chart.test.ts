import { describe, expect, it } from "vitest";
import { unionAlign } from "../components/NavChart";
import { drawdownFrom, extentOf, ffillInside, linAxisFit, logAxisFit, navFmtFor, navFrom, niceStep, rebase } from "./chart";
import { compact, pct, ratio, sign } from "./format";

describe("nav helpers", () => {
  it("compounds returns and keeps nulls before the start", () => {
    const nav = navFrom([null, 0.1, -0.1, null, 0.05]);
    expect(nav[0]).toBeNull();
    expect(nav[1]).toBeCloseTo(1.1);
    expect(nav[2]).toBeCloseTo(0.99);
    expect(nav[3]).toBeCloseTo(0.99); // carried inside the life
    expect(nav[4]).toBeCloseTo(1.0395);
  });

  it("rebases from an index", () => {
    const r = rebase([1, 2, 4], 1);
    expect(r).toEqual([null, 1, 2]);
  });

  it("drawdown from peak", () => {
    const dd = drawdownFrom([1.1, 0.99, 1.2, 0.9]);
    expect(dd[0]).toBe(0);
    expect(dd[1]).toBeCloseTo(0.99 / 1.1 - 1);
    expect(dd[2]).toBe(0);
    expect(dd[3]).toBeCloseTo(0.75 - 1);
  });

  it("aligns onto the union calendar with zero fill inside the life", () => {
    const { dates, cols } = unionAlign([
      { dates: ["2024-01-01", "2024-01-03"], values: [0.01, 0.02] },
      { dates: ["2024-01-02", "2024-01-04"], values: [0.03, 0.04] },
    ]);
    expect(dates).toEqual(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]);
    expect(cols[0]).toEqual([0.01, 0, 0.02, null]);
    expect(cols[1]).toEqual([null, 0.03, 0, 0.04]);
  });
});

describe("format", () => {
  it("formats numbers and nulls", () => {
    expect(pct(0.12345)).toBe("12.35%");
    expect(pct(null)).toBe("—");
    expect(ratio(1.234)).toBe("1.23");
    expect(compact(5e7)).toBe("50.0M");
    expect(sign(-1)).toBe("down");
    expect(sign(0.1)).toBe("up");
    expect(sign(null)).toBe("");
  });
});

describe("log axis fit", () => {
  const inside = (f: ReturnType<typeof logAxisFit>) => f.ticks.every((t) => t >= f.min && t <= f.max);

  it("hugs the data instead of widening to powers of ten", () => {
    const f = logAxisFit(0.6, 2.05);
    expect(f.min).toBeGreaterThan(0.55);
    expect(f.max).toBeLessThan(2.3);
    expect(f.ticks).toEqual([0.7, 1, 1.5, 2]);
    expect(inside(f)).toBe(true);
  });

  it("uses 1-2-5 marks across decades", () => {
    const f = logAxisFit(0.5, 60);
    expect(f.ticks).toEqual([0.5, 1, 2, 5, 10, 20, 50]);
  });

  it("falls back to even steps on a narrow range", () => {
    const f = logAxisFit(0.95, 1.12);
    expect(f.ticks).toEqual([0.95, 1, 1.05, 1.1]);
    expect(inside(f)).toBe(true);
  });

  it("survives empty or flat input", () => {
    expect(logAxisFit(Infinity, -Infinity).ticks.length).toBeGreaterThan(0);
    const flat = logAxisFit(1, 1);
    expect(flat.min).toBeLessThan(1);
    expect(flat.max).toBeGreaterThan(1);
    expect(flat.ticks).toContain(1);
  });

  it("nice steps and extents", () => {
    expect([0.034, 0.07, 0.24, 3.1].map(niceStep)).toEqual([0.05, 0.1, 0.5, 5]);
    expect(extentOf([[null, 3, 1], [5, null, 2]], 1)).toEqual([1, 3]);
  });
});

describe("linear axis fit", () => {
  it("uses 1-2-5 steps, never 3", () => {
    expect(linAxisFit(0.93, 1.12)).toEqual({ min: 0.9, max: 1.15, interval: 0.05 });
    const f = linAxisFit(0.6, 2.05);
    expect(f.interval).toBe(0.5);
    expect([f.min, f.max]).toEqual([0.5, 2.5]);
  });

  it("widens a flat or empty range", () => {
    const flat = linAxisFit(1, 1);
    expect(flat.min).toBeLessThan(1);
    expect(flat.max).toBeGreaterThan(1);
    expect(linAxisFit(Infinity, -Infinity).max).toBeGreaterThan(0);
  });

  it("labels carry enough decimals for the step", () => {
    expect(navFmtFor(0.05)(0.95)).toBe("0.95");
    expect(navFmtFor(0.005)(0.995)).toBe("0.995");
    expect(navFmtFor(10)(120)).toBe("120");
  });

  it("carries values over gaps inside the life only", () => {
    expect(ffillInside([null, 0.1, null, null, 0.3, null])).toEqual([null, 0.1, 0.1, 0.1, 0.3, null]);
  });
});
