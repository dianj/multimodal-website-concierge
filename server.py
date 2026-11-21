import os
import json
import asyncio
import base64
import sqlite3
import re
import uuid
import tempfile
import threading
import urllib.error
from contextlib import asynccontextmanager
import urllib.request
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.concurrency import run_in_threadpool
from openai import OpenAI, APIConnectionError

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the default model in the background so the web UI is available while LM Studio loads it.
    threading.Thread(target=ensure_default_model_on_startup, daemon=True).start()
    yield

app = FastAPI(title="Multimodal AI Website Maintenance Concierge", lifespan=lifespan)

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1235/v1")
LMSTUDIO_ROOT = LLM_BASE_URL.rstrip("/").removesuffix("/v1")
# Models offered in the UI as (LM Studio model key, display label). The first one is the default.
ALLOWED_MODELS = [
    ("gemma-4-26b-a4b-it", "Gemma 4 26B"),
    ("qwen3.8-27b", "Qwen 3.8 27B"),
]
MODEL_NAME = os.environ.get("LLM_MODEL", ALLOWED_MODELS[0][0])
LOAD_CONTEXT_LENGTH = int(os.environ.get("LLM_CONTEXT_LENGTH", "16384"))
MODEL_LOAD_TIMEOUT = 600

# --- VOICE ---
# Speech-to-text runs locally with faster-whisper on the CPU (the GPU is reserved for the LLM).
# Text-to-speech uses edge-tts (Microsoft Edge online neural voices), same stack as the other voice projects.
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
# Voice per gender and language: [primary, fallback]. The fallback keeps the same gender
# (Bulgarian has only one voice per gender, so it is simply retried).
TTS_VOICES = {
    "male": {"en": ["en-US-AndrewNeural", "en-US-BrianNeural"], "bg": ["bg-BG-BorislavNeural", "bg-BG-BorislavNeural"]},
    "female": {"en": ["en-US-AvaNeural", "en-US-EmmaNeural"], "bg": ["bg-BG-KalinaNeural", "bg-BG-KalinaNeural"]},
}
DEFAULT_VOICE_GENDER = "male"
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_TTS_CHARS = 1500
TTS_ATTEMPT_TIMEOUT = 15
MAX_BRIEF_TTS_CHARS = 500
# English is the primary language; Bulgarian is fully supported.
PRIMARY_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "bg")
# Domain vocabulary hints for Whisper (per language): improve recognition of website-editing terms.
WHISPER_PROMPTS = {
    "en": (
        "Website editing commands: change the color, primary color, background, text, heading, subtitle, "
        "paragraph, section, services, testimonials, slider, dark mode, contact, working hours, button, image, reorder, undo, approve."
    ),
    "bg": (
        "Команди за редакция на уебсайт: смени цвета, основен цвят, фон, текст, заглавие, подзаглавие, "
        "параграф, секция, услуги, отзиви, слайдер, тъмен режим, контакти, работно време, бутон, снимка, подредба, отмени, одобри."
    ),
}
llm_client = OpenAI(base_url=LLM_BASE_URL, api_key="lm-studio")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# WEBSITE_DB_PATH lets tests and evals use a throwaway database.
DB_PATH = os.environ.get("WEBSITE_DB_PATH", os.path.join(BASE_DIR, "website_cms.db"))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
MAX_TOOL_ROUNDS = 8
HEX_COLOR_RE = r"#[0-9A-Fa-f]{6}"

# Version status values: 0 = active history, 1 = undone (available for redo), 2 = discarded redo branch
STATUS_ACTIVE, STATUS_UNDONE, STATUS_DISCARDED = 0, 1, 2

# Dark palette used when the site visitor is in dark mode (system preference or the toggle on the site).
DEFAULT_DARK_MODE = {
    "enabled": False,
    "background_color": "#0f172a",
    "text_color": "#e2e8f0",
    "primary_color": "#2563eb",
    # Where the visitor's Light / Dark / Auto switcher is shown on the site.
    "switcher_position": "corner",
    # Mode for visitors who have not chosen yet: "auto" follows the visitor's OS setting.
    "default_mode": "auto"
}
# Service card styling, per color mode. border_opacity lets the agent use soft outlines
# (e.g. white at 35% in dark mode). background_color None = transparent (inherits the page).
DEFAULT_CARD_STYLE = {
    "light": {"border_color": "#7f7f7f", "border_opacity": 0.25, "border_width": "1px", "background_color": None},
    "dark": {"border_color": "#7f7f7f", "border_opacity": 0.25, "border_width": "1px", "background_color": None},
}
# Hover effects per element. The agent picks values from a whitelist; the frontend builds the CSS.
HOVER_SHADOWS = ("none", "soft", "medium", "strong")
HOVER_ELEMENTS = {
    # element: properties the agent may set for it
    "cta_button": ("lift_px", "scale", "shadow", "background_color", "duration_ms"),
    "service_cards": ("lift_px", "scale", "shadow", "border_color", "border_opacity", "tint_opacity", "duration_ms"),
    "hero_image": ("lift_px", "scale", "shadow", "duration_ms"),
    "slider_arrows": ("scale", "shadow", "background_color", "duration_ms"),
}
DEFAULT_HOVER_EFFECT = {
    "enabled": False, "lift_px": 0, "scale": 1.0, "shadow": "none", "background_color": None,
    "border_color": None, "border_opacity": 1.0, "tint_opacity": 0.0, "duration_ms": 200,
}
SWITCHER_POSITIONS = ("corner", "top_bar", "contact")
DARK_DEFAULT_MODES = ("auto", "light", "dark")
MAX_TESTIMONIALS = 10

