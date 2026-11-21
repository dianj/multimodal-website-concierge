"""End-to-end browser tests (Playwright) for the chat pane and the live website preview.

The LLM is the scripted fake from tests/fake_lmstudio.py, so each test is deterministic.
"""
import json

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

DARK_BG = "rgb(15, 23, 42)"      # dark_mode.background_color #0f172a
LIGHT_BG = "rgb(255, 255, 255)"  # theme.background_color #ffffff


def open_app(page: Page, app_url: str):
    page.goto(app_url)
    expect(page.locator("#siteRoot h1")).to_have_text("Modern Digital Solutions for Your Business")


def send(page: Page, text: str):
    page.locator("#promptInput").fill(text)
    page.locator("#promptInput").press("Enter")


def last_bot_message(page: Page):
    return page.locator(".msg.bot").last


# --- Chat pane -------------------------------------------------------------------------

def test_preview_renders_the_default_site(page: Page, app_url):
    open_app(page, app_url)
    expect(page.locator(".service-card")).to_have_count(3)
    expect(page.locator("#verLabel")).to_have_text("v1")


def test_enter_sends_message_and_preview_updates(page: Page, app_url, fake_lm):
    fake_lm.script(("update_text_content", {"section_id": "hero", "field": "title", "new_value": "Hello from a test"}),
                   reply="I updated the title.")
    open_app(page, app_url)
    send(page, "Change the title")
    expect(last_bot_message(page)).to_contain_text("I updated the title.")
    expect(last_bot_message(page).locator(".tool-tag")).to_contain_text("update_text_content")
    expect(page.locator("#siteRoot h1")).to_have_text("Hello from a test")
    expect(page.locator("#verLabel")).to_have_text("v2")


def test_shift_enter_adds_a_new_line_without_sending(page: Page, app_url, fake_lm):
    open_app(page, app_url)
    box = page.locator("#promptInput")
    box.fill("first line")
    box.press("Shift+Enter")
    box.type("second line")
    expect(box).to_have_value("first line\nsecond line")
    assert fake_lm.requests == []


def test_markdown_reply_is_rendered_safely(page: Page, app_url, fake_lm):
    fake_lm.script(reply='**Bold** and `code` <img src=x onerror="window.__xss = 1">')
    open_app(page, app_url)
    send(page, "hi")
    reply = last_bot_message(page)
    expect(reply.locator("strong")).to_have_text("Bold")
    expect(reply.locator("code")).to_have_text("code")
    expect(page.locator(".messages img")).to_have_count(0)
    assert page.evaluate("window.__xss") is None


def test_font_size_controls_persist_after_reload(page: Page, app_url):
    open_app(page, app_url)
    page.get_by_role("button", name="Larger text").click()
    page.get_by_role("button", name="Larger text").click()
    expect(page.locator("#fontSizeLabel")).to_have_text("16px")
    page.reload()
    expect(page.locator("#fontSizeLabel")).to_have_text("16px")


def test_outdated_page_shows_reload_banner(page: Page, app_url, srv, monkeypatch):
    open_app(page, app_url)
    expect(page.locator("#updateBanner")).to_be_hidden()
    monkeypatch.setattr(srv, "app_build", lambda: "1")  # the server now reports a newer frontend build
    page.get_by_role("button", name="Undo Last Change").click()
    expect(page.locator("#updateBanner")).to_be_visible()


def test_model_selector_switches_the_loaded_model(page: Page, app_url, fake_lm):
    open_app(page, app_url)
    page.locator("#modelSelect").select_option("qwen3.8-27b")
    expect(last_bot_message(page)).to_contain_text("The active model is now Qwen 3.8 27B.")
    assert fake_lm.loaded == {"qwen3.8-27b"}


def test_voice_reply_uses_selected_gender_and_brief_mode(page: Page, app_url, fake_lm):
    page.add_init_script("HTMLMediaElement.prototype.play = () => Promise.resolve();")
    tts_requests = []

    def fake_tts(route):
        tts_requests.append(route.request.post_data)
        route.fulfill(status=200, content_type="audio/mpeg", body=b"ID3")

    page.route("**/api/tts", fake_tts)
    fake_lm.script(reply="Done, the colors are updated.")
    open_app(page, app_url)
    page.locator("#voiceReplies").check()
    page.locator("#voiceGender").select_option("female")
    send(page, "update colors")
    expect(last_bot_message(page)).to_contain_text("Done, the colors are updated.")
    for _ in range(50):  # the TTS request is sent right after the reply is shown
        if tts_requests:
            break
        page.wait_for_timeout(100)
    assert len(tts_requests) == 1
    body = tts_requests[0]
    assert 'name="gender"' in body and "female" in body
    assert 'name="brief"' in body
    assert "VOICE MODE IS ON" in fake_lm.last_system_prompt()


