"""Unit tests for the agent tools and guardrails (no browser, no LLM)."""
import json

from concierge import db, registry, site_state, tickets, tool_schemas, tools, voice


def call(fn, *args, **kwargs):
    return json.loads(fn(*args, **kwargs))


def state():
    return db.get_latest_state()[1]


# --- Theme & contrast ------------------------------------------------------------------

def test_theme_switch_to_dark_palette_in_one_call():
    result = call(tools.update_theme_style, background_color="#0F172A", text_color="#e2e8f0")
    assert result["status"] == "success"
    assert state()["theme"]["background_color"] == "#0f172a"  # normalized to lowercase


def test_theme_single_color_step_that_breaks_contrast_is_blocked():
    result = call(tools.update_theme_style, background_color="#0f172a")  # dark text stays -> unreadable
    assert result["status"] == "blocked_by_guardrail"
    assert state()["theme"]["background_color"] == "#ffffff"


def test_primary_color_must_contrast_with_white_button_text():
    assert call(tools.update_theme_style, primary_color="#ef4444")["status"] == "blocked_by_guardrail"
    assert call(tools.update_theme_style, primary_color="#14532d")["status"] == "success"


def test_theme_rejects_non_hex_values():
    assert call(tools.update_theme_style, primary_color="red")["status"] == "error"


# --- Text content ----------------------------------------------------------------------

def test_mandatory_contact_fields_cannot_be_emptied():
    assert call(tools.update_text_content, "contact", "phone", "   ")["status"] == "blocked_by_guardrail"


def test_list_fields_cannot_be_overwritten_with_text():
    assert call(tools.update_text_content, "services", "items", "oops")["status"] == "error"
    assert isinstance(state()["sections"]["services"]["items"], list)


def test_paragraphs_are_kept_only_in_multiline_fields():
    call(tools.update_text_content, "hero", "subtitle", "First.\r\n\r\n\r\nSecond.")
    call(tools.update_text_content, "hero", "title", "Line one\n\nline two")
    hero = state()["sections"]["hero"]
    assert hero["subtitle"] == "First.\n\nSecond."
    assert hero["title"] == "Line one line two"


def test_hero_image_accepts_real_urls_and_rejects_other_schemes():
    # Regression: an earlier regex rejected every URL containing the letter "s" after the scheme.
    assert call(tools.update_hero_image, "https://images.unsplash.com/photo-1?w=1")["status"] == "success"
    assert call(tools.update_hero_image, "javascript:alert(1)")["status"] == "error"


# --- Testimonials, dark mode, cards, hover --------------------------------------------

def test_testimonials_with_two_items_become_a_slider():
    items = [{"quote": "Great.", "author": "A"}, {"quote": "Fast.\n\nReliable.", "author": "B"}]
    result = call(tools.update_testimonials, json.dumps(items), 5)
    assert result["layout"] == "slider"
    assert state()["sections"]["testimonials"]["autoplay_seconds"] == 5


def test_testimonials_validation():
    assert call(tools.update_testimonials, "[]")["status"] == "error"
    assert call(tools.update_testimonials, '[{"quote": "x"}]')["status"] == "error"


def test_dark_mode_validates_options_and_palette():
    assert call(tools.configure_dark_mode, True, switcher_position="footer")["status"] == "error"
    blocked = call(tools.configure_dark_mode, True, background_color="#0f172a", text_color="#1e293b")
    assert blocked["status"] == "blocked_by_guardrail"
    ok = call(tools.configure_dark_mode, True, switcher_position="top_bar", default_mode="auto")
    assert ok["status"] == "success" and "Light, Dark and Auto" in ok["visitor_experience"]


def test_card_background_must_keep_text_readable():
    assert call(tools.update_card_style, "light", background_color="#111111")["status"] == "blocked_by_guardrail"
    result = call(tools.update_card_style, "dark", border_color="#FFFFFF", border_opacity=0.35)
    assert result["card_style"]["dark"] == {"border_color": "#ffffff", "border_opacity": 0.35,
                                            "border_width": "1px", "background_color": None}


def test_hover_effect_whitelist_and_ranges():
    assert call(tools.update_hover_effect, "hero_image", background_color="#000000")["status"] == "error"
    assert call(tools.update_hover_effect, "hero_image", scale=1.5)["status"] == "error"
    assert call(tools.update_hover_effect, "hero_image", lift_px=True)["status"] == "error"  # bool is not a number
    assert call(tools.update_hover_effect, "cta_button", background_color="#93c5fd")["status"] == "blocked_by_guardrail"
    ok = call(tools.update_hover_effect, "service_cards", lift_px=4, scale=1.03, shadow="soft")
    assert ok["hover_effect"]["enabled"] is True


# --- Versioning ------------------------------------------------------------------------

def test_undo_is_non_destructive_and_redo_restores():
    call(tools.update_text_content, "hero", "title", "Version two")
    assert call(tools.rollback_version)["status"] == "success"
    assert state()["sections"]["hero"]["title"] != "Version two"
    assert call(tools.redo_version)["status"] == "success"
    assert state()["sections"]["hero"]["title"] == "Version two"


def test_new_change_after_undo_discards_redo_branch():
    call(tools.update_text_content, "hero", "title", "A")
    call(tools.rollback_version)
    call(tools.update_text_content, "hero", "title", "B")
    assert call(tools.redo_version)["status"] == "error"


def test_old_snapshots_are_upgraded_on_read():
    legacy = json.loads(json.dumps(site_state.DEFAULT_SITE_STATE))
    legacy["sections"]["testimonials"] = {"heading": "Old", "quote": "Legacy quote", "author": "X"}
    del legacy["theme"]["dark_mode"]
    db.save_new_state(legacy, "legacy snapshot")
    upgraded = state()
    assert upgraded["sections"]["testimonials"]["items"] == [{"quote": "Legacy quote", "author": "X"}]
    assert {"dark_mode", "card_style", "hover_effects"} <= set(upgraded["theme"])


def test_approval_ticket_is_stored():
    result = call(tickets.create_approval_ticket, "Approved new colors")
    assert result["ticket_id"] == "CR-0001"


# --- Agent-loop helpers ----------------------------------------------------------------

def test_bad_tool_calls_return_errors_instead_of_raising():
    assert "Unknown tool" in registry.execute_tool_call("drop_database", "{}")
    assert "not valid JSON" in registry.execute_tool_call("reorder_sections", "{bad")
    assert "Invalid arguments" in registry.execute_tool_call("reorder_sections", '{"x": 1}')


def test_every_tool_schema_has_an_implementation():
    names = {tool["function"]["name"] for tool in tool_schemas.TOOLS_SCHEMA}
    assert names == set(registry.AVAILABLE_FUNCTIONS) and len(names) == 14


def test_spoken_reply_is_trimmed_at_a_sentence_not_a_list_number():
    text = "Intro sentence here. " * 10 + "1. First point is long enough. 2. Second point"
    trimmed = voice.trim_to_sentences(text, 240)
    assert trimmed.endswith(".") and not trimmed.endswith("2.")


def test_reply_language_detection():
    assert voice.detect_reply_language("Done!") == "en"
    assert voice.detect_reply_language("#14532d") == "en"
    assert voice.detect_reply_language("Готово! Смених цвета.") == "bg"