DEFAULT_SITE_STATE = {
    "theme": {
        "primary_color": "#2563eb",
        "background_color": "#ffffff",
        "text_color": "#1e293b",
        "border_radius": "8px",
        "dark_mode": dict(DEFAULT_DARK_MODE)
    },
    "section_order": ["hero", "services", "testimonials", "contact"],
    "sections": {
        "hero": {
            "title": "Modern Digital Solutions for Your Business",
            "subtitle": "We build fast, reliable, and scalable web applications tailored to your needs.",
            "cta_text": "Get Started",
            "image_url": "https://images.unsplash.com/photo-1498050108023-c5249f4df085?auto=format&fit=crop&w=1000&q=80"
        },
        "services": {
            "heading": "Our Services",
            "items": [
                {"name": "Web Development", "desc": "Custom websites and portals."},
                {"name": "Cloud Hosting", "desc": "99.9% uptime managed infrastructure."},
                {"name": "AI Integration", "desc": "Smart chatbots and workflow automation."}
            ]
        },
        "testimonials": {
            "heading": "What Clients Say",
            "items": [
                {"quote": "Working with this team transformed our online presence in just three weeks!", "author": "Elena Petrova, CEO at TechStart"}
            ],
            "autoplay_seconds": 6
        },
        "contact": {
            "heading": "Contact Us",
            "email": "support@novastudio.example",
            "phone": "+359 2 000 0000",
            "working_hours": "Mon - Fri: 09:00 - 18:00"
        }
    }
}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            change_summary TEXT,
            state_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS change_tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            version_id INTEGER,
            ticket_markdown TEXT
        )
    """)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(site_versions)")]
    if "status" not in columns:
        conn.execute("ALTER TABLE site_versions ADD COLUMN status INTEGER NOT NULL DEFAULT 0")
    count = conn.execute("SELECT COUNT(*) FROM site_versions").fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO site_versions (timestamp, change_summary, state_json) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), "Initial website baseline", json.dumps(DEFAULT_SITE_STATE))
        )
    conn.commit()
    conn.close()

def get_latest_state():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT version_id, state_json FROM site_versions WHERE status = ? ORDER BY version_id DESC LIMIT 1",
        (STATUS_ACTIVE,)
    ).fetchone()
    conn.close()
    return row[0], upgrade_state(json.loads(row[1]))

def upgrade_state(state: dict) -> dict:
    """Bring older stored versions up to the current schema (used for every read, incl. undo/redo)."""
    testimonials = state["sections"].get("testimonials", {})
    if "items" not in testimonials:
        quote, author = testimonials.pop("quote", ""), testimonials.pop("author", "")
        testimonials["items"] = [{"quote": quote, "author": author}] if quote else []
    testimonials.setdefault("autoplay_seconds", 6)
    dark = state["theme"].setdefault("dark_mode", {})
    for key, value in DEFAULT_DARK_MODE.items():
        dark.setdefault(key, value)
    card_style = state["theme"].setdefault("card_style", {})
    for mode, defaults in DEFAULT_CARD_STYLE.items():
        style = card_style.setdefault(mode, {})
        for key, value in defaults.items():
            style.setdefault(key, value)
    hover = state["theme"].setdefault("hover_effects", {})
    for element in HOVER_ELEMENTS:
        effect = hover.setdefault(element, {})
        for key, value in DEFAULT_HOVER_EFFECT.items():
            effect.setdefault(key, value)
    return state

def save_new_state(new_state: dict, summary: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # A new change after Undo invalidates the redo branch (kept in the DB for auditing).
    cur.execute("UPDATE site_versions SET status = ? WHERE status = ?", (STATUS_DISCARDED, STATUS_UNDONE))
    cur.execute(
        "INSERT INTO site_versions (timestamp, change_summary, state_json) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), summary, json.dumps(new_state))
    )
    ver_id = cur.lastrowid
    conn.commit()
    conn.close()
    return ver_id

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

# Fields rendered as paragraphs (blank line = new paragraph). All other text fields are single-line.
MULTILINE_FIELDS = {("hero", "subtitle"), ("contact", "working_hours")}

def normalize_text(value: str, multiline: bool) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not multiline:
        return re.sub(r"\s*\n\s*", " ", value)
    lines = [line.strip() for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))

# --- TOOLS FOR GEMMA 4 ---
def get_current_site_state():
    ver_id, state = get_latest_state()
    return json.dumps({"version": ver_id, "state": state})

def update_text_content(section_id: str, field: str, new_value: str):
    ver_id, state = get_latest_state()
    if section_id not in state["sections"]:
        return json.dumps({"status": "error", "message": f"Unknown section_id '{section_id}'. Available: {list(state['sections'].keys())}"})
    if field not in state["sections"][section_id]:
        return json.dumps({"status": "error", "message": f"Field '{field}' not found in '{section_id}'. Available: {list(state['sections'][section_id].keys())}"})
    if not isinstance(state["sections"][section_id][field], str):
        return json.dumps({"status": "error", "message": f"Field '{section_id}.{field}' is not a text field. Use the dedicated tool: update_services_list for services.items, update_testimonials for testimonials.items/autoplay_seconds."})
    if section_id == "hero" and field == "image_url":
        return json.dumps({"status": "error", "message": "Use update_hero_image to change the hero image."})
    if not isinstance(new_value, str) or len(new_value.strip()) > 2000:
        return json.dumps({"status": "error", "message": "Text values must be strings of at most 2000 characters."})
    if section_id == "contact" and field in ("email", "phone") and not new_value.strip():
        return json.dumps({"status": "blocked_by_guardrail", "reason": f"Mandatory contact field '{field}' cannot be removed."})
    
    state["sections"][section_id][field] = normalize_text(new_value, (section_id, field) in MULTILINE_FIELDS)
    new_ver = save_new_state(state, f"Updated {section_id}.{field}")
    return json.dumps({"status": "success", "new_version": new_ver, "updated": f"{section_id}.{field}"})

def update_services_list(items_json: str):
    try:
        items = json.loads(items_json)
        if not isinstance(items, list):
            return json.dumps({"status": "error", "message": "items_json must be a JSON array of objects with 'name' and 'desc'."})
        if len(items) > 12:
            return json.dumps({"status": "error", "message": "A maximum of 12 services is allowed."})
        if any(
            not isinstance(item, dict)
            or set(item.keys()) != {"name", "desc"}
            or not isinstance(item["name"], str)
            or not isinstance(item["desc"], str)
            or not item["name"].strip()
            or not item["desc"].strip()
            for item in items
        ):
            return json.dumps({"status": "error", "message": "Each service must contain only non-empty string 'name' and 'desc' fields."})
        items = [
            {"name": normalize_text(item["name"], False)[:120], "desc": normalize_text(item["desc"], True)[:1000]}
            for item in items
        ]
        ver_id, state = get_latest_state()
        state["sections"]["services"]["items"] = items
        new_ver = save_new_state(state, f"Updated services list ({len(items)} items)")
        return json.dumps({"status": "success", "new_version": new_ver})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

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
        return f"{label}: primary_color has {ratio:.2f}:1 contrast with the white button text, it must be at least 4.5:1. Choose a darker shade."
    return None

def update_theme_style(primary_color: str = None, background_color: str = None, text_color: str = None, border_radius: str = None):
    changes, error = normalize_palette_changes({
        "primary_color": primary_color, "background_color": background_color,
        "text_color": text_color, "border_radius": border_radius
    })
    if error:
        return json.dumps({"status": "error", "message": error})
    if not changes:
        return json.dumps({"status": "error", "message": "Provide at least one property to change."})
    ver_id, state = get_latest_state()
    new_theme = {**state["theme"], **changes}
    blocked = palette_guardrail(new_theme, "Light theme")
    if blocked:
        return json.dumps({"status": "blocked_by_guardrail", "reason": blocked})
    state["theme"] = new_theme
    summary = ", ".join(f"{k}={v}" for k, v in changes.items())
    new_ver = save_new_state(state, f"Changed theme: {summary}")
    return json.dumps({"status": "success", "new_version": new_ver, "changed": changes})

def configure_dark_mode(enabled: bool, background_color: str = None, text_color: str = None, primary_color: str = None,
                        switcher_position: str = None, default_mode: str = None):
    if not isinstance(enabled, bool):
        return json.dumps({"status": "error", "message": "'enabled' must be true or false."})
    if switcher_position is not None and switcher_position not in SWITCHER_POSITIONS:
        return json.dumps({"status": "error", "message": f"switcher_position must be one of {list(SWITCHER_POSITIONS)}."})
    if default_mode is not None and default_mode not in DARK_DEFAULT_MODES:
        return json.dumps({"status": "error", "message": f"default_mode must be one of {list(DARK_DEFAULT_MODES)}."})
    changes, error = normalize_palette_changes({
        "background_color": background_color, "text_color": text_color, "primary_color": primary_color
    })
    if error:
        return json.dumps({"status": "error", "message": error})
    ver_id, state = get_latest_state()
    dark = {**state["theme"]["dark_mode"], **changes, "enabled": enabled}
    if switcher_position is not None:
        dark["switcher_position"] = switcher_position
    if default_mode is not None:
        dark["default_mode"] = default_mode
    if enabled:
        blocked = palette_guardrail(dark, "Dark mode palette")
        if blocked:
            return json.dumps({"status": "blocked_by_guardrail", "reason": blocked})
    state["theme"]["dark_mode"] = dark
    new_ver = save_new_state(state, f"Dark mode {'enabled' if enabled else 'disabled'}")
    result = {"status": "success", "new_version": new_ver, "dark_mode": dark}
    if enabled:
        result["visitor_experience"] = (
            f"Visitors see a theme switcher with three options: Light, Dark and Auto (Auto follows the visitor's OS setting live). "
            f"Switcher position: {dark['switcher_position']}. Mode before the visitor chooses: {dark['default_mode']}. "
            f"The visitor's choice is remembered in their browser and can be changed back to Auto at any time."
        )
    return json.dumps(result)

def update_card_style(mode: str, border_color: str = None, border_opacity: float = None,
                      border_width: str = None, background_color: str = None):
    if mode not in ("light", "dark", "both"):
        return json.dumps({"status": "error", "message": "mode must be 'light', 'dark' or 'both'."})
    changes = {}
    if border_color is not None:
        if not isinstance(border_color, str) or not re.fullmatch(HEX_COLOR_RE, border_color.strip()):
            return json.dumps({"status": "error", "message": "border_color must use 6-digit hex format, e.g. #ffffff."})
        changes["border_color"] = border_color.strip().lower()
    if border_opacity is not None:
        if not isinstance(border_opacity, (int, float)) or not (0.05 <= float(border_opacity) <= 1):
            return json.dumps({"status": "error", "message": "border_opacity must be a number between 0.05 and 1."})
        changes["border_opacity"] = round(float(border_opacity), 2)
    if border_width is not None:
        if not isinstance(border_width, str) or not re.fullmatch(r"[0-6]px", border_width.strip()):
            return json.dumps({"status": "error", "message": "border_width must be between '0px' and '6px'."})
        changes["border_width"] = border_width.strip()
    if background_color is not None:
        value = background_color.strip().lower() if isinstance(background_color, str) else ""
        if value in ("none", "transparent", ""):
            changes["background_color"] = None
        elif re.fullmatch(HEX_COLOR_RE, value):
            changes["background_color"] = value
        else:
            return json.dumps({"status": "error", "message": "background_color must be a 6-digit hex color or 'none'."})
    if not changes:
        return json.dumps({"status": "error", "message": "Provide at least one style property to change."})

    ver_id, state = get_latest_state()
    theme = state["theme"]
    modes = ["light", "dark"] if mode == "both" else [mode]
    for m in modes:
        new_style = {**theme["card_style"][m], **changes}
        # Card text uses the mode's text color, so a custom card background must keep it readable.
        if new_style["background_color"]:
            text_color = theme["dark_mode"]["text_color"] if m == "dark" else theme["text_color"]
            ratio = color_contrast_ratio(new_style["background_color"], text_color)
            if ratio < 4.5:
                return json.dumps({"status": "blocked_by_guardrail", "reason": f"{m} mode card background has {ratio:.2f}:1 contrast with the text, it must be at least 4.5:1."})
        theme["card_style"][m] = new_style
    note = None
    if "dark" in modes and not theme["dark_mode"]["enabled"]:
        note = "Dark mode is currently disabled, so the dark card style will only be visible after configure_dark_mode(enabled=true)."
    new_ver = save_new_state(state, f"Updated service card style ({mode} mode)")
    result = {"status": "success", "new_version": new_ver, "card_style": {m: theme["card_style"][m] for m in modes}}
    if note:
        result["note"] = note
    return json.dumps(result)

def update_hover_effect(element: str, enabled: bool = True, lift_px: int = None, scale: float = None,
                        shadow: str = None, background_color: str = None, border_color: str = None,
                        border_opacity: float = None, tint_opacity: float = None, duration_ms: int = None):
    if element not in HOVER_ELEMENTS:
        return json.dumps({"status": "error", "message": f"element must be one of {list(HOVER_ELEMENTS)}."})
    if not isinstance(enabled, bool):
        return json.dumps({"status": "error", "message": "'enabled' must be true or false."})
    given = {
        "lift_px": lift_px, "scale": scale, "shadow": shadow, "background_color": background_color,
        "border_color": border_color, "border_opacity": border_opacity, "tint_opacity": tint_opacity,
        "duration_ms": duration_ms,
    }
    given = {k: v for k, v in given.items() if v is not None}
    unsupported = [k for k in given if k not in HOVER_ELEMENTS[element]]
    if unsupported:
        return json.dumps({"status": "error", "message": f"{unsupported} not supported for '{element}'. Supported: {list(HOVER_ELEMENTS[element])}."})

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
        return json.dumps({"status": "error", "message": str(e)})
    if "shadow" in given:
        if given["shadow"] not in HOVER_SHADOWS:
            return json.dumps({"status": "error", "message": f"shadow must be one of {list(HOVER_SHADOWS)}."})
        changes["shadow"] = given["shadow"]
    for key in ("background_color", "border_color"):
        if key in given:
            value = given[key].strip().lower() if isinstance(given[key], str) else ""
            if value in ("none", ""):
                changes[key] = None
            elif re.fullmatch(HEX_COLOR_RE, value):
                changes[key] = value
            else:
                return json.dumps({"status": "error", "message": f"{key} must be a 6-digit hex color or 'none'."})
    # Buttons and arrows keep white text/icons on hover, so a hover background must stay readable.
    if changes.get("background_color"):
        ratio = color_contrast_ratio(changes["background_color"], "#ffffff")
        if ratio < 4.5:
            return json.dumps({"status": "blocked_by_guardrail", "reason": f"Hover background has {ratio:.2f}:1 contrast with the white text, it must be at least 4.5:1. Choose a darker shade."})

    ver_id, state = get_latest_state()
    effect = {**state["theme"]["hover_effects"][element], **changes, "enabled": enabled}
    state["theme"]["hover_effects"][element] = effect
    new_ver = save_new_state(state, f"{'Updated' if enabled else 'Disabled'} hover effect: {element}")
    return json.dumps({"status": "success", "new_version": new_ver, "element": element, "hover_effect": effect})

def update_testimonials(items_json: str, autoplay_seconds: int = None):
    try:
        items = json.loads(items_json) if isinstance(items_json, str) else items_json
    except json.JSONDecodeError as e:
        return json.dumps({"status": "error", "message": f"items_json is not valid JSON: {e}"})
    if not isinstance(items, list) or not items:
        return json.dumps({"status": "error", "message": "items_json must be a non-empty JSON array of objects with 'quote' and 'author'."})
    if len(items) > MAX_TESTIMONIALS:
        return json.dumps({"status": "error", "message": f"A maximum of {MAX_TESTIMONIALS} testimonials is allowed."})
    if any(
        not isinstance(item, dict)
        or set(item.keys()) != {"quote", "author"}
        or not all(isinstance(item[k], str) and item[k].strip() for k in ("quote", "author"))
        for item in items
    ):
        return json.dumps({"status": "error", "message": "Each testimonial must contain only non-empty string 'quote' and 'author' fields."})
    if autoplay_seconds is not None and (not isinstance(autoplay_seconds, int) or not (0 <= autoplay_seconds <= 30)):
        return json.dumps({"status": "error", "message": "autoplay_seconds must be an integer between 0 (off) and 30."})
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
        return json.dumps({"status": "error", "message": "Hero image must be a valid http or https URL."})
    ver_id, state = get_latest_state()
    state["sections"]["hero"]["image_url"] = image_url.strip()
    new_ver = save_new_state(state, "Updated hero banner image")
    return json.dumps({"status": "success", "new_version": new_ver})

def reorder_sections(new_order: list):
    ver_id, state = get_latest_state()
    valid_sections = set(state["sections"].keys())
    if not isinstance(new_order, list) or len(new_order) != len(valid_sections) or set(new_order) != valid_sections:
        return json.dumps({"status": "error", "message": f"Must include exact sections: {list(valid_sections)}"})
    state["section_order"] = new_order
    new_ver = save_new_state(state, f"Reordered sections to {new_order}")
    return json.dumps({"status": "success", "new_version": new_ver})

def rollback_version():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT version_id FROM site_versions WHERE status = ? ORDER BY version_id DESC LIMIT 2",
        (STATUS_ACTIVE,)
    ).fetchall()
    if len(rows) < 2:
        conn.close()
        return json.dumps({"status": "error", "message": "Already at initial version; nothing to undo."})
    latest_id = rows[0][0]
    # Non-destructive undo: mark the version as undone so it can be redone.
    conn.execute("UPDATE site_versions SET status = ? WHERE version_id = ?", (STATUS_UNDONE, latest_id))
    conn.commit()
    conn.close()
    ver_id, _ = get_latest_state()
    return json.dumps({"status": "success", "reverted_to_version": ver_id})

def redo_version():
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT version_id FROM site_versions WHERE status = ? ORDER BY version_id ASC LIMIT 1",
        (STATUS_UNDONE,)
    ).fetchone()
    if not row:
        conn.close()
        return json.dumps({"status": "error", "message": "Nothing to redo."})
    conn.execute("UPDATE site_versions SET status = ? WHERE version_id = ?", (STATUS_ACTIVE, row[0]))
    conn.commit()
    conn.close()
    return json.dumps({"status": "success", "restored_version": row[0]})

def create_approval_ticket(summary_notes: str):
    ver_id, state = get_latest_state()
    ticket_md = f"# Approved Client Change Request (Version v{ver_id})\n\n**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n**Summary:** {summary_notes}\n\n```json\n{json.dumps(state, indent=2, ensure_ascii=False)}\n```"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO change_tickets (created_at, version_id, ticket_markdown) VALUES (?, ?, ?)", (datetime.now().isoformat(), ver_id, ticket_md))
    t_id = cur.lastrowid
    conn.commit()
    conn.close()
    return json.dumps({"status": "ticket_created", "ticket_id": f"CR-{t_id:04d}", "version": ver_id})

TOOLS_SCHEMA = [
    {"type": "function", "function": {"name": "get_current_site_state", "description": "Inspect current website texts, colors, images, and section layout.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "update_text_content", "description": "Replace the full value of a text field inside a website section (hero, services, testimonials, contact). Plain text only; separate paragraphs with a blank line (\\n\\n).", "parameters": {"type": "object", "properties": {"section_id": {"type": "string"}, "field": {"type": "string"}, "new_value": {"type": "string"}}, "required": ["section_id", "field", "new_value"]}}},
    {"type": "function", "function": {"name": "update_services_list", "description": "Replace the list of services using a JSON array string of objects [{'name': '...', 'desc': '...'}].", "parameters": {"type": "object", "properties": {"items_json": {"type": "string"}}, "required": ["items_json"]}}},
    {"type": "function", "function": {"name": "update_theme_style", "description": "Update the light (default) theme. Pass one or several properties in a single call; contrast rules are checked on the final combination, so change background_color and text_color together when switching between light and dark palettes.", "parameters": {"type": "object", "properties": {"primary_color": {"type": "string", "description": "Hex, e.g. #14532d. Buttons use white text on it."}, "background_color": {"type": "string", "description": "Hex page background."}, "text_color": {"type": "string", "description": "Hex body text color."}, "border_radius": {"type": "string", "description": "e.g. '8px'."}}}}},
    {"type": "function", "function": {"name": "configure_dark_mode", "description": "Enable or disable dark mode on the website. When enabled, the site shows a theme switcher with THREE options for visitors: Light, Dark and Auto (Auto follows the visitor's operating-system setting live). The visitor's choice is remembered. You can set the dark palette, where the switcher is shown, and which mode applies before the visitor chooses.", "parameters": {"type": "object", "properties": {"enabled": {"type": "boolean"}, "background_color": {"type": "string", "description": "Dark mode hex background, e.g. #0f172a."}, "text_color": {"type": "string", "description": "Dark mode hex text color, e.g. #e2e8f0."}, "primary_color": {"type": "string", "description": "Dark mode hex button/accent color; must contrast with white text."}, "switcher_position": {"type": "string", "enum": ["corner", "top_bar", "contact"], "description": "corner = compact icon switcher in the top-right corner; top_bar = a dedicated bar at the top of the site with a labelled Light/Dark/Auto switcher; contact = an 'Appearance' row inside the contact section."}, "default_mode": {"type": "string", "enum": ["auto", "light", "dark"], "description": "Mode used before the visitor makes a choice. 'auto' (recommended) follows the visitor's OS setting."}}, "required": ["enabled"]}}},
    {"type": "function", "function": {"name": "update_testimonials", "description": "Replace the full list of client testimonials. With more than one item the section is rendered as a slider/carousel with arrows, dots and autoplay. To add a slide, read the current items first and send the complete list including the new one.", "parameters": {"type": "object", "properties": {"items_json": {"type": "string", "description": "JSON array string: [{\"quote\": \"...\", \"author\": \"Name, Role at Company\"}]. Max 10."}, "autoplay_seconds": {"type": "integer", "description": "Seconds between slides; 0 disables autoplay."}}, "required": ["items_json"]}}},
    {"type": "function", "function": {"name": "update_card_style", "description": "Style the service cards in the 'services' section, separately for light mode, dark mode, or both: outline/border color, border opacity (for soft outlines such as white at 35%), border width and card background. Use this for any request about card borders, outlines or card backgrounds. The colored top accent bar always uses the primary color.", "parameters": {"type": "object", "properties": {"mode": {"type": "string", "enum": ["light", "dark", "both"]}, "border_color": {"type": "string", "description": "Hex, e.g. #ffffff for a white outline."}, "border_opacity": {"type": "number", "description": "0.05-1. E.g. 0.35 for a subtle light outline on dark backgrounds."}, "border_width": {"type": "string", "description": "'0px' to '6px'."}, "background_color": {"type": "string", "description": "Hex card background, or 'none' for transparent."}}, "required": ["mode"]}}},
    {"type": "function", "function": {"name": "update_hover_effect", "description": "Add, change or disable the mouse-hover (and keyboard-focus) effect of a website element. Elements and the properties each supports: cta_button (lift_px, scale, shadow, background_color, duration_ms); service_cards (lift_px, scale, shadow, border_color, border_opacity, tint_opacity, duration_ms); hero_image (lift_px, scale, shadow, duration_ms); slider_arrows (scale, shadow, background_color, duration_ms). Omitted properties keep their current value. Motion is automatically disabled for visitors who prefer reduced motion.", "parameters": {"type": "object", "properties": {"element": {"type": "string", "enum": ["cta_button", "service_cards", "hero_image", "slider_arrows"]}, "enabled": {"type": "boolean", "description": "false removes the hover effect."}, "lift_px": {"type": "integer", "description": "Move up on hover, 0-12 px."}, "scale": {"type": "number", "description": "Grow on hover, 1.0-1.1 (e.g. 1.03)."}, "shadow": {"type": "string", "enum": ["none", "soft", "medium", "strong"]}, "background_color": {"type": "string", "description": "Hex hover background (buttons/arrows); must contrast with white text. 'none' to remove."}, "border_color": {"type": "string", "description": "Hex card outline color on hover, or 'none'."}, "border_opacity": {"type": "number", "description": "0.05-1, opacity of the hover outline."}, "tint_opacity": {"type": "number", "description": "0-0.2, subtle primary-color tint of the card background on hover."}, "duration_ms": {"type": "integer", "description": "Animation duration 100-600 ms."}}, "required": ["element"]}}},
    {"type": "function", "function": {"name": "update_hero_image", "description": "Change the main hero banner image URL.", "parameters": {"type": "object", "properties": {"image_url": {"type": "string"}}, "required": ["image_url"]}}},
    {"type": "function", "function": {"name": "reorder_sections", "description": "Reorder page sections. Must contain all 4: ['hero', 'services', 'testimonials', 'contact'].", "parameters": {"type": "object", "properties": {"new_order": {"type": "array", "items": {"type": "string"}}}, "required": ["new_order"]}}},
    {"type": "function", "function": {"name": "rollback_version", "description": "Undo the last website change and restore previous version.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "create_approval_ticket", "description": "Generate an official developer handoff ticket when the client approves the changes.", "parameters": {"type": "object", "properties": {"summary_notes": {"type": "string"}}, "required": ["summary_notes"]}}}
]

AVAILABLE_FUNCTIONS = {
    "get_current_site_state": get_current_site_state,
    "update_text_content": update_text_content,
    "update_services_list": update_services_list,
    "update_theme_style": update_theme_style,
    "configure_dark_mode": configure_dark_mode,
    "update_testimonials": update_testimonials,
    "update_card_style": update_card_style,
    "update_hover_effect": update_hover_effect,
    "update_hero_image": update_hero_image,
    "reorder_sections": reorder_sections,
    "rollback_version": rollback_version,
    "create_approval_ticket": create_approval_ticket
}

sessions = {}

def get_session(request: Request):
    session_id = request.cookies.get("concierge_session")
    if not session_id or session_id not in sessions:
        session_id = str(uuid.uuid4())
        sessions[session_id] = {"active_skills": [], "chat_history": []}
    return session_id, sessions[session_id]

# --- LM STUDIO MODEL MANAGEMENT ---
# The hardware fits only one model in memory, so the active model is global (not per session):
# switching unloads the current model in LM Studio and then loads the selected one.
model_lock = threading.Lock()
model_state = {"active": MODEL_NAME, "switching": False, "target": None, "error": None}

def lmstudio_request(method: str, path: str, body: dict = None, timeout: float = 10):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{LMSTUDIO_ROOT}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"LM Studio {method} {path} failed ({e.code}): {detail}") from e

def get_lmstudio_models() -> dict:
    """Map model key -> LM Studio model info (includes loaded_instances)."""
    return {m["key"]: m for m in lmstudio_request("GET", "/api/v1/models").get("models", [])}

def list_available_models():
    try:
        lm_models = get_lmstudio_models()
    except Exception:
        lm_models = {}
    result = []
    for model_id, label in ALLOWED_MODELS:
        info = lm_models.get(model_id)
        result.append({
            "id": model_id,
            "label": label,
            "installed": info is not None if lm_models else None,
            "loaded": bool(info and info.get("loaded_instances")) if lm_models else None,
            "vision": bool(info and info.get("capabilities", {}).get("vision")) if lm_models else None,
        })
    return result

def activate_model(target: str):
    """Unload every other allowed model, then make sure `target` is loaded. Blocking."""
    lm_models = get_lmstudio_models()
    if target not in lm_models:
        raise RuntimeError(f"Model '{target}' is not installed in LM Studio.")
    allowed_ids = {model_id for model_id, _ in ALLOWED_MODELS}
    for model_id in allowed_ids - {target}:
        for instance in (lm_models.get(model_id) or {}).get("loaded_instances", []):
            lmstudio_request("POST", "/api/v1/models/unload", {"instance_id": instance["id"]}, timeout=120)
    if not lm_models[target].get("loaded_instances"):
        lmstudio_request(
            "POST", "/api/v1/models/load",
            {"model": target, "context_length": LOAD_CONTEXT_LENGTH},
            timeout=MODEL_LOAD_TIMEOUT
        )

def switch_model(target: str):
    """Switch the global active model; on failure try to restore the previous one."""
    with model_lock:
        previous = model_state["active"]
        model_state.update(switching=True, target=target, error=None)
        try:
            activate_model(target)
            model_state["active"] = target
            return {"status": "success", "active": target}
        except Exception as e:
            model_state["error"] = str(e)
            try:
                activate_model(previous)
                restored = f" Restored '{previous}'."
            except Exception as restore_error:
                restored = f" Could not restore '{previous}': {restore_error}"
            return {"status": "error", "message": f"{e}{restored}", "active": previous}
        finally:
            model_state.update(switching=False, target=None)

def ensure_default_model_on_startup():
    try:
        switch_model(MODEL_NAME)
    except Exception:
        pass  # LM Studio may not be running yet; the UI reports connection errors per request.

def set_session_cookie(response: JSONResponse, session_id: str):
    response.set_cookie(
        key="concierge_session",
        value=session_id,
        httponly=True,
        samesite="lax"
    )
    return response

def build_system_prompt(active_skills, voice_mode: bool = False):
    base = (
        "You are an AI Website Maintenance Concierge working for a web agency. "
        "Your job is to help a client update their website safely via tools. "
        "Your primary language is English. Bulgarian is fully supported: if the user writes or speaks in Bulgarian, reply in Bulgarian; "
        "otherwise reply in English. When the language is unclear (e.g. only a file or screenshot without text), reply in English. "
        "Website content stays in the language it is written in unless the client asks to translate it. "
        "Before making assumptions about current values, call get_current_site_state if needed. "
        "If the user uploads a screenshot, analyze colors, layout, or UI issues shown in the image and apply matching updates. "
        "If the user uploads a document or brief, extract the content and apply it accurately. "
        "Strictly obey any ACTIVE SKILLS / BRAND GUIDELINES loaded below.\n\n"
        "PARAGRAPHS: Website text fields are plain text, never HTML or Markdown. "
        "Multi-paragraph text is supported in hero.subtitle, contact.working_hours, testimonial quotes and service descriptions: "
        "separate paragraphs with a blank line (two newline characters, \"\\n\\n\"); a single newline is a line break inside the same paragraph. "
        "To add, duplicate, remove or reorder paragraphs, first read the current value with get_current_site_state, "
        "then write the complete new text of that field with update_text_content (or update_services_list / update_testimonials for list items). "
        "Never use HTML tags such as <p> or <br>. Headings, titles, button text, email and phone are single-line fields.\n\n"
        "CAPABILITIES:\n"
        "- Testimonials slider: the testimonials section holds a list of testimonials. With 2+ items it is automatically shown as a slider "
        "(arrows, dots, autoplay). Add/edit/remove slides with update_testimonials, always sending the complete list. "
        "If you write a new testimonial yourself, tell the client it is placeholder text that should be replaced by a real client quote before publishing.\n"
        "- Dark mode: configure_dark_mode(enabled=true) adds a theme switcher with three options (Light, Dark, Auto = follow the visitor's OS setting). "
        "You can set the dark palette, the switcher position (corner; top_bar = a dedicated bar/section at the top; contact = a row in the contact section) "
        "and the default mode. If the client asks for a separate section or bar for the switcher, use switcher_position='top_bar'. "
        "This is the right tool for any request about dark mode, night mode or a theme switcher.\n"
        "- Theme: update_theme_style accepts several properties at once. When a change involves both background and text colors, send them in ONE call; "
        "never change them one by one, because the contrast guardrail checks the final combination.\n"
        "- Service cards: update_card_style changes card borders/outlines (color, opacity, width) and card background, "
        "separately for light mode, dark mode or both. For 'lighter outline in dark mode' use mode='dark' with a light color "
        "(e.g. #ffffff) and a moderate opacity (0.3-0.5).\n"
        "- Hover effects: update_hover_effect adds or changes hover effects (lift, grow, shadow, hover colors, outline, tint, speed) "
        "for the CTA button, service cards, hero image and slider arrows; enabled=false removes an effect. Prefer subtle, professional values "
        "(e.g. lift 4px, scale 1.02-1.04, soft/medium shadow, 200-300 ms).\n"
        "- If a guardrail blocks a change, explain the reason and propose compliant values instead of giving up.\n"
        "- Only say something is impossible after checking that none of the tools can achieve it; then offer the closest alternative or a developer ticket.\n"
        "- HONESTY: when a request has several parts, check each part against what the tools actually did (read the tool results). "
        "Report only what was really implemented. If any part could not be done or was only approximated, say so explicitly "
        "(e.g. 'Done: X and Y. Not possible with my tools: Z') and offer a developer ticket for it. Never imply a feature exists if no tool created it.\n"
        "- VERIFICATION: when the client asks you to confirm or check a change, or says they do not see it, ALWAYS call get_current_site_state first "
        "and report the actual stored values. Never claim something is confirmed without that call in the same turn. "
        "You cannot see the client's screen: if the stored state is correct but they do not see it, suggest reloading the page (Ctrl+F5), "
        "and for hover effects remind them to move the mouse over the element; if the state is missing the change, apply it again."
    )
    if voice_mode:
        base += VOICE_MODE_INSTRUCTIONS
    if active_skills:
        skills_text = "\n\n=== ACTIVE SKILLS & BRAND GUARDRAILS ===\n" + "\n---\n".join(active_skills)
        return base + skills_text
    return base

# Appended to the system prompt when the client has voice replies enabled: replies are read aloud,
# so they must be short and easy to listen to. Tool usage and guardrails are unchanged.
VOICE_MODE_INSTRUCTIONS = (
    "\n\nVOICE MODE IS ON: your reply will be read aloud to the client, so keep it short, clear and to the point.\n"
    "- Maximum 2-3 short sentences (about 50 words). Say what you did or found, then the single most useful next step or question.\n"
    "- Plain spoken language only: no Markdown, bullet lists, headings, tables, code, JSON or URLs.\n"
    "- Do not read out hex codes, tool names or technical field names; describe them in words (e.g. 'dark green' instead of '#14532d').\n"
    "- Do not repeat the client's request or list everything you could do; offer at most one alternative.\n"
    "- This changes only how you phrase the reply: still use tools fully and still follow all guardrails and active skills."
)

def execute_tool_call(fn_name: str, raw_arguments: str) -> str:
    """Run one model-requested tool call; always return a JSON string, never raise."""
    if fn_name not in AVAILABLE_FUNCTIONS:
        return json.dumps({"status": "error", "message": f"Unknown tool '{fn_name}'. Available: {list(AVAILABLE_FUNCTIONS.keys())}"})
    try:
        fn_args = json.loads(raw_arguments) if raw_arguments else {}
    except json.JSONDecodeError as e:
        return json.dumps({"status": "error", "message": f"Tool arguments are not valid JSON: {e}"})
    if not isinstance(fn_args, dict):
        return json.dumps({"status": "error", "message": "Tool arguments must be a JSON object."})
    try:
        return AVAILABLE_FUNCTIONS[fn_name](**fn_args)
    except TypeError as e:
        return json.dumps({"status": "error", "message": f"Invalid arguments for '{fn_name}': {e}"})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Tool '{fn_name}' failed: {e}"})

def assistant_message_to_dict(msg) -> dict:
    """Convert an SDK message object to a plain dict that can be safely re-sent."""
    entry = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        entry["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"}
            }
            for tc in msg.tool_calls
        ]
    return entry

def run_llm_turn(active_model: str, active_skills: list, turn_history: list, tools_used: list, voice_mode: bool = False):
    """Call the model, executing tool calls until it answers (or MAX_TOOL_ROUNDS is hit).

    Mutates turn_history and tools_used; returns the final model message.
    """
    msg = None
    for _ in range(MAX_TOOL_ROUNDS + 1):
        response = llm_client.chat.completions.create(
            model=active_model,
            messages=[{"role": "system", "content": build_system_prompt(active_skills, voice_mode)}] + turn_history,
            tools=TOOLS_SCHEMA
        )
        msg = response.choices[0].message
        if not msg.tool_calls:
            break
        turn_history.append(assistant_message_to_dict(msg))
        for tc in msg.tool_calls:
            tools_used.append(f"{tc.function.name}({tc.function.arguments or ''})")
            turn_history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": execute_tool_call(tc.function.name, tc.function.arguments)
            })
    return msg

def strip_images_from_history(chat_history: list):
    """Replace Base64 image blocks with a short placeholder so screenshots are not re-sent every turn."""
    for entry in chat_history:
        if entry.get("role") != "user" or not isinstance(entry.get("content"), list):
            continue
        entry["content"] = [
            {"type": "text", "text": "[Screenshot was attached here and already analyzed.]"}
            if block.get("type") == "image_url" else block
            for block in entry["content"]
        ]

_whisper_model = None
_whisper_lock = threading.Lock()

def get_whisper_model():
    """Load Whisper lazily on the first voice request so server start/reload stays fast."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        return _whisper_model

