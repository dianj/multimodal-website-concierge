"""Unit tests for the agent tools and guardrails (no browser, no LLM)."""
import json


def call(fn, *args, **kwargs):
    return json.loads(fn(*args, **kwargs))


def state(srv):
    return srv.get_latest_state()[1]


# --- Theme & contrast ------------------------------------------------------------------

def test_theme_switch_to_dark_palette_in_one_call(srv):
    result = call(srv.update_theme_style, background_color="#0F172A", text_color="#e2e8f0")
    assert result["status"] == "success"
    assert state(srv)["theme"]["background_color"] == "#0f172a"  # normalized to lowercase


def test_theme_single_color_step_that_breaks_contrast_is_blocked(srv):
    result = call(srv.update_theme_style, background_color="#0f172a")  # dark text stays -> unreadable
    assert result["status"] == "blocked_by_guardrail"
    assert state(srv)["theme"]["background_color"] == "#ffffff"


def test_primary_color_must_contrast_with_white_button_text(srv):
    assert call(srv.update_theme_style, primary_color="#ef4444")["status"] == "blocked_by_guardrail"
    assert call(srv.update_theme_style, primary_color="#14532d")["status"] == "success"


def test_theme_rejects_non_hex_values(srv):
    assert call(srv.update_theme_style, primary_color="red")["status"] == "error"


# --- Text content ----------------------------------------------------------------------

def test_mandatory_contact_fields_cannot_be_emptied(srv):
    assert call(srv.update_text_content, "contact", "phone", "   ")["status"] == "blocked_by_guardrail"


def test_list_fields_cannot_be_overwritten_with_text(srv):
    assert call(srv.update_text_content, "services", "items", "oops")["status"] == "error"
    assert isinstance(state(srv)["sections"]["services"]["items"], list)


def test_paragraphs_are_kept_only_in_multiline_fields(srv):
    call(srv.update_text_content, "hero", "subtitle", "First.\r\n\r\n\r\nSecond.")
    call(srv.update_text_content, "hero", "title", "Line one\n\nline two")
    hero = state(srv)["sections"]["hero"]
    assert hero["subtitle"] == "First.\n\nSecond."
    assert hero["title"] == "Line one line two"


def test_hero_image_accepts_real_urls_and_rejects_other_schemes(srv):
    # Regression: an earlier regex rejected every URL containing the letter "s" after the scheme.
    assert call(srv.update_hero_image, "https://images.unsplash.com/photo-1?w=1")["status"] == "success"
    assert call(srv.update_hero_image, "javascript:alert(1)")["status"] == "error"


# --- Testimonials, dark mode, cards, hover --------------------------------------------

def test_testimonials_with_two_items_become_a_slider(srv):
    items = [{"quote": "Great.", "author": "A"}, {"quote": "Fast.\n\nReliable.", "author": "B"}]
    result = call(srv.update_testimonials, json.dumps(items), 5)
    assert result["layout"] == "slider"
    assert state(srv)["sections"]["testimonials"]["autoplay_seconds"] == 5


def test_testimonials_validation(srv):
    assert call(srv.update_testimonials, "[]")["status"] == "error"
    assert call(srv.update_testimonials, '[{"quote": "x"}]')["status"] == "error"


def test_dark_mode_validates_options_and_palette(srv):
    assert call(srv.configure_dark_mode, True, switcher_position="footer")["status"] == "error"
    blocked = call(srv.configure_dark_mode, True, background_color="#0f172a", text_color="#1e293b")
    assert blocked["status"] == "blocked_by_guardrail"
    ok = call(srv.configure_dark_mode, True, switcher_position="top_bar", default_mode="auto")
    assert ok["status"] == "success" and "Light, Dark and Auto" in ok["visitor_experience"]


def test_card_background_must_keep_text_readable(srv):
    assert call(srv.update_card_style, "light", background_color="#111111")["status"] == "blocked_by_guardrail"
    result = call(srv.update_card_style, "dark", border_color="#FFFFFF", border_opacity=0.35)
    assert result["card_style"]["dark"] == {"border_color": "#ffffff", "border_opacity": 0.35,
                                            "border_width": "1px", "background_color": None}


def test_hover_effect_whitelist_and_ranges(srv):
    assert call(srv.update_hover_effect, "hero_image", background_color="#000000")["status"] == "error"
    assert call(srv.update_hover_effect, "hero_image", scale=1.5)["status"] == "error"
    assert call(srv.update_hover_effect, "hero_image", lift_px=True)["status"] == "error"  # bool is not a number
    assert call(srv.update_hover_effect, "cta_button", background_color="#93c5fd")["status"] == "blocked_by_guardrail"
    ok = call(srv.update_hover_effect, "service_cards", lift_px=4, scale=1.03, shadow="soft")
    assert ok["hover_effect"]["enabled"] is True


# --- Versioning ------------------------------------------------------------------------

def test_undo_is_non_destructive_and_redo_restores(srv):
    call(srv.update_text_content, "hero", "title", "Version two")
    assert call(srv.rollback_version)["status"] == "success"
    assert state(srv)["sections"]["hero"]["title"] != "Version two"
    assert call(srv.redo_version)["status"] == "success"
    assert state(srv)["sections"]["hero"]["title"] == "Version two"


def test_new_change_after_undo_discards_redo_branch(srv):
    call(srv.update_text_content, "hero", "title", "A")
    call(srv.rollback_version)
    call(srv.update_text_content, "hero", "title", "B")
    assert call(srv.redo_version)["status"] == "error"


def test_old_snapshots_are_upgraded_on_read(srv):
    legacy = json.loads(json.dumps(srv.DEFAULT_SITE_STATE))
    legacy["sections"]["testimonials"] = {"heading": "Old", "quote": "Legacy quote", "author": "X"}
    del legacy["theme"]["dark_mode"]
    srv.save_new_state(legacy, "legacy snapshot")
    upgraded = state(srv)
    assert upgraded["sections"]["testimonials"]["items"] == [{"quote": "Legacy quote", "author": "X"}]
    assert {"dark_mode", "card_style", "hover_effects"} <= set(upgraded["theme"])


def test_approval_ticket_is_stored(srv):
    result = call(srv.create_approval_ticket, "Approved new colors")
    assert result["ticket_id"] == "CR-0001"


# --- Agent-loop helpers ----------------------------------------------------------------

def test_bad_tool_calls_return_errors_instead_of_raising(srv):
    assert "Unknown tool" in srv.execute_tool_call("drop_database", "{}")
    assert "not valid JSON" in srv.execute_tool_call("reorder_sections", "{bad")
    assert "Invalid arguments" in srv.execute_tool_call("reorder_sections", '{"x": 1}')


def test_every_tool_schema_has_an_implementation(srv):
    names = {tool["function"]["name"] for tool in srv.TOOLS_SCHEMA}
    assert names == set(srv.AVAILABLE_FUNCTIONS) and len(names) == 14


def test_spoken_reply_is_trimmed_at_a_sentence_not_a_list_number(srv):
    text = "Intro sentence here. " * 10 + "1. First point is long enough. 2. Second point"
    trimmed = srv.trim_to_sentences(text, 240)
    assert trimmed.endswith(".") and not trimmed.endswith("2.")


def test_reply_language_detection(srv):
    assert srv.detect_reply_language("Done!") == "en"
    assert srv.detect_reply_language("#14532d") == "en"
    assert srv.detect_reply_language("Готово! Смених цвета.") == "bg"
