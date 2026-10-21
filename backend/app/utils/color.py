"""WCAG contrast-ratio math, used to block/flag unreadable theme token pairs
(Step 10G advanced theme customization). Pure functions, no dependencies."""


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    clean = value.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _channel_luminance(c: int) -> float:
    srgb = c / 255
    return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4


def relative_luminance(value: str) -> float:
    r, g, b = hex_to_rgb(value)
    return 0.2126 * _channel_luminance(r) + 0.7152 * _channel_luminance(g) + 0.0722 * _channel_luminance(b)


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG relative-luminance contrast ratio between two `#RRGGBB` colors, in [1, 21]."""
    l1 = relative_luminance(hex_a)
    l2 = relative_luminance(hex_b)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def blend(hex_a: str, hex_b: str, amount: float) -> str:
    """Blends `hex_a` toward `hex_b` by `amount` (0-1)."""
    ra, ga, ba = hex_to_rgb(hex_a)
    rb, gb, bb = hex_to_rgb(hex_b)
    ch = lambda a, b: round(a + (b - a) * amount)
    return f"#{ch(ra, rb):02x}{ch(ga, gb):02x}{ch(ba, bb):02x}"


def mix(hex_value: str, target: str, amount: float) -> str:
    """Mixes `hex_value` toward `target` ('white' or 'black') by `amount` (0-1).
    Python port of frontend/lib/theming.ts's `mix()` so default token derivation
    matches on both sides."""
    return blend(hex_value, "#ffffff" if target == "white" else "#000000", amount)


def darken(hex_value: str, amount: float) -> str:
    return mix(hex_value, "black", amount)


def ensure_min_contrast(fg: str, bg: str, min_ratio: float, fallback: str, step: float = 0.06) -> str:
    """Blends `fg` toward `fallback` (which is assumed to already contrast well
    against `bg`, e.g. from `pick_contrasting_text(bg)`) until it clears `min_ratio`
    against `bg`, or gives up at `fallback` itself. Used to keep brand-colored
    elements (links, etc.) from going unreadable when an admin picks a color that
    happens to be too close to the page background."""
    if contrast_ratio(fg, bg) >= min_ratio:
        return fg
    amount = 0.0
    candidate = fg
    while contrast_ratio(candidate, bg) < min_ratio and amount < 1.0:
        amount = min(1.0, amount + step)
        candidate = blend(fg, fallback, amount)
    return candidate


def _bg_for_min_contrast_toward(text: str, target: str, min_ratio: float, start: float, step: float) -> str:
    amount = start
    bg = mix(text, target, amount)
    while contrast_ratio(text, bg) < min_ratio and amount < 0.97:
        amount = min(0.97, amount + step)
        bg = mix(text, target, amount)
    return bg


def bg_for_min_contrast(text: str, dark: bool, min_ratio: float, start: float, step: float = 0.02) -> str:
    """Mixes `text` toward black (dark mode) or white (light mode) by an increasing
    amount, starting at `start`, until the resulting background clears `min_ratio`
    against `text` — used to derive status backgrounds (success/warning/danger/info)
    that stay readable regardless of exactly how saturated the admin's chosen status
    color is. Falls back to mixing the other direction if the mode-preferred
    direction can never reach the threshold (e.g. a text color already light enough
    that a lighter background can't gain any contrast)."""
    preferred = "black" if dark else "white"
    bg = _bg_for_min_contrast_toward(text, preferred, min_ratio, start, step)
    if contrast_ratio(text, bg) >= min_ratio:
        return bg
    other = "white" if preferred == "black" else "black"
    return _bg_for_min_contrast_toward(text, other, min_ratio, start, step)


def pick_contrasting_text(bg: str, light: str = "#ffffff", dark: str = "#0b1220") -> str:
    """Auto-picks whichever of `light`/`dark` has higher contrast against `bg` — used to
    derive default text-on-fill tokens (e.g. button text) so they never go stale when
    an admin changes the underlying fill color, instead of a hard-coded '#fff'."""
    return light if contrast_ratio(light, bg) >= contrast_ratio(dark, bg) else dark
