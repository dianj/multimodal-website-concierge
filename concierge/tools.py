"""Site-editing tools the agent can call. Each validates its input, applies guardrails and saves a new snapshot.

Every tool returns a JSON string: status success, error, or blocked_by_guardrail with a reason.
"""
import copy
import json
import re

from .db import STATUS_ACTIVE, STATUS_UNDONE, connect, get_latest_state, save_new_state
from .guardrails import HEX_COLOR_RE, contrast_refusal, normalize_palette_changes
from .site_state import (
    DARK_DEFAULT_MODES,
    HOVER_ELEMENTS,
    HOVER_SHADOWS,
    MAX_TESTIMONIALS,
    MULTILINE_FIELDS,
    SWITCHER_POSITIONS,
    normalize_text,
)


def error(message):
    """A tool result the model can act on: what was wrong with the call."""
    return json.dumps({"status": "error", "message": message})


def blocked(reason):
    """A tool result for a change that a guardrail refused."""
    return json.dumps({"status": "blocked_by_guardrail", "reason": reason})


def get_current_site_state():
    ver_id, state = get_latest_state()
    return json.dumps({"version": ver_id, "state": state})

def update_text_content(section_id: str, field: str, new_value: str):
    ver_id, state = get_latest_state()
    if section_id not in state["sections"]:
        return error(f"Unknown section_id '{section_id}'. Available: {list(state['sections'].keys())}")
    if field not in state["sections"][section_id]:
        available = list(state["sections"][section_id].keys())
        return error(f"Field '{field}' not found in '{section_id}'. Available: {available}")
    if not isinstance(state["sections"][section_id][field], str):
        return error(f"Field '{section_id}.{field}' is not a text field. Use the dedicated tool: "
                     "update_services_list for services.items, "
                     "update_testimonials for testimonials.items/autoplay_seconds.")
    if section_id == "hero" and field == "image_url":
        return error("Use update_hero_image to change the hero image.")
    if not isinstance(new_value, str) or len(new_value.strip()) > 2000:
        return error("Text values must be strings of at most 2000 characters.")
    if section_id == "contact" and field in ("email", "phone") and not new_value.strip():
        return blocked(f"Mandatory contact field '{field}' cannot be removed.")

    state["sections"][section_id][field] = normalize_text(new_value, (section_id, field) in MULTILINE_FIELDS)
    new_ver = save_new_state(state, f"Updated {section_id}.{field}")
    return json.dumps({"status": "success", "new_version": new_ver, "updated": f"{section_id}.{field}"})

def update_services_list(items_json: str):
    try:
        items = json.loads(items_json)
        if not isinstance(items, list):
            return error("items_json must be a JSON array of objects with 'name' and 'desc'.")
        if len(items) > 12:
            return error("A maximum of 12 services is allowed.")
        if any(
            not isinstance(item, dict)
            or set(item.keys()) != {"name", "desc"}
            or not isinstance(item["name"], str)
            or not isinstance(item["desc"], str)
            or not item["name"].strip()
            or not item["desc"].strip()
            for item in items
        ):
            return error("Each service must contain only non-empty string 'name' and 'desc' fields.")
        items = [
            {"name": normalize_text(item["name"], False)[:120], "desc": normalize_text(item["desc"], True)[:1000]}
            for item in items
        ]
        ver_id, state = get_latest_state()
        state["sections"]["services"]["items"] = items
        new_ver = save_new_state(state, f"Updated services list ({len(items)} items)")
        return json.dumps({"status": "success", "new_version": new_ver})
    except Exception as e:
        return error(str(e))

def update_theme_style(primary_color: str = None, background_color: str = None, text_color: str = None,
                       border_radius: str = None):
    changes, problem = normalize_palette_changes({
        "primary_color": primary_color, "background_color": background_color,
        "text_color": text_color, "border_radius": border_radius
    })
    if problem:
        return error(problem)
    if not changes:
        return error("Provide at least one property to change.")
    ver_id, current = get_latest_state()
    state = copy.deepcopy(current)
    state["theme"].update(changes)
    if refusal := contrast_refusal(current, state):
        return blocked(refusal)
    summary = ", ".join(f"{k}={v}" for k, v in changes.items())
    new_ver = save_new_state(state, f"Changed theme: {summary}")
    return json.dumps({"status": "success", "new_version": new_ver, "changed": changes})

