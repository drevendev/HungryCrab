import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { clamp, mean } from "./math";

describe("clamp", () => {
  it("keeps values inside the range", () => {
    fc.assert(
      fc.property(fc.integer(), fc.integer(), fc.integer(), (value, a, b) => {
        const [min, max] = a <= b ? [a, b] : [b, a];
        const result = clamp(value, min, max);
        expect(result).toBeGreaterThanOrEqual(min);
        expect(result).toBeLessThanOrEqual(max);
      }),
    );
  });
});

describe("mean", () => {
  it("returns 0 for an empty list", () => {
    expect(mean([])).toBe(0);
  });
});
