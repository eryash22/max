import { describe, expect, it } from "vitest";
import { contrastRatio } from "./color";
import { CONTRAST_PAIRS, THEME_PRESETS } from "./theming";
import type { AdvancedThemeTokens } from "../types";

describe("THEME_PRESETS", () => {
  const referenceKeys = Object.keys(THEME_PRESETS["Clinical Blue"].advanced_tokens).sort();

  for (const [name, preset] of Object.entries(THEME_PRESETS)) {
    it(`${name} has the same complete advanced_tokens set as every other preset`, () => {
      expect(Object.keys(preset.advanced_tokens).sort()).toEqual(referenceKeys);
      for (const value of Object.values(preset.advanced_tokens)) {
        expect(value).toMatch(/^#[0-9A-Fa-f]{6}$/);
      }
    });

    it(`${name} passes every WCAG contrast pair`, () => {
      const resolve = (field: string): string =>
        field in preset.advanced_tokens
          ? preset.advanced_tokens[field as keyof AdvancedThemeTokens]
          : (preset as unknown as Record<string, string>)[field];

      const failures: string[] = [];
      for (const [label, fgField, bgField, minRatio] of CONTRAST_PAIRS) {
        const ratio = contrastRatio(resolve(fgField), resolve(bgField));
        if (ratio < minRatio) failures.push(`${label}: ${ratio.toFixed(2)} < ${minRatio}`);
      }
      expect(failures).toEqual([]);
    });
  }
});
