"""The agent: the tool-calling loop and chat-history helpers. The system prompt is in prompts.py."""
from openai import OpenAI

from .config import LLM_BASE_URL, LLM_TIMEOUT_SECONDS, MAX_TOOL_ROUNDS
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

def run_llm_turn(active_model: str, active_skills: list, turn_history: list, tools_used: list,
                 voice_mode: bool = False):
    """Call the model, executing tool calls until it answers (or MAX_TOOL_ROUNDS is hit).

    Mutates turn_history and tools_used; returns the final model message.
    """
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
            turn_history.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": execute_tool_call(tc.function.name, tc.function.arguments)
            })
    return msg

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
