import { describe, expect, it } from "vitest";
import { unionAlign } from "../components/NavChart";
import { drawdownFrom, navFrom, rebase } from "./chart";
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
