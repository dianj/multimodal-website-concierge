"""Color validation and the contrast guardrails (WCAG ratio of at least 4.5:1)."""
import re

HEX_COLOR_RE = r"#[0-9A-Fa-f]{6}"

def color_contrast_ratio(color_a: str, color_b: str) -> float:
    def luminance(hex_color: str) -> float:
        if not re.fullmatch(HEX_COLOR_RE, hex_color):
            return 0.0
        rgb = [int(hex_color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in rgb]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    lighter = max(luminance(color_a), luminance(color_b))
    darker = min(luminance(color_a), luminance(color_b))
    return (lighter + 0.05) / (darker + 0.05)

PALETTE_COLORS = ("primary_color", "background_color", "text_color")

def normalize_palette_changes(changes: dict):
    """Validate color/radius formats. Returns (normalized_changes, error_message)."""
    normalized = {}
    for key, value in changes.items():
        if value is None:
            continue
        if not isinstance(value, str):
            return None, f"'{key}' must be a string."
        value = value.strip().lower()
        if key in PALETTE_COLORS and not re.fullmatch(HEX_COLOR_RE, value):
            return None, f"'{key}' must use 6-digit hex format, for example #14532d."
        if key == "border_radius" and not re.fullmatch(r"\d{1,3}px", value):
            return None, "border_radius must use px format, for example '8px'."
        normalized[key] = value
    return normalized, None

WHITE = "#ffffff"
MIN_CONTRAST = 4.5


def contrast_pairs(state: dict) -> list[tuple[str, str, str, str]]:
    """Every color pair the site renders as text on a background: (pair id, label, color, color).
    The CTA button and the slider arrows draw white on the primary color (and on their hover background); service
    cards with a background draw the mode's text color on it."""
    theme, dark = state["theme"], state["theme"]["dark_mode"]
    modes = {"light": theme, "dark": dark}
    pairs = []
    for mode, palette in modes.items():
        label = "Light theme" if mode == "light" else "Dark mode palette"
        pairs.append((f"{mode}.text", f"{label}: background/text", palette["background_color"], palette["text_color"]))
        pairs.append((f"{mode}.button", f"{label}: white button text on primary_color", palette["primary_color"],
                      WHITE))
    for mode, palette in modes.items():
        background = theme["card_style"][mode]["background_color"]
        if background:
            pairs.append((f"card.{mode}", f"{mode} mode card background/text", background, palette["text_color"]))
    for element in ("cta_button", "slider_arrows"):
        background = theme["hover_effects"][element]["background_color"]
        if background:
            pairs.append((f"hover.{element}", f"{element} hover background/white text", background, WHITE))
    return pairs


def audit_contrast(state: dict) -> list[tuple[str, str]]:
    """(pair id, problem) for every rendered pair below 4.5:1, on the whole site (review T21.A1: checking only the
    changed piece let a theme text color make an earlier card background unreadable)."""
    problems = []
    for pair, label, color_a, color_b in contrast_pairs(state):
        ratio = color_contrast_ratio(color_a, color_b)
        if ratio < MIN_CONTRAST:
            hint = " Choose a darker shade." if WHITE in (color_a, color_b) else ""
            problems.append((pair, f"{label} contrast would be {ratio:.2f}:1, it must be at least 4.5:1.{hint}"))
    return problems


def contrast_refusal(current: dict, candidate: dict) -> str | None:
    """The first pair the change would make unreadable, or None. A pair that already failed in the stored version
    (saved before this check existed) does not block other changes, so the site never gets stuck."""
    already = {pair for pair, _ in audit_contrast(current)}
    for pair, problem in audit_contrast(candidate):
        if pair not in already:
            return problem
    return None