def pick_supported_language(model, audio) -> str:
    """Detect the spoken language, restricted to the supported ones (English or Bulgarian).

    Whisper sometimes labels short Bulgarian clips as Russian/Macedonian/Slovenian, so instead of
    trusting the top label we compare only the probabilities of the supported languages.
    """
    try:
        _, _, all_probs = model.detect_language(audio, vad_filter=True)
        probs = dict(all_probs)
        return max(SUPPORTED_LANGUAGES, key=lambda lang: probs.get(lang, 0.0))
    except Exception:
        return PRIMARY_LANGUAGE

def transcribe_audio(path: str):
    from faster_whisper import decode_audio
    model = get_whisper_model()
    audio = decode_audio(path)
    language = pick_supported_language(model, audio)
    segments, _ = model.transcribe(
        audio, language=language, vad_filter=True, beam_size=5, initial_prompt=WHISPER_PROMPTS[language]
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, language

def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)           # drop code blocks
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [label](url) -> label
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*•])\s+", "", text, flags=re.M)
    text = re.sub(r"[*`_#]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()

def trim_to_sentences(text: str, limit: int) -> str:
    """Cut text to at most `limit` characters, ending on a sentence boundary when possible."""
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    # Sentence end = . ! ? followed by whitespace/end, but not a list number like "2." or a decimal.
    ends = [m.end() for m in re.finditer(r"(?<!\d)[.!?…](?=\s|$)", cut)]
    if ends and ends[-1] >= limit * 0.4:
        return cut[:ends[-1]].strip()
    cut = cut[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut) + "..."

def detect_reply_language(text: str) -> str:
    cyrillic = len(re.findall(r"[Ѐ-ӿ]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "bg" if cyrillic and cyrillic >= latin * 0.3 else PRIMARY_LANGUAGE

init_db()

@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...)):
    raw = await audio.read()
    if not raw:
        return JSONResponse({"status": "error", "message": "Empty audio."}, status_code=400)
    if len(raw) > MAX_AUDIO_BYTES:
        return JSONResponse({"status": "error", "message": "Audio is too long."}, status_code=413)
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(raw)
        tmp.close()
        text, language = await run_in_threadpool(transcribe_audio, tmp.name)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Speech recognition failed: {e}"}, status_code=500)
    finally:
        os.remove(tmp.name)
    return JSONResponse({"status": "success", "text": text, "language": language})

@app.post("/api/tts")
async def api_tts(text: str = Form(...), brief: bool = Form(False), gender: str = Form(DEFAULT_VOICE_GENDER)):
    spoken = clean_for_speech(text)
    # Auto-played replies are capped at a sentence boundary; the manual "Read aloud" button reads everything.
    spoken = trim_to_sentences(spoken, MAX_BRIEF_TTS_CHARS) if brief else spoken[:MAX_TTS_CHARS]
    if not spoken:
        return JSONResponse({"status": "error", "message": "Nothing to speak."}, status_code=400)
    import edge_tts
    lang = detect_reply_language(spoken)
    # The online edge-tts service intermittently answers with "No audio was received";
    # retry with a short backoff and use a fallback voice on the last attempt.
    primary, fallback = TTS_VOICES.get(gender, TTS_VOICES[DEFAULT_VOICE_GENDER])[lang]
    voices = [primary, primary, fallback]
    async def synthesize(voice: str) -> bytes:
        audio = bytearray()
        async for chunk in edge_tts.Communicate(spoken, voice).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    last_error = None
    for attempt, voice in enumerate(voices):
        try:
            # Cap each attempt so a hanging connection does not keep the user waiting.
            audio = await asyncio.wait_for(synthesize(voice), timeout=TTS_ATTEMPT_TIMEOUT)
            if audio:
                return Response(content=audio, media_type="audio/mpeg")
            last_error = "empty audio"
        except asyncio.TimeoutError:
            last_error = f"timeout after {TTS_ATTEMPT_TIMEOUT}s"
        except Exception as e:
            last_error = e
        await asyncio.sleep(0.6 * (attempt + 1))
    return JSONResponse({"status": "error", "message": f"Text-to-speech failed after {len(voices)} attempts: {last_error}"}, status_code=502)

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        # The page remembers which build it was served from, so it can detect when it is outdated.
        return f.read().replace("__APP_BUILD__", app_build())

def app_build() -> str:
    """Identifier of the current frontend version (changes whenever index.html is edited)."""
    return str(int(os.path.getmtime(INDEX_PATH)))

@app.get("/api/state")
async def api_state(request: Request):
    session_id, session = get_session(request)
    ver_id, state = get_latest_state()
    response = JSONResponse({"version": ver_id, "state": state, "active_skills_count": len(session["active_skills"]), "app_build": app_build()})
    return set_session_cookie(response, session_id)

@app.post("/api/undo")
async def api_undo():
    res = json.loads(rollback_version())
    ver_id, state = get_latest_state()
    return JSONResponse({"result": res, "version": ver_id, "state": state, "app_build": app_build()})

@app.get("/api/models")
def api_models():
    return JSONResponse({
        "models": list_available_models(),
        "active": model_state["active"],
        "switching": model_state["switching"],
        "target": model_state["target"],
    })

@app.post("/api/model")
def api_select_model(model: str = Form(...)):
    # Sync endpoint: FastAPI runs it in a worker thread, so the long LM Studio load does not block the server.
    if model not in {model_id for model_id, _ in ALLOWED_MODELS}:
        return JSONResponse({"status": "error", "message": f"Unknown model '{model}'."}, status_code=400)
    if model_state["switching"]:
        return JSONResponse({"status": "error", "message": "Another model switch is already in progress."}, status_code=409)
    result = switch_model(model)
    return JSONResponse(result, status_code=200 if result["status"] == "success" else 500)

@app.post("/api/redo")
async def api_redo():
    res = json.loads(redo_version())
    ver_id, state = get_latest_state()
    return JSONResponse({"result": res, "version": ver_id, "state": state, "app_build": app_build()})

@app.post("/api/chat")
async def api_chat(
    request: Request,
    message: str = Form(""),
    attachment_type: str = Form("auto"),
    voice_mode: bool = Form(False),
    file: UploadFile = File(None)
):
    session_id, session = get_session(request)
    if model_state["switching"]:
        response = JSONResponse({
            "reply": f"The AI model is being switched to '{model_state['target']}'. Please wait until it is loaded and try again.",
            "tools_used": []
        })
        return set_session_cookie(response, session_id)
    active_model = model_state["active"]
    active_skills = session["active_skills"]
    chat_history = session["chat_history"]
    tools_used = []
    skill_loaded = None
    user_content_blocks = []

    if message.strip():
        user_content_blocks.append({"type": "text", "text": message.strip()})

    if file and file.filename:
        raw_bytes = await file.read()
        fname = file.filename.lower()

        if fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
            if fname.endswith(".png"):
                mime = "image/png"
            elif fname.endswith(".webp"):
                mime = "image/webp"
            else:
                mime = "image/jpeg"
            b64 = base64.b64encode(raw_bytes).decode("utf-8")
            user_content_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
            if not message.strip():
                user_content_blocks.insert(0, {"type": "text", "text": f"Analyze this attached screenshot ({file.filename}) and apply relevant website updates or suggest improvements."})
        else:
            text_content = raw_bytes.decode("utf-8", errors="ignore")
            is_skill = (attachment_type == "skill") or ("skill" in fname) or ("guideline" in fname) or ("handoff" in fname)
            if is_skill:
                active_skills.append(f"[Skill File: {file.filename}]\n{text_content}")
                skill_loaded = file.filename
                user_content_blocks.append({
                    "type": "text",
                    "text": f"[System Note: The client attached a Skill/Guideline file '{file.filename}'. It is now active in your system instructions. Acknowledge the rules and apply any immediate instructions.]"
                })
            else:
                user_content_blocks.append({
                    "type": "text",
                    "text": f"[Attached Document: {file.filename}]\n{text_content}"
                })

    if not user_content_blocks:
        response = JSONResponse({"reply": "Please enter a message or attach a file/screenshot.", "tools_used": []})
        return set_session_cookie(response, session_id)

    # Work on a copy so a failed turn never leaves a half-finished tool exchange in the session.
    turn_history = chat_history + [{"role": "user", "content": user_content_blocks}]

    try:
        # The OpenAI client is blocking; run it in a worker thread so the server stays responsive.
        msg = await run_in_threadpool(run_llm_turn, active_model, active_skills, turn_history, tools_used, voice_mode)

        if msg.tool_calls:
            bot_reply = "Stopped after too many consecutive tool calls. Please check the preview and rephrase your request if needed."
        else:
            bot_reply = msg.content or "Changes applied."
        turn_history.append({"role": "assistant", "content": bot_reply})
        strip_images_from_history(turn_history)
        chat_history[:] = turn_history

    except APIConnectionError:
        bot_reply = (
            f"Cannot reach LM Studio at {LLM_BASE_URL}. Make sure LM Studio is running, "
            f"the model '{active_model}' is loaded, and the local server is started (Developer tab -> Start Server)."
        )
    except Exception as e:
        bot_reply = f"Error communicating with local LM Studio model: {str(e)}"
        if skill_loaded:
            bot_reply += f"\n(The Skill file '{skill_loaded}' is still active.)"

    ver_id, state = get_latest_state()
    response = JSONResponse({
        "reply": bot_reply,
        "tools_used": tools_used,
        "skill_loaded": skill_loaded,
        "model": active_model,
        "app_build": app_build(),
        "version": ver_id,
        "state": state
    })
    return set_session_cookie(response, session_id)