def test_ticket_panel_shows_new_ticket_and_updates_status(page: Page, app_url, fake_lm, srv):
    fake_lm.script(("create_support_ticket", {"title": "Newsletter signup form",
                                              "client_request": "Add a newsletter form with an email field.",
                                              "reason": "No tool can add forms.", "priority": "high"}),
                   reply="Created ticket CR-0001.")
    open_app(page, app_url)
    expect(page.locator("#ticketBadge")).to_be_hidden()
    send(page, "Yes, please create a ticket")
    expect(page.locator("#ticketBadge")).to_have_text("1")

    page.get_by_role("button", name="Tickets").click()
    card = page.locator(".ticket-card", has_text="CR-0001")
    expect(card).to_contain_text("Newsletter signup form")
    expect(card.locator(".ticket-type")).to_have_text("Feature request")
    card.get_by_role("button", name="Show details").click()
    expect(card.locator(".ticket-details")).to_contain_text("No tool can add forms.")

    card.get_by_label("Status of CR-0001").select_option("done")
    expect(page.locator("#ticketBadge")).to_be_hidden()   # no open tickets left
    assert srv.get_ticket(1)["status"] == "done"
    page.keyboard.press("Escape")
    expect(page.locator("#ticketPanel")).to_be_hidden()


# --- Live website preview --------------------------------------------------------------

def test_undo_and_redo_buttons(page: Page, app_url, fake_lm):
    fake_lm.script(("update_text_content", {"section_id": "hero", "field": "title", "new_value": "Changed"}))
    open_app(page, app_url)
    send(page, "change it")
    expect(page.locator("#siteRoot h1")).to_have_text("Changed")
    page.get_by_role("button", name="Undo Last Change").click()
    expect(page.locator("#siteRoot h1")).to_have_text("Modern Digital Solutions for Your Business")
    page.get_by_role("button", name="Redo").click()
    expect(page.locator("#siteRoot h1")).to_have_text("Changed")


def test_testimonials_become_a_working_slider(page: Page, app_url, fake_lm):
    items = [{"quote": f"Quote {i}", "author": f"Author {i}"} for i in range(1, 4)]
    fake_lm.script(("update_testimonials", {"items_json": json.dumps(items), "autoplay_seconds": 0}))
    open_app(page, app_url)
    send(page, "add testimonials")
    expect(page.locator(".slider-dot")).to_have_count(3)
    page.get_by_role("button", name="Next testimonial").click()
    expect(page.locator(".slider-track")).to_have_attribute("style", "transform: translateX(-100%);")


def test_dark_mode_switcher_follows_os_and_visitor_choice(page: Page, app_url, fake_lm):
    page.emulate_media(color_scheme="dark")
    fake_lm.script(("configure_dark_mode", {"enabled": True, "switcher_position": "top_bar", "default_mode": "auto"}))
    open_app(page, app_url)
    root = page.locator("#siteRoot")
    expect(root).to_have_css("background-color", LIGHT_BG)  # dark mode not enabled yet
    send(page, "add dark mode")
    expect(page.locator(".theme-bar")).to_be_visible()
    expect(root).to_have_css("background-color", DARK_BG)   # Auto + OS in dark mode
    page.locator(".theme-bar [role=radio]", has_text="Light").click()
    expect(root).to_have_css("background-color", LIGHT_BG)  # visitor override
    page.locator(".theme-bar [role=radio]", has_text="Auto").click()
    expect(root).to_have_css("background-color", DARK_BG)
    page.emulate_media(color_scheme="light")                 # OS switches live
    expect(root).to_have_css("background-color", LIGHT_BG)


def test_hover_effect_is_applied_on_mouse_over(page: Page, app_url, fake_lm):
    fake_lm.script(("update_hover_effect", {"element": "service_cards", "lift_px": 4, "scale": 1.03, "shadow": "soft"}))
    open_app(page, app_url)
    card = page.locator(".service-card").first
    expect(card).to_have_css("transform", "none")
    send(page, "add a hover effect to the cards")
    expect(last_bot_message(page)).to_contain_text("Done.")
    card.hover()
    expect(card).not_to_have_css("transform", "none")
    expect(card).not_to_have_css("box-shadow", "none")
