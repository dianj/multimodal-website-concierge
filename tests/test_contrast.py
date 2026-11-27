"""Review T21.A1: the contrast guardrail checks the whole resulting site, not only the piece being changed."""
import json
import os

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from concierge import config, db, guardrails, tools


def call(fn, *args, **kwargs):
    return json.loads(fn(*args, **kwargs))


def state():
    return db.get_latest_state()[1]


def test_a_theme_text_color_that_makes_a_card_unreadable_is_blocked():
    """Probe: card background #f1f5f9, then theme text #f8fafc -> both were accepted, card contrast 1.05:1."""
    assert call(tools.update_card_style, "light", background_color="#f1f5f9")["status"] == "success"
    result = call(tools.update_theme_style, background_color="#0f172a", text_color="#f8fafc")
    assert result["status"] == "blocked_by_guardrail" and "card" in result["reason"]
    assert state()["theme"]["text_color"] == "#1e293b"
    assert guardrails.audit_contrast(state()) == []


def test_a_dark_text_color_that_makes_a_dark_card_unreadable_is_blocked():
    """Probe: dark card #1e293b, then dark text #111827 -> 1.21:1 was saved."""
    assert call(tools.configure_dark_mode, True)["status"] == "success"
    assert call(tools.update_card_style, "dark", background_color="#1e293b")["status"] == "success"
    result = call(tools.configure_dark_mode, True, background_color="#f8fafc", text_color="#111827")
    assert result["status"] == "blocked_by_guardrail" and "card" in result["reason"]
    assert guardrails.audit_contrast(state()) == []


def test_a_dark_primary_color_must_keep_the_white_button_text_readable():
    result = call(tools.configure_dark_mode, True, primary_color="#93c5fd")
    assert result["status"] == "blocked_by_guardrail" and "button" in result["reason"]


def test_every_pair_is_audited():
    site = state()
    assert guardrails.audit_contrast(site) == []
    site["theme"]["card_style"]["dark"]["background_color"] = "#e2e8f0"      # light card, light dark-mode text
    site["theme"]["hover_effects"]["slider_arrows"]["background_color"] = "#fde68a"   # white arrows on yellow
    pairs = [pair for pair, _ in guardrails.audit_contrast(site)]
    assert pairs == ["card.dark", "hover.slider_arrows"]


def test_a_stored_problem_does_not_block_unrelated_changes():
    """A snapshot saved before this check may already fail a pair: other changes still go through, and a change
    that fixes the pair is allowed (no dead end), but a new failing pair is not."""
    site = state()
    site["theme"]["card_style"]["light"]["background_color"] = "#334155"   # dark card, dark text: 1.6:1
    db.save_new_state(site, "legacy snapshot with a bad card")
    assert call(tools.update_hover_effect, "service_cards", lift_px=4)["status"] == "success"
    assert call(tools.update_theme_style, primary_color="#14532d")["status"] == "success"
    assert call(tools.update_card_style, "light", background_color="none")["status"] == "success"
    assert guardrails.audit_contrast(state()) == []


# --- Property test: no sequence of accepted tool calls leaves an unreadable pair --------------------------------

COLORS = st.sampled_from(["#ffffff", "#f8fafc", "#f1f5f9", "#e2e8f0", "#94a3b8", "#64748b", "#334155", "#1e293b",
                          "#111827", "#0f172a", "#000000", "#2563eb", "#93c5fd", "#14532d", "#fde68a", "#b91c1c"])
CALLS = st.one_of(
    st.builds(lambda b, t, p: (tools.update_theme_style, {"background_color": b, "text_color": t, "primary_color": p}),
              st.none() | COLORS, st.none() | COLORS, st.none() | COLORS),
    st.builds(lambda e, b, t, p: (tools.configure_dark_mode,
                                  {"enabled": e, "background_color": b, "text_color": t, "primary_color": p}),
              st.booleans(), st.none() | COLORS, st.none() | COLORS, st.none() | COLORS),
    st.builds(lambda m, b: (tools.update_card_style, {"mode": m, "background_color": b}),
              st.sampled_from(["light", "dark", "both"]), st.sampled_from(["none"]) | COLORS),
    st.builds(lambda e, b: (tools.update_hover_effect, {"element": e, "background_color": b}),
              st.sampled_from(["cta_button", "slider_arrows"]), COLORS),
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.lists(CALLS, min_size=4, max_size=8))
def test_no_sequence_of_tool_calls_saves_an_unreadable_site(calls):
    if os.path.exists(config.DB_PATH):          # each example starts from the default site
        os.remove(config.DB_PATH)
    db.init_db()
    for fn, arguments in calls:
        fn(**{k: v for k, v in arguments.items() if v is not None})
        assert guardrails.audit_contrast(state()) == [], (fn.__name__, arguments)
