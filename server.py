"""FastAPI app: HTTP endpoints for chat, site state, undo/redo, models, tickets and voice.

The logic lives in the `concierge` package; this module wires it to HTTP. Run with: uvicorn server:app
"""
import base64
import json
import os
import tempfile
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from openai import APIConnectionError, APITimeoutError

from concierge import agent
from concierge.agent import run_llm_turn, strip_images_from_history
from concierge.config import (ALLOWED_MODELS, DEFAULT_VOICE_GENDER, INDEX_PATH, LLM_BASE_URL, MAX_AUDIO_BYTES,
                              MAX_BRIEF_TTS_CHARS, MAX_TTS_CHARS)
from concierge.db import get_latest_state, init_db
from concierge.lmstudio import ensure_default_model_on_startup, list_available_models, model_state, switch_model
from concierge.tickets import TICKET_STATUSES, get_ticket, list_tickets, parse_ticket_id, set_ticket_status
from concierge.tools import redo_version, rollback_version
from concierge.voice import clean_for_speech, synthesize_speech, transcribe_audio, trim_to_sentences

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Load the default model in the background so the web UI is available while LM Studio loads it.
    threading.Thread(target=ensure_default_model_on_startup, daemon=True).start()
    yield

app = FastAPI(title="Multimodal AI Website Maintenance Concierge", lifespan=lifespan)

# Chat sessions (history and active Skills) are kept in memory, keyed by a cookie.
sessions = {}

def get_session(request: Request):
    session_id = request.cookies.get("concierge_session")
    if not session_id or session_id not in sessions:
        session_id = str(uuid.uuid4())
        sessions[session_id] = {"active_skills": [], "chat_history": []}
    return session_id, sessions[session_id]

def set_session_cookie(response: JSONResponse, session_id: str):
    response.set_cookie(
        key="concierge_session",
        value=session_id,
        httponly=True,
        samesite="lax"
    )
    return response

init_db()

@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...)):
    raw = await audio.read()
    if not raw:
        return JSONResponse({"status": "error", "message": "Empty audio."}, status_code=400)
    if len(raw) > MAX_AUDIO_BYTES:
        return JSONResponse({"status": "error", "message": "Audio is too long."}, status_code=413)
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(raw)
        tmp.close()
        text, language = await run_in_threadpool(transcribe_audio, tmp.name)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Speech recognition failed: {e}"}, status_code=500)
    finally:
        os.remove(tmp.name)
    return JSONResponse({"status": "success", "text": text, "language": language})

@app.post("/api/tts")
async def api_tts(text: str = Form(...), brief: bool = Form(False), gender: str = Form(DEFAULT_VOICE_GENDER)):
    spoken = clean_for_speech(text)
    # Auto-played replies are capped at a sentence boundary; the manual "Read aloud" button reads everything.
    spoken = trim_to_sentences(spoken, MAX_BRIEF_TTS_CHARS) if brief else spoken[:MAX_TTS_CHARS]
    if not spoken:
        return JSONResponse({"status": "error", "message": "Nothing to speak."}, status_code=400)
    audio, error = await synthesize_speech(spoken, gender)
    if audio:
        return Response(content=audio, media_type="audio/mpeg")
    return JSONResponse({"status": "error", "message": error}, status_code=502)

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        # The page remembers which build it was served from, so it can detect when it is outdated.
        return f.read().replace("__APP_BUILD__", app_build())

def app_build() -> str:
    """Identifier of the current frontend version (changes whenever index.html is edited)."""
    return str(int(os.path.getmtime(INDEX_PATH)))

@app.get("/api/state")
async def api_state(request: Request):
    session_id, session = get_session(request)
    ver_id, state = get_latest_state()
    response = JSONResponse({"version": ver_id, "state": state, "active_skills_count": len(session["active_skills"]), "app_build": app_build()})
    return set_session_cookie(response, session_id)

@app.post("/api/undo")
async def api_undo():
    res = json.loads(rollback_version())
    ver_id, state = get_latest_state()
    return JSONResponse({"result": res, "version": ver_id, "state": state, "app_build": app_build()})

@app.get("/api/models")
def api_models():
    return JSONResponse({
        "models": list_available_models(),
        "active": model_state["active"],
        "switching": model_state["switching"],
        "target": model_state["target"],
    })

@app.post("/api/model")
def api_select_model(model: str = Form(...)):
    # Sync endpoint: FastAPI runs it in a worker thread, so the long LM Studio load does not block the server.
    if model not in {model_id for model_id, _ in ALLOWED_MODELS}:
        return JSONResponse({"status": "error", "message": f"Unknown model '{model}'."}, status_code=400)
    if model_state["switching"]:
        return JSONResponse({"status": "error", "message": "Another model switch is already in progress."}, status_code=409)
    result = switch_model(model)
    return JSONResponse(result, status_code=200 if result["status"] == "success" else 500)

# --- Ticket panel (developer view in the demo) ---
@app.get("/api/tickets")
def api_tickets():
    tickets = list_tickets()
    open_count = sum(t["status"] in ("open", "in_progress") for t in tickets)
    return JSONResponse({"tickets": tickets, "open_count": open_count})

