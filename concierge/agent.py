"""The agent: the tool-calling loop and chat-history helpers. The system prompt is in prompts.py."""
import json

from openai import OpenAI

from . import config, decisions
from .config import LLM_BASE_URL, LLM_TIMEOUT_SECONDS, MAX_TOOL_ROUNDS
from .decide import Action
from .prompts import build_system_prompt
from .registry import execute_tool_call
from .tool_schemas import TOOLS_SCHEMA

llm_client = OpenAI(base_url=LLM_BASE_URL, api_key="lm-studio", timeout=LLM_TIMEOUT_SECONDS)

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

SIGN_OFF_UNCLEAR = json.dumps({
    "status": "error",
    "message": "No approval ticket was created: the client's message is not a clear sign-off of the whole site. Ask "
               "them explicitly whether the current website is approved as finished.",
})
SIGN_OFF_NOT_GIVEN = json.dumps({
    "status": "error",
    "message": "No approval ticket was created: the client did not approve the site. Continue with their request.",
})


def sign_off_gate(client_text: str, model: str, decisions_log: list | None = None, previous: str = "",
                  evidence: dict | None = None) -> str | None:
    """The second key for create_approval_ticket: None = create it; otherwise the result to return instead.
    `previous`: the assistant's last reply, so a "Yes" is judged against the question it answers. `evidence` is
    filled with the client's words and the decision, for the approval ticket (review T21.A5)."""
    if evidence is not None:
        evidence.update(client_message=client_text, previous=previous, model=model)
    if not config.DECISION_GATE:
        if evidence is not None:
            evidence["gate"] = "off"
        return None
    agree, instruction = decisions.decider(model).ask_many_sync([decisions.APPROVE_SITE, decisions.INSTRUCTION],
                                                                client_text, {"previous": previous})
    action = decisions.approval_action(agree, instruction)
    if decisions_log is not None:
        decisions_log += [{**d.record.model_dump(mode="json"), "policy_action": action.value}
                          for d in (agree, instruction)]
    if evidence is not None:
        evidence.update(p_approve=agree.probs.get("yes"), p_flag=instruction.probs.get("yes"), action=action.value,
                        reason=agree.reason,
                        calibration_sha256=decisions.calibration_sha256(model, decisions.APPROVE_SITE.id))
    if action == Action.ACT:
        return None
    return SIGN_OFF_UNCLEAR if action == Action.CONFIRM else SIGN_OFF_NOT_GIVEN


def run_llm_turn(active_model: str, active_skills: list, turn_history: list, tools_used: list,
                 voice_mode: bool = False, decisions_log: list | None = None):
    """Call the model, executing tool calls until it answers (or MAX_TOOL_ROUNDS is hit).

    Mutates turn_history and tools_used; returns the final model message. The approval ticket (the client's sign-off)
    is created only if the sign-off gate (decisions.py) agrees that the client's latest message approves the site.
    """
    last_index = max((i for i, m in enumerate(turn_history) if m.get("role") == "user"), default=None)
    last_user = turn_history[last_index] if last_index is not None else {}
    client_text = decisions.message_text(last_user)
    previous = decisions.previous_assistant_message(turn_history[:last_index or 0])
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
            refusal, trusted = None, None
            if tc.function.name == "create_approval_ticket":
                evidence = {}
                refusal = sign_off_gate(client_text, active_model, decisions_log, previous, evidence)
                trusted = {"evidence": evidence}
            turn_history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": refusal or execute_tool_call(tc.function.name, tc.function.arguments, trusted)
            })
    return msg

STATE_READ_STUB = json.dumps({
    "status": "success",
    "note": "An older copy of the site state was here. Call get_current_site_state again for the current one.",
})


def compact_history(history: list) -> list:
    """Replace every get_current_site_state result except the newest with a one-line stub: each full state is
    about 2.4 k characters and only the latest one is still true (review T21.A2)."""
    reads = {tc["id"] for m in history if m.get("role") == "assistant" for tc in m.get("tool_calls") or []
             if tc["function"]["name"] == "get_current_site_state"}
    results = [i for i, m in enumerate(history) if m.get("role") == "tool" and m.get("tool_call_id") in reads]
    return [{**m, "content": STATE_READ_STUB} if i in results[:-1] else m for i, m in enumerate(history)]


def trim_history(history: list, max_turns: int = config.MAX_HISTORY_TURNS) -> list:
    """Keep the last `max_turns` user turns. Cuts only before a user message, so tool-call pairs stay complete."""
    user_indexes = [i for i, m in enumerate(history) if m.get("role") == "user"]
    if len(user_indexes) <= max_turns:
        return history
    return history[user_indexes[-max_turns]:]


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
