"""Review T21.A2: the chat history is trimmed, so a long session (a live demo) stays inside the model's context."""
import json

import httpx
import pytest

from concierge import agent


@pytest.fixture()
def client(app_url):
    with httpx.Client(base_url=app_url, timeout=30) as c:
        yield c


def chat(client, message):
    return client.post("/api/chat", data={"message": message}).json()


def request_chars(request):
    return len(json.dumps(request["messages"][1:], ensure_ascii=False))     # without the system prompt


def test_a_long_session_with_state_reads_stays_small(client, fake_lm):
    """Probe: one state read adds 2.4 k characters and every one stayed; after 12 short turns a request was 50 k
    characters (12-16 k tokens) against the 16,384-token context the app loads."""
    for turn in range(30):
        fake_lm.script(("get_current_site_state", {}), reply=f"The primary color is #2563eb ({turn}).")
        chat(client, f"What is the primary color now? ({turn})")
    last = fake_lm.requests[-1]
    assert request_chars(last) < 30_000
    messages = last["messages"][1:]
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    asked = {tc["id"] for m in messages if m["role"] == "assistant" for tc in m.get("tool_calls") or []}
    assert asked == answered                         # every tool call still has its result
    assert messages[0]["role"] == "user"             # cut only before a user message


def test_only_the_newest_state_read_is_kept_in_full():
    def read(call_id, version):
        return [{"role": "assistant", "content": "", "tool_calls": [
                    {"id": call_id, "type": "function",
                     "function": {"name": "get_current_site_state", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": call_id, "content": json.dumps({"version": version, "state": {}})}]
    history = [{"role": "user", "content": "a"}, *read("c1", 1), {"role": "assistant", "content": "ok"},
               {"role": "user", "content": "b"}, *read("c2", 2), {"role": "assistant", "content": "ok"}]
    compacted = agent.compact_history(history)
    tool_results = [m["content"] for m in compacted if m["role"] == "tool"]
    assert '"version": 2' in tool_results[1] and "get_current_site_state" in tool_results[0]
    assert '"version": 1' not in tool_results[0]


def test_trimming_keeps_the_last_turns_and_cuts_before_a_user_message():
    history = []
    for i in range(20):
        history += [{"role": "user", "content": f"u{i}"},
                    {"role": "assistant", "content": "", "tool_calls": [
                        {"id": f"c{i}", "type": "function", "function": {"name": "x", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": f"c{i}", "content": "{}"},
                    {"role": "assistant", "content": f"a{i}"}]
    trimmed = agent.trim_history(history, max_turns=12)
    assert trimmed[0] == {"role": "user", "content": "u8"} and len(trimmed) == 48
