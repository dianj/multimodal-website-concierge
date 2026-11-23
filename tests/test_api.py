"""API tests: the real FastAPI app and agent loop over HTTP, with a scripted fake LLM."""
import httpx
import pytest

from concierge import agent, config


@pytest.fixture()
def client(app_url):
    with httpx.Client(base_url=app_url, timeout=30) as c:
        yield c


def chat(client, message, **form):
    return client.post("/api/chat", data={"message": message, **form}).json()


def test_chat_turn_executes_tool_calls_and_returns_new_state(client, fake_lm):
    fake_lm.script(("update_text_content", {"section_id": "hero", "field": "title", "new_value": "Hello"}),
                   reply="Title updated.")
    data = chat(client, "Change the title to Hello")
    assert data["reply"] == "Title updated."
    assert data["tools_used"][0].startswith("update_text_content")
    assert data["state"]["sections"]["hero"]["title"] == "Hello"
    assert data["version"] == 2


def test_model_receives_all_tools_and_capability_rules(client, fake_lm):
    fake_lm.script(reply="Hi")
    chat(client, "hello")
    request = fake_lm.requests[-1]
    assert len(request["tools"]) == 14
    prompt = fake_lm.last_system_prompt()
    assert "HONESTY" in prompt and "VERIFICATION" in prompt and "VOICE MODE" not in prompt


def test_voice_mode_adds_brevity_rules(client, fake_lm):
    fake_lm.script(reply="Short.")
    chat(client, "hello", voice_mode="true")
    assert "VOICE MODE IS ON" in fake_lm.last_system_prompt()


def test_guardrail_result_is_passed_back_to_the_model(client, fake_lm):
    fake_lm.script(("update_theme_style", {"background_color": "#000000"}), reply="Blocked, sorry.")
    data = chat(client, "make the background black")
    tool_message = fake_lm.requests[-1]["messages"][-1]
    assert tool_message["role"] == "tool" and "blocked_by_guardrail" in tool_message["content"]
    assert data["state"]["theme"]["background_color"] == "#ffffff"


def test_skill_file_is_injected_into_the_system_prompt(client, fake_lm):
    fake_lm.script(reply="Rules noted.")
    files = {"file": ("brand_guidelines_skill.md", b"Never use red.", "text/markdown")}
    data = client.post("/api/chat", data={"message": ""}, files=files).json()
    assert data["skill_loaded"] == "brand_guidelines_skill.md"
    assert "Never use red." in fake_lm.last_system_prompt()


def test_screenshot_is_sent_once_then_replaced_in_history(client, fake_lm):
    fake_lm.script(reply="Nice screenshot.")
    fake_lm.script(reply="Second turn.")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    client.post("/api/chat", data={"message": "look"}, files={"file": ("shot.png", png, "image/png")})
    assert "data:image/png;base64" in str(fake_lm.requests[0]["messages"])
    chat(client, "again")
    assert "data:image/png;base64" not in str(fake_lm.requests[1]["messages"])


def test_llm_outage_does_not_corrupt_the_session(client, fake_lm, srv):
    fake_lm.fail_count = agent.llm_client.max_retries + 1  # fail the first try and every automatic retry
    data = chat(client, "first")
    assert "Error" in data["reply"]
    fake_lm.script(reply="Back online.")
    assert chat(client, "second")["reply"] == "Back online."
    history = next(iter(srv.sessions.values()))["chat_history"]
    assert [m["role"] for m in history] == ["user", "assistant"]  # failed turn left nothing behind


def test_llm_timeout_ends_the_turn_cleanly(client, fake_lm, srv, monkeypatch):
    assert agent.llm_client.timeout == config.LLM_TIMEOUT_SECONDS  # the setting reaches the client
    monkeypatch.setattr(agent, "llm_client", agent.llm_client.with_options(timeout=0.3, max_retries=0))
    fake_lm.delay_s = 2
    data = chat(client, "first")
    assert "did not answer within 0.3 seconds" in data["reply"]
    assert "Cannot reach LM Studio" not in data["reply"]
    fake_lm.delay_s = 0
    fake_lm.script(reply="Back online.")
    assert chat(client, "second")["reply"] == "Back online."
    history = next(iter(srv.sessions.values()))["chat_history"]
    assert [m["role"] for m in history] == ["user", "assistant"]  # the timed-out turn left nothing behind


def test_tool_loop_is_capped(client, fake_lm):
    fake_lm.script_tool_loop("get_current_site_state", {}, rounds=config.MAX_TOOL_ROUNDS + 1)
    data = chat(client, "loop forever")
    assert "too many consecutive tool calls" in data["reply"]
    assert len(data["tools_used"]) == config.MAX_TOOL_ROUNDS + 1


def test_undo_and_redo_endpoints(client, fake_lm):
    fake_lm.script(("update_text_content", {"section_id": "hero", "field": "title", "new_value": "V2"}))
    chat(client, "change")
    assert client.post("/api/undo").json()["state"]["sections"]["hero"]["title"] != "V2"
    assert client.post("/api/redo").json()["state"]["sections"]["hero"]["title"] == "V2"


def test_model_switch_unloads_current_model_first(client, fake_lm):
    result = client.post("/api/model", data={"model": "qwen3.8-27b"}).json()
    assert result == {"status": "success", "active": "qwen3.8-27b"}
    assert fake_lm.loaded == {"qwen3.8-27b"}  # only one model in memory
    assert client.post("/api/model", data={"model": "not-allowed"}).status_code == 400


def test_page_carries_the_build_id(client, srv):
    html = client.get("/").text
    assert f"PAGE_BUILD = '{srv.app_build()}'" in html
    assert client.get("/api/state").json()["app_build"] == srv.app_build()