def configure_dark_mode(enabled: bool, background_color: str = None, text_color: str = None, primary_color: str = None,
                        switcher_position: str = None, default_mode: str = None):
    if not isinstance(enabled, bool):
        return error("'enabled' must be true or false.")
    if switcher_position is not None and switcher_position not in SWITCHER_POSITIONS:
        return error(f"switcher_position must be one of {list(SWITCHER_POSITIONS)}.")
    if default_mode is not None and default_mode not in DARK_DEFAULT_MODES:
        return error(f"default_mode must be one of {list(DARK_DEFAULT_MODES)}.")
    changes, problem = normalize_palette_changes({
        "background_color": background_color, "text_color": text_color, "primary_color": primary_color
    })
    if problem:
        return error(problem)
    ver_id, current = get_latest_state()
    state = copy.deepcopy(current)
    dark = {**state["theme"]["dark_mode"], **changes, "enabled": enabled}
    if switcher_position is not None:
        dark["switcher_position"] = switcher_position
    if default_mode is not None:
        dark["default_mode"] = default_mode
    state["theme"]["dark_mode"] = dark
    if refusal := contrast_refusal(current, state):
        return blocked(refusal)
    new_ver = save_new_state(state, f"Dark mode {'enabled' if enabled else 'disabled'}")
    result = {"status": "success", "new_version": new_ver, "dark_mode": dark}
    if enabled:
        result["visitor_experience"] = (
            "Visitors see a theme switcher with three options: Light, Dark and Auto "
            "(Auto follows the visitor's OS setting live). "
            f"Switcher position: {dark['switcher_position']}. Mode before the visitor chooses: {dark['default_mode']}. "
            f"The visitor's choice is remembered in their browser and can be changed back to Auto at any time."
        )
    return json.dumps(result)

def update_card_style(mode: str, border_color: str = None, border_opacity: float = None,
                      border_width: str = None, background_color: str = None):
    if mode not in ("light", "dark", "both"):
        return error("mode must be 'light', 'dark' or 'both'.")
    changes = {}
    if border_color is not None:
        if not isinstance(border_color, str) or not re.fullmatch(HEX_COLOR_RE, border_color.strip()):
            return error("border_color must use 6-digit hex format, e.g. #ffffff.")
        changes["border_color"] = border_color.strip().lower()
    if border_opacity is not None:
        if not isinstance(border_opacity, (int, float)) or not (0.05 <= float(border_opacity) <= 1):
            return error("border_opacity must be a number between 0.05 and 1.")
        changes["border_opacity"] = round(float(border_opacity), 2)
    if border_width is not None:
        if not isinstance(border_width, str) or not re.fullmatch(r"[0-6]px", border_width.strip()):
            return error("border_width must be between '0px' and '6px'.")
        changes["border_width"] = border_width.strip()
    if background_color is not None:
        value = background_color.strip().lower() if isinstance(background_color, str) else ""
        if value in ("none", "transparent", ""):
            changes["background_color"] = None
        elif re.fullmatch(HEX_COLOR_RE, value):
            changes["background_color"] = value
        else:
            return error("background_color must be a 6-digit hex color or 'none'.")
    if not changes:
        return error("Provide at least one style property to change.")

    ver_id, current = get_latest_state()
    state = copy.deepcopy(current)
    theme = state["theme"]
    modes = ["light", "dark"] if mode == "both" else [mode]
    for m in modes:
        theme["card_style"][m] = {**theme["card_style"][m], **changes}
    # Card text uses the mode's text color, so a custom card background must keep it readable.
    if refusal := contrast_refusal(current, state):
        return blocked(refusal)
    note = None
    if "dark" in modes and not theme["dark_mode"]["enabled"]:
        note = ("Dark mode is currently disabled, so the dark card style will only be visible after "
                "configure_dark_mode(enabled=true).")
    new_ver = save_new_state(state, f"Updated service card style ({mode} mode)")
    result = {"status": "success", "new_version": new_ver, "card_style": {m: theme["card_style"][m] for m in modes}}
    if note:
        result["note"] = note
    return json.dumps(result)

