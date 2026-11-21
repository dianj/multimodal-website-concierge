"""The tool schemas sent to the model and the dispatcher that runs the model's tool calls."""
import json

from .tickets import create_approval_ticket, create_support_ticket, get_ticket_status
from .tools import (configure_dark_mode, get_current_site_state, reorder_sections, rollback_version,
                    update_card_style, update_hero_image, update_hover_effect, update_services_list,
                    update_testimonials, update_text_content, update_theme_style)

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
    {"type": "function", "function": {"name": "create_approval_ticket", "description": "Create an approval ticket (type approval) for the developers when the client says they are happy with / approve the current website. It includes the full approved site state.", "parameters": {"type": "object", "properties": {"summary_notes": {"type": "string"}}, "required": ["summary_notes"]}}},
    {"type": "function", "function": {"name": "create_support_ticket", "description": "Create a developer ticket for something you cannot do with your tools (feature_request, e.g. a newsletter form, a new page or section type, custom code) or a problem that needs a human (support). Only call it after the client has agreed to create a ticket. Then tell the client the ticket number.", "parameters": {"type": "object", "properties": {"title": {"type": "string", "description": "Short title, e.g. 'Add a newsletter signup form'."}, "client_request": {"type": "string", "description": "What the client asked for, in detail and in their own terms."}, "reason": {"type": "string", "description": "Why it cannot be done with the available tools."}, "ticket_type": {"type": "string", "enum": ["feature_request", "support"]}, "priority": {"type": "string", "enum": ["low", "normal", "high"]}, "partial_work": {"type": "string", "description": "Optional: related changes you already applied."}}, "required": ["title", "client_request", "reason"]}}},
    {"type": "function", "function": {"name": "get_ticket_status", "description": "Look up developer tickets. With ticket_id (e.g. 'CR-0003') returns that ticket's status and details; without it, lists the 10 most recent tickets. Use it when the client asks about a ticket or its progress.", "parameters": {"type": "object", "properties": {"ticket_id": {"type": "string"}}}}},
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
    "create_approval_ticket": create_approval_ticket,
    "create_support_ticket": create_support_ticket,
    "get_ticket_status": get_ticket_status,
}

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
