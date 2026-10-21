// Step 10G — WCAG contrast-ratio math, mirrored from backend/app/utils/color.py so
// the Theme Settings page can warn about unreadable combinations live, using exactly
// the same thresholds the backend will enforce on save.

export function hexToRgb(value: string): [number, number, number] {
  const clean = value.replace("#", "");
  return [parseInt(clean.slice(0, 2), 16), parseInt(clean.slice(2, 4), 16), parseInt(clean.slice(4, 6), 16)];
}

function channelLuminance(c: number): number {
  const srgb = c / 255;
  return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(value: string): number {
  const [r, g, b] = hexToRgb(value);
  return 0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
}

export function contrastRatio(hexA: string, hexB: string): number {
  const l1 = relativeLuminance(hexA);
  const l2 = relativeLuminance(hexB);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

export function blend(hexA: string, hexB: string, amount: number): string {
  const [ra, ga, ba] = hexToRgb(hexA);
  const [rb, gb, bb] = hexToRgb(hexB);
  const ch = (a: number, b: number) => Math.round(a + (b - a) * amount);
  const toHex = (c: number) => c.toString(16).padStart(2, "0");
  return `#${toHex(ch(ra, rb))}${toHex(ch(ga, gb))}${toHex(ch(ba, bb))}`;
}

export function mix(hexValue: string, target: "white" | "black", amount: number): string {
  return blend(hexValue, target === "white" ? "#ffffff" : "#000000", amount);
}

export function darken(hexValue: string, amount: number): string {
  return mix(hexValue, "black", amount);
}

export function pickContrastingText(bg: string, light = "#ffffff", dark = "#0b1220"): string {
  return contrastRatio(light, bg) >= contrastRatio(dark, bg) ? light : dark;
}

export function ensureMinContrast(fg: string, bg: string, minRatio: number, fallback: string, step = 0.06): string {
  if (contrastRatio(fg, bg) >= minRatio) return fg;
  let amount = 0;
  let candidate = fg;
  while (contrastRatio(candidate, bg) < minRatio && amount < 1) {
    amount = Math.min(1, amount + step);
    candidate = blend(fg, fallback, amount);
  }
  return candidate;
}

function bgForMinContrastToward(text: string, target: "white" | "black", minRatio: number, start: number, step: number): string {
  let amount = start;
  let bg = mix(text, target, amount);
  while (contrastRatio(text, bg) < minRatio && amount < 0.97) {
    amount = Math.min(0.97, amount + step);
    bg = mix(text, target, amount);
  }
  return bg;
}

export function bgForMinContrast(text: string, dark: boolean, minRatio: number, start: number, step = 0.02): string {
  const preferred: "white" | "black" = dark ? "black" : "white";
  const bg = bgForMinContrastToward(text, preferred, minRatio, start, step);
  if (contrastRatio(text, bg) >= minRatio) return bg;
  const other: "white" | "black" = preferred === "black" ? "white" : "black";
  return bgForMinContrastToward(text, other, minRatio, start, step);
}
