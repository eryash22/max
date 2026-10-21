"use client";

import type { ReactNode } from "react";
import { useTheme } from "../hooks/useTheme";

/** Applies the centrally-stored theme (Part 11-12) as soon as it loads. Renders
 * children immediately regardless of load state — the default `:root` colors in
 * globals.css are the fallback until GET /api/v1/theme resolves, so there is nothing
 * to block on. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  useTheme();
  return <>{children}</>;
}