def update_hover_effect(element: str, enabled: bool = True, lift_px: int = None, scale: float = None,
                        shadow: str = None, background_color: str = None, border_color: str = None,
                        border_opacity: float = None, tint_opacity: float = None, duration_ms: int = None):
    if element not in HOVER_ELEMENTS:
        return error(f"element must be one of {list(HOVER_ELEMENTS)}.")
    if not isinstance(enabled, bool):
        return error("'enabled' must be true or false.")
    given = {
        "lift_px": lift_px, "scale": scale, "shadow": shadow, "background_color": background_color,
        "border_color": border_color, "border_opacity": border_opacity, "tint_opacity": tint_opacity,
        "duration_ms": duration_ms,
    }
    given = {k: v for k, v in given.items() if v is not None}
    unsupported = [k for k in given if k not in HOVER_ELEMENTS[element]]
    if unsupported:
        return error(f"{unsupported} not supported for '{element}'. Supported: {list(HOVER_ELEMENTS[element])}.")

    def number_in(key, lo, hi, cast=float):
        value = given[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (lo <= value <= hi):
            raise ValueError(f"{key} must be a number between {lo} and {hi}.")
        return cast(value)

    changes = {}
    try:
        if "lift_px" in given:
            changes["lift_px"] = number_in("lift_px", 0, 12, int)
        if "scale" in given:
            changes["scale"] = round(number_in("scale", 1.0, 1.1), 3)
        if "border_opacity" in given:
            changes["border_opacity"] = round(number_in("border_opacity", 0.05, 1), 2)
        if "tint_opacity" in given:
            changes["tint_opacity"] = round(number_in("tint_opacity", 0, 0.2), 2)
        if "duration_ms" in given:
            changes["duration_ms"] = number_in("duration_ms", 100, 600, int)
    except ValueError as e:
        return error(str(e))
    if "shadow" in given:
        if given["shadow"] not in HOVER_SHADOWS:
            return error(f"shadow must be one of {list(HOVER_SHADOWS)}.")
        changes["shadow"] = given["shadow"]
    for key in ("background_color", "border_color"):
        if key in given:
            value = given[key].strip().lower() if isinstance(given[key], str) else ""
            if value in ("none", ""):
                changes[key] = None
            elif re.fullmatch(HEX_COLOR_RE, value):
                changes[key] = value
            else:
                return error(f"{key} must be a 6-digit hex color or 'none'.")
    ver_id, current = get_latest_state()
    state = copy.deepcopy(current)
    effect = {**state["theme"]["hover_effects"][element], **changes, "enabled": enabled}
    state["theme"]["hover_effects"][element] = effect
    # Buttons and arrows keep white text/icons on hover, so a hover background must stay readable.
    if refusal := contrast_refusal(current, state):
        return blocked(refusal)
    new_ver = save_new_state(state, f"{'Updated' if enabled else 'Disabled'} hover effect: {element}")
    return json.dumps({"status": "success", "new_version": new_ver, "element": element, "hover_effect": effect})

def update_testimonials(items_json: str, autoplay_seconds: int = None):
    try:
        items = json.loads(items_json) if isinstance(items_json, str) else items_json
    except json.JSONDecodeError as e:
        return error(f"items_json is not valid JSON: {e}")
    if not isinstance(items, list) or not items:
        return error("items_json must be a non-empty JSON array of objects with 'quote' and 'author'.")
    if len(items) > MAX_TESTIMONIALS:
        return error(f"A maximum of {MAX_TESTIMONIALS} testimonials is allowed.")
    if any(
        not isinstance(item, dict)
        or set(item.keys()) != {"quote", "author"}
        or not all(isinstance(item[k], str) and item[k].strip() for k in ("quote", "author"))
        for item in items
    ):
        return error("Each testimonial must contain only non-empty string 'quote' and 'author' fields.")
    if autoplay_seconds is not None and (not isinstance(autoplay_seconds, int) or not (0 <= autoplay_seconds <= 30)):
        return error("autoplay_seconds must be an integer between 0 (off) and 30.")
    ver_id, state = get_latest_state()
    section = state["sections"]["testimonials"]
    section["items"] = [
        {"quote": normalize_text(item["quote"], True)[:1000], "author": normalize_text(item["author"], False)[:150]}
        for item in items
    ]
    if autoplay_seconds is not None:
        section["autoplay_seconds"] = autoplay_seconds
    layout = "slider" if len(items) > 1 else "single testimonial"
    new_ver = save_new_state(state, f"Updated testimonials ({len(items)} items, {layout})")
    return json.dumps({"status": "success", "new_version": new_ver, "count": len(items), "layout": layout})

def update_hero_image(image_url: str):
    if not isinstance(image_url, str) or not re.fullmatch(r"https?://\S+", image_url.strip()):
        return error("Hero image must be a valid http or https URL.")
    ver_id, state = get_latest_state()
    state["sections"]["hero"]["image_url"] = image_url.strip()
    new_ver = save_new_state(state, "Updated hero banner image")
    return json.dumps({"status": "success", "new_version": new_ver})

def reorder_sections(new_order: list):
    ver_id, state = get_latest_state()
    valid_sections = set(state["sections"].keys())
    if not isinstance(new_order, list) or len(new_order) != len(valid_sections) or set(new_order) != valid_sections:
        return error(f"Must include exact sections: {list(valid_sections)}")
    state["section_order"] = new_order
    new_ver = save_new_state(state, f"Reordered sections to {new_order}")
    return json.dumps({"status": "success", "new_version": new_ver})

def rollback_version():
    conn = connect()
    rows = conn.execute(
        "SELECT version_id FROM site_versions WHERE status = ? ORDER BY version_id DESC LIMIT 2",
        (STATUS_ACTIVE,)
    ).fetchall()
    if len(rows) < 2:
        conn.close()
        return error("Already at initial version; nothing to undo.")
    latest_id = rows[0][0]
    # Non-destructive undo: mark the version as undone so it can be redone.
    conn.execute("UPDATE site_versions SET status = ? WHERE version_id = ?", (STATUS_UNDONE, latest_id))
    conn.commit()
    conn.close()
    ver_id, _ = get_latest_state()
    return json.dumps({"status": "success", "reverted_to_version": ver_id})

def redo_version():
    conn = connect()
    row = conn.execute(
        "SELECT version_id FROM site_versions WHERE status = ? ORDER BY version_id ASC LIMIT 1",
        (STATUS_UNDONE,)
    ).fetchone()
    if not row:
        conn.close()
        return error("Nothing to redo.")
    conn.execute("UPDATE site_versions SET status = ? WHERE version_id = ?", (STATUS_ACTIVE, row[0]))
    conn.commit()
    conn.close()
    return json.dumps({"status": "success", "restored_version": row[0]})