@app.get("/api/tickets/{ticket_ref}")
def api_ticket(ticket_ref: str):
    try:
        ticket = get_ticket(parse_ticket_id(ticket_ref))
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    if not ticket:
        return JSONResponse({"status": "error", "message": "Ticket not found."}, status_code=404)
    return JSONResponse(ticket)

@app.post("/api/tickets/{ticket_ref}/status")
def api_ticket_status(ticket_ref: str, status: str = Form(...)):
    if status not in TICKET_STATUSES:
        return JSONResponse({"status": "error", "message": f"status must be one of {list(TICKET_STATUSES)}."}, status_code=400)
    try:
        ticket_id = parse_ticket_id(ticket_ref)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    if not set_ticket_status(ticket_id, status):
        return JSONResponse({"status": "error", "message": "Ticket not found."}, status_code=404)
    return JSONResponse({"status": "success", "ticket": get_ticket(ticket_id)})

@app.post("/api/redo")
async def api_redo():
    res = json.loads(redo_version())
    ver_id, state = get_latest_state()
    return JSONResponse({"result": res, "version": ver_id, "state": state, "app_build": app_build()})

@app.post("/api/chat")
async def api_chat(
    request: Request,
    message: str = Form(""),
    attachment_type: str = Form("auto"),
    voice_mode: bool = Form(False),
    file: UploadFile = File(None)
):
    session_id, session = get_session(request)
    if model_state["switching"]:
        response = JSONResponse({
            "reply": f"The AI model is being switched to '{model_state['target']}'. Please wait until it is loaded and try again.",
            "tools_used": []
        })
        return set_session_cookie(response, session_id)
    active_model = model_state["active"]
    active_skills = session["active_skills"]
    chat_history = session["chat_history"]
    tools_used = []
    skill_loaded = None
    user_content_blocks = []

    if message.strip():
        user_content_blocks.append({"type": "text", "text": message.strip()})

    if file and file.filename:
        raw_bytes = await file.read()
        fname = file.filename.lower()

        if fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
            if fname.endswith(".png"):
                mime = "image/png"
            elif fname.endswith(".webp"):
                mime = "image/webp"
            else:
                mime = "image/jpeg"
            b64 = base64.b64encode(raw_bytes).decode("utf-8")
            user_content_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
            if not message.strip():
                user_content_blocks.insert(0, {"type": "text", "text": f"Analyze this attached screenshot ({file.filename}) and apply relevant website updates or suggest improvements."})
        else:
            text_content = raw_bytes.decode("utf-8", errors="ignore")
            is_skill = (attachment_type == "skill") or ("skill" in fname) or ("guideline" in fname) or ("handoff" in fname)
            if is_skill:
                active_skills.append(f"[Skill File: {file.filename}]\n{text_content}")
                skill_loaded = file.filename
                user_content_blocks.append({
                    "type": "text",
                    "text": f"[System Note: The client attached a Skill/Guideline file '{file.filename}'. It is now active in your system instructions. Acknowledge the rules and apply any immediate instructions.]"
                })
            else:
                user_content_blocks.append({
                    "type": "text",
                    "text": f"[Attached Document: {file.filename}]\n{text_content}"
                })

    if not user_content_blocks:
        response = JSONResponse({"reply": "Please enter a message or attach a file/screenshot.", "tools_used": []})
        return set_session_cookie(response, session_id)

    # Work on a copy so a failed turn never leaves a half-finished tool exchange in the session.
    turn_history = chat_history + [{"role": "user", "content": user_content_blocks}]

    try:
        # The OpenAI client is blocking; run it in a worker thread so the server stays responsive.
        msg = await run_in_threadpool(run_llm_turn, active_model, active_skills, turn_history, tools_used, voice_mode)

        if msg.tool_calls:
            bot_reply = "Stopped after too many consecutive tool calls. Please check the preview and rephrase your request if needed."
        else:
            bot_reply = msg.content or "Changes applied."
        turn_history.append({"role": "assistant", "content": bot_reply})
        strip_images_from_history(turn_history)
        chat_history[:] = turn_history

    except APITimeoutError:  # a subclass of APIConnectionError, so it must come first
        bot_reply = (
            f"The model '{active_model}' did not answer within {agent.llm_client.timeout:g} seconds. "
            "Nothing was changed; please try again, or try a shorter request."
        )
    except APIConnectionError:
        bot_reply = (
            f"Cannot reach LM Studio at {LLM_BASE_URL}. Make sure LM Studio is running, "
            f"the model '{active_model}' is loaded, and the local server is started (Developer tab -> Start Server)."
        )
    except Exception as e:
        bot_reply = f"Error communicating with local LM Studio model: {str(e)}"
        if skill_loaded:
            bot_reply += f"\n(The Skill file '{skill_loaded}' is still active.)"

    ver_id, state = get_latest_state()
    response = JSONResponse({
        "reply": bot_reply,
        "tools_used": tools_used,
        "skill_loaded": skill_loaded,
        "model": active_model,
        "app_build": app_build(),
        "version": ver_id,
        "state": state
    })
    return set_session_cookie(response, session_id)
