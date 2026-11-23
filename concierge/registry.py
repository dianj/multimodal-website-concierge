"""The dispatcher that runs the model's tool calls. The schemas sent to the model are in tool_schemas.py."""
import json

from .tickets import create_approval_ticket, create_support_ticket, get_ticket_status
from .tools import (
    configure_dark_mode,
    get_current_site_state,
    reorder_sections,
    rollback_version,
    update_card_style,
    update_hero_image,
    update_hover_effect,
    update_services_list,
    update_testimonials,
    update_text_content,
    update_theme_style,
)

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
        return json.dumps({"status": "error",
                           "message": f"Unknown tool '{fn_name}'. Available: {list(AVAILABLE_FUNCTIONS.keys())}"})
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
