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

def palette_guardrail(palette: dict, label: str):
    """Contrast rules for a complete palette (checked on the final result, not per property)."""
    ratio = color_contrast_ratio(palette["background_color"], palette["text_color"])
    if ratio < 4.5:
        return f"{label}: background/text contrast is {ratio:.2f}:1, it must be at least 4.5:1."
    # The CTA button renders white text on primary_color, so that pair must stay readable too.
    ratio = color_contrast_ratio(palette["primary_color"], "#ffffff")
    if ratio < 4.5:
        return (f"{label}: primary_color has {ratio:.2f}:1 contrast with the white button text, "
                "it must be at least 4.5:1. Choose a darker shade.")
    return None
