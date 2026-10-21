import { describe, expect, it } from "vitest";
import { contrastRatio } from "./color";

// Reference ratios per the WCAG spec / common contrast checkers.
describe("contrastRatio", () => {
  it("returns 21:1 for pure black on pure white", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
  });

  it("returns 1:1 for identical colors", () => {
    expect(contrastRatio("#336699", "#336699")).toBeCloseTo(1, 5);
  });

  it("is symmetric regardless of argument order", () => {
    expect(contrastRatio("#123456", "#abcdef")).toBeCloseTo(contrastRatio("#abcdef", "#123456"), 10);
  });

  it("matches a known reference ratio (white vs #767676 ~= 4.5:1)", () => {
    expect(contrastRatio("#ffffff", "#767676")).toBeCloseTo(4.5, 1);
  });
});
