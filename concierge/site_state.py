"""The website's JSON state: defaults, schema migration of stored snapshots and text normalization."""
import re

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
            "image_url": "https://images.unsplash.com/photo-1498050108023-c5249f4df085?auto=format&fit=crop&w=1000&q=80",
            "image_alt": ""     # the <img alt>; "" = decorative until the client describes the image
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
                {"quote": "Working with this team transformed our online presence in just three weeks!",
                 "author": "Elena Petrova, CEO at TechStart"}
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

def upgrade_state(state: dict) -> dict:
    """Bring older stored versions up to the current schema (used for every read, incl. undo/redo)."""
    testimonials = state["sections"].get("testimonials", {})
    if "items" not in testimonials:
        quote, author = testimonials.pop("quote", ""), testimonials.pop("author", "")
        testimonials["items"] = [{"quote": quote, "author": author}] if quote else []
    testimonials.setdefault("autoplay_seconds", 6)
    state["sections"].get("hero", {}).setdefault("image_alt", "")
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

# Fields rendered as paragraphs (blank line = new paragraph). All other text fields are single-line.
MULTILINE_FIELDS = {("hero", "subtitle"), ("contact", "working_hours")}

def normalize_text(value: str, multiline: bool) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not multiline:
        return re.sub(r"\s*\n\s*", " ", value)
    lines = [line.strip() for line in value.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
