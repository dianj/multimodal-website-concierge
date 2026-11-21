# Architecture and Developer Guide: Multimodal AI Website Maintenance Concierge

This guide explains how the project works: architecture, data model, agent tools, frontend, voice, model management, testing and known limitations. The [README](../README.md) is the short overview. The architecture diagram is in [`multimodal-website-concierge_diagram.svg`](../multimodal-website-concierge_diagram.svg).

---

## 1. Purpose

This is a portfolio project. It simulates a **client-facing AI website maintenance concierge** for a web agency:

* The client opens a split-screen web app: an AI chat on the left and a live website preview on the right.
* The client requests changes in natural language (typed or spoken), with screenshots, content briefs or Skill/brand-guideline files.
* The LLM **never writes HTML/CSS**. It changes a validated JSON site state through 14 tools with guardrails. Every change is a new snapshot in SQLite, with Undo and Redo.
* **Tickets (`CR-XXXX`):** when the client approves the site, the agent creates an approval ticket. When the client asks for something the tools cannot do (e.g. a newsletter form), the agent offers a feature-request ticket and creates it after the client agrees. Developers manage tickets in a panel; the agent can report a ticket's status.

---

## 2. Quick start

1. **LM Studio**
   * The local server runs on **port 1235**, not the LM Studio default of 1234. Settings are under **Local Model API**.
   * The app loads `gemma-4-26b-a4b-it` automatically on startup. The model selector always lists Gemma and Qwen. If `qwen3.8-27b` is not installed in LM Studio, its option is disabled and marked "(not installed)".
2. **Python 3.12** (the interpreter the app was tested with):
   ```powershell
   cd multimodal-website-concierge
   pip install -r requirements.txt
   uvicorn server:app --host 127.0.0.1 --port 8000 --reload
   ```
   If port 8000 is already in use, pick another one, e.g. `--port 8001`.
3. Open `http://localhost:8000` (or `:8001`).
4. **Optional public demo link:** `cloudflared tunnel --url http://localhost:8000`

**Environment variables (all optional):**

| Variable | Default |
|---|---|
| `LLM_BASE_URL` | `http://localhost:1235/v1` |
| `LLM_MODEL` | `gemma-4-26b-a4b-it` |
| `LLM_CONTEXT_LENGTH` | `16384` |
| `WHISPER_MODEL` | `large-v3-turbo` |

**Reset the demo site:** stop the server and delete `website_cms.db`. It is recreated from `DEFAULT_SITE_STATE`.

---

## 3. File inventory

| File | Purpose |
|---|---|
| `server.py` | FastAPI app: HTTP endpoints and in-memory chat sessions. Thin layer over the `concierge` package. |
| `concierge/config.py` | Settings from environment variables, paths and limits |
| `concierge/site_state.py` | `DEFAULT_SITE_STATE`, schema defaults, `upgrade_state()` migration, text normalization |
| `concierge/guardrails.py` | Hex color validation and the contrast checks |
| `concierge/db.py` | SQLite: versioned snapshots, table creation and column migrations |
| `concierge/tools.py` | The 11 site-editing tools, including undo (`rollback_version`) and redo |
| `concierge/tickets.py` | Ticket storage and the 3 ticket tools |
| `concierge/registry.py` | Tool schemas sent to the model and `execute_tool_call` (the dispatcher that never raises) |
| `concierge/agent.py` | System prompt, voice-mode rules and the tool-calling loop (`run_llm_turn`) |
| `concierge/lmstudio.py` | LM Studio model list, load, unload and switch |
| `concierge/voice.py` | faster-whisper STT with EN/BG language pick, text cleanup for speech, edge-tts synthesis with retries |
| `index.html` | Single-page UI in vanilla JS: chat pane and live preview renderer. No build step. |
| `website_cms.db` | SQLite database with `site_versions` and `change_tickets`. Holds demo data; can be deleted to reset. |
| `requirements.txt` | Python dependencies. `av<19` is pinned on purpose (see §12). |
| `requirements-dev.txt` | Test dependencies: pytest, pytest-playwright, httpx |
| `tests/` | Deterministic tests with a scripted fake LM Studio: `test_tools.py` (unit), `test_api.py` (HTTP + agent loop), `test_tickets.py` (ticket tools, migration, API, agent flow), `e2e/test_ui.py` (Playwright) |
| `evals/run_evals.py` | LLM evaluation against the real models in LM Studio; writes `evals/results/latest.md` |
| `.github/workflows/tests.yml` | CI: runs the deterministic tests on every push (Chromium, no GPU) |
| `pytest.ini` | Test config; `live` marker excluded by default |
| `multimodal-website-concierge_diagram.svg` | Architecture and chat-turn flow diagram |
| `docs/screenshots/` | README screenshots from a real session with Gemma 4 26B |
| `README.md` | Portfolio / GitHub description |
| `sample_attachments/brand_guidelines_skill.md` | Example Skill file: forbidden colors, formal Bulgarian tone, auto-ticket on approval |
| `sample_attachments/new_services_brief.txt` | Example content brief: three new services and new working hours |

---

## 4. Architecture

```
Browser (index.html) ──HTTP──► FastAPI (server.py) ──► LM Studio :1235  (chat completions + tools, model load/unload)
   chat pane                     agent loop + tools ──► SQLite website_cms.db (JSON snapshots, tickets)
   live preview  ◄── state JSON ─┘   voice ──────────► faster-whisper (local CPU)  /  Edge TTS (online)
```

**Module dependencies** go one way, so each layer can be tested on its own: `server` → `agent` → `registry` → `tools` / `tickets` → `db` → `site_state` / `config`. `guardrails` is used by `tools`; `lmstudio` and `voice` are used only by `server` (and `lmstudio` by the evals).

**One chat turn** (the same steps as in the diagram):
1. `POST /api/chat` sends the message, an optional file and the `voice_mode` flag. A voice command is first transcribed through `POST /api/transcribe`.
2. The agent loop builds the system prompt (base rules + capabilities + HONESTY/VERIFICATION rules + voice-mode rules when on + active Skills). It sends the prompt, the session history and the 14 tool schemas to the active model.
3. The model returns tool calls or a final reply. Tool calls are executed and their results sent back, up to `MAX_TOOL_ROUNDS = 8` rounds.
4. Each tool validates its input, applies guardrails and saves a full JSON snapshot to `site_versions`.
5. The tool result (`success`, `error` or `blocked_by_guardrail` with a reason) goes back to the model.
6. The response contains `reply`, `tools_used`, `version`, `state` and `app_build`. With voice replies on, the browser requests MP3 from `/api/tts`.
7. The preview re-renders from the JSON state.

**Robustness details:**
* LLM calls run in a worker thread (`run_in_threadpool`), so the server stays responsive.
* Each turn works on a copy of the history, so a failed LLM call never leaves a half-finished tool exchange.
* Base64 screenshots are replaced by a placeholder after the turn, so they are not re-sent every turn.
* Unknown tools, invalid JSON and wrong arguments return errors to the model instead of crashing.

**HTTP endpoints:**

| Endpoint | Purpose |
|---|---|
| `GET /` | The page, with the injected build id |
| `POST /api/chat` | Chat turn: `message`, optional `file`, `attachment_type`, `voice_mode` |
| `GET /api/state` · `POST /api/undo` · `POST /api/redo` | Current site state and version history |
| `GET /api/models` · `POST /api/model` | List the models and switch the loaded LLM |
| `POST /api/transcribe` · `POST /api/tts` | Voice: audio → text, text → MP3 (`gender`, `brief`) |
| `GET /api/tickets` · `GET /api/tickets/{CR-XXXX}` · `POST /api/tickets/{CR-XXXX}/status` | Ticket panel: list (with `open_count`), details, status change |

---

## 5. Site state (JSON schema)

```jsonc
{
  "theme": {
    "primary_color": "#2563eb", "background_color": "#ffffff", "text_color": "#1e293b", "border_radius": "8px",
    "dark_mode": { "enabled": false, "background_color": "#0f172a", "text_color": "#e2e8f0", "primary_color": "#2563eb",
                   "switcher_position": "corner|top_bar|contact", "default_mode": "auto|light|dark" },
    "card_style": { "light": { "border_color": "#7f7f7f", "border_opacity": 0.25, "border_width": "1px", "background_color": null },
                    "dark":  { ...same keys... } },
    "hover_effects": { "cta_button|service_cards|hero_image|slider_arrows": {
        "enabled": false, "lift_px": 0, "scale": 1.0, "shadow": "none|soft|medium|strong", "background_color": null,
        "border_color": null, "border_opacity": 1.0, "tint_opacity": 0.0, "duration_ms": 200 } }
  },
  "section_order": ["hero", "services", "testimonials", "contact"],
  "sections": {
    "hero":         { "title", "subtitle", "cta_text", "image_url" },
    "services":     { "heading", "items": [{ "name", "desc" }] },
    "testimonials": { "heading", "items": [{ "quote", "author" }], "autoplay_seconds": 6 },
    "contact":      { "heading", "email", "phone", "working_hours" }
  }
}
```

* **Schema migration:** `upgrade_state()` runs on every read, including Undo and Redo. It converts old snapshots, such as a single `quote`/`author` instead of `items`, and adds missing defaults (`dark_mode`, `card_style`, `hover_effects`). New schema fields must get defaults there.
* **Paragraphs:** a blank line (`\n\n`) starts a new `<p>`. This applies to `hero.subtitle`, `contact.working_hours`, service descriptions and testimonial quotes. All other text fields are single-line; newlines in them are replaced by spaces.

**Database:**
* `site_versions(version_id, timestamp, change_summary, state_json, status)`. Status values: `0` active, `1` undone (available for Redo), `2` discarded. A new change after Undo sets every undone row to discarded, so the rows are kept for auditing.
* `change_tickets(ticket_id, created_at, updated_at, version_id, type, title, details, status, priority, ticket_markdown)`.
  * `type`: `approval` | `feature_request` | `support`
  * `status`: `open` | `in_progress` | `done` | `rejected`
  * `priority`: `low` | `normal` | `high`
  * `init_db()` adds missing columns to old databases, where tickets were approval-only. Existing rows become open, normal-priority approval tickets.

---

## 6. Agent tools (14)

| Tool | What it does / key validation |
|---|---|
| `get_current_site_state` | Returns the version and full state |
| `update_text_content` | Replaces one **string** field in a section. Max 2000 characters. `contact.email` and `contact.phone` cannot be emptied. Rejects list fields and `hero.image_url`. |
| `update_services_list` | Replaces the full service list: 0–12 items of `{name, desc}` |
| `update_testimonials` | Replaces the full testimonial list (1–10 items of `{quote, author}`) and optionally `autoplay_seconds` (0–30). With 2+ items the section renders as a slider. |
| `update_theme_style` | Changes **several** light-theme properties in one call. Contrast is checked on the final combination. |
| `configure_dark_mode` | Turns dark mode on or off. Also sets the dark palette, `switcher_position` and `default_mode`. The dark palette must pass the contrast checks. |
| `update_card_style` | Sets service-card border color, opacity, width and background per `light`, `dark` or `both` |
| `update_hover_effect` | Sets a whitelisted hover effect per element (see §8) |
| `update_hero_image` | Sets the hero image; must be an `http(s)` URL |
| `reorder_sections` | Must contain every section exactly once |
| `rollback_version` | Non-destructive undo |
| `create_approval_ticket` | Approval ticket: summary + the approved JSON state |
| `create_support_ticket` | `feature_request` or `support` ticket: title, client request, why the agent could not do it, optional partial work, priority. Only after the client agrees. |
| `get_ticket_status` | One ticket by code (`CR-0003`, `3` or `cr-3`), or the 10 most recent tickets |

**Contrast guardrails (all at least 4.5:1):**
* text on background;
* white button text on `primary_color`;
* the same two pairs for the dark palette;
* a custom card background against that mode's text color;
* white text on a hover background.

---

## 7. System prompt rules (`build_system_prompt` in `concierge/agent.py`)

* **Language:** English is primary. The agent replies in Bulgarian when the user writes or speaks Bulgarian. Website content keeps its language unless a translation is requested.
* **Paragraphs:** plain text only, with `\n\n` between paragraphs, never `<p>` or `<br>`.
* **Capabilities:** the prompt describes the slider, dark mode, theme, card and hover tools. Background and text colors must be changed in **one** call.
* **Generated testimonials** must be flagged to the client as placeholder text.
* **HONESTY:** for multi-part requests the agent reports only what the tools actually did and explicitly names what was not possible.
* **TICKETS:** the agent offers a ticket when the tools cannot do something, creates it **only after the client agrees**, gives the `CR-XXXX` number, and uses `get_ticket_status` for status questions.
* **VERIFICATION:** when asked to confirm a change, or when the user says they cannot see it, the agent must call `get_current_site_state` first. It then suggests Ctrl+F5 if the state is correct.
* **Voice mode** (`VOICE_MODE_INSTRUCTIONS`, only when voice replies are on): 2–3 short sentences, no Markdown, hex codes or tool names.
* **Active Skill files** are appended last and must be obeyed.

---

## 8. Frontend (index.html)

**Chat pane:**
* Enter sends and Shift+Enter adds a new line. A loader shows elapsed seconds.
* File upload with three modes: Auto-detect, Load as Skill, Content Brief. A file is treated as a Skill if that mode is chosen or the filename contains `skill`, `guideline` or `handoff`.
* 🎤 voice command button.
* 🔊 Voice replies toggle and Male/Female voice selector.
* A− / A+ chat text size (12–22 px).
* Model selector (Gemma / Qwen).
* Bot replies are rendered as safe Markdown, built with DOM nodes only. Each reply has a "Read aloud" button.
* An **"App updated → Reload" banner** appears when the page's injected `PAGE_BUILD` (the mtime of `index.html`) differs from the server's `app_build`. This catches tabs that still run old JavaScript.

**Ticket panel** (🎫 Tickets button above the preview, with a red badge showing the number of open and in-progress tickets):
* A side panel lists all tickets with their code, type, priority, date and site version.
* Details are rendered as safe Markdown. A status dropdown lets the developer move a ticket through open → in progress → done / rejected.
* The panel refreshes after every chat turn. Esc closes it.
* It is the developer's view in this demo and has **no authentication**.

**Preview:**
* Renders the JSON state with `textContent`.
* Undo / Redo buttons and a version badge.
* Testimonials slider: arrows, dots, autoplay, pause on hover.
* Light / Dark / Auto switcher with radio semantics. Auto follows `prefers-color-scheme` live. The visitor's choice is saved in localStorage.
* Hover CSS is generated into `<style id="siteHoverStyles">` from validated values. The browser re-checks hex colors and number ranges, so CSS cannot be injected.
* Motion is disabled under `prefers-reduced-motion`.

**localStorage keys:** `siteThemePreference`, `voiceReplies`, `voiceGender`, `chatFontSize`.

---

## 9. Voice

* **STT:** `POST /api/transcribe` uses faster-whisper `large-v3-turbo` on the CPU (int8). The model is lazy-loaded on the first request.
  * The language is picked between **EN and BG** by `detect_language` probabilities. Short Bulgarian clips are otherwise sometimes labelled RU, MK or SL.
  * A per-language domain-vocabulary prompt improves recognition of editing terms, plus VAD.
  * Audio is limited to 15 MB.
* **TTS:** `POST /api/tts` uses edge-tts, an **online Microsoft service**, so the reply text leaves the machine.
  * The voice is chosen from the reply script (Cyrillic → BG) and the selected gender:
    * male: Andrew (fallback Brian), Borislav
    * female: Ava (fallback Emma), Kalina
  * 3 attempts with a 15 s timeout each, because the service sometimes returns "No audio was received".
  * `brief=true` (auto-play) trims at a sentence end to about 500 characters. "Read aloud" plays the full text (up to 1500 characters).

---

## 10. Model management

* The hardware fits **one** LLM in VRAM, so the active model is global, not per session.
* `ALLOWED_MODELS` lists the models shown in the UI. The first entry (Gemma) is the default and is ensured on server startup.
* `POST /api/model` unloads the other allowed models through the LM Studio REST API (`/api/v1/models/unload`), then loads the target (`/api/v1/models/load`, context `LLM_CONTEXT_LENGTH`). If that fails, the previous model is restored.
* Chat requests are rejected while a switch is running.
* With `--reload`, every code change restarts the server, which switches LM Studio back to Gemma.

---

## 11. Tests and evals

**Deterministic tests** (52: 21 tool unit, 11 API, 7 ticket, 13 Playwright; about 12 s; no GPU and no LM Studio needed):
```powershell
pip install -r requirements-dev.txt
python -m pytest --browser-channel msedge   # local run on the installed Edge
python -m pytest                            # needs: python -m playwright install chromium
```
* `tests/fake_lmstudio.py` is a small OpenAI-compatible server built on the standard library. Tests queue the model's answers with `fake_lm.script(("tool_name", {...}), reply="...")`. It also fakes the LM Studio model load/unload API and records every request, so tests can check the system prompt, the tools sent and the images sent.
* `tests/conftest.py` sets `WEBSITE_DB_PATH` (a throwaway database) and `LLM_BASE_URL` (the fake) before importing `server`. It runs the real app with uvicorn on a free port and resets the database, sessions and model state before every test.
* The browser tests cover:
  * Enter vs Shift+Enter;
  * Markdown XSS safety;
  * Undo / Redo;
  * the slider;
  * Light / Dark / Auto, including a live OS switch through `emulate_media`;
  * hover CSS on mouse-over;
  * the outdated-page banner;
  * the model selector;
  * voice-reply requests (`/api/tts` is mocked through `page.route`);
  * the ticket panel: badge, details, status change.
* A mutation check was done: disabling `applyHoverStyles` makes the hover test fail.

**LLM evals** (`python evals/run_evals.py`, LM Studio required, about 25 minutes for both models):
* 19 real prompts in English and Bulgarian. Each runs on a fresh site in a throwaway database.
* Each prompt is graded on the outcome (final JSON state, tools used, reply), not on wording. Examples: atomic dark theme, content brief, paragraph duplication, card outline, hover, contrast guardrail, honesty on partial requests, verification, the approval ticket, offering a ticket without creating it before consent, creating it after consent (multi-turn), and looking up a ticket's status.
* Models are loaded one at a time through `switch_model`, and Gemma is loaded again at the end.
* Outcomes are **pass**, **fail** (wrong behaviour) or **error** (LM Studio or infrastructure problem; not counted as a model failure). A case with an error is retried once, after reloading the model.
* **Do not edit `.py` files while evals run** if a dev server with `--reload` is running. Its restart re-loads Gemma, which unloads the model under evaluation.
* **Last run (2026-11-21):** Gemma 4 26B 19/19 (median 13.0 s per turn), Qwen 3.8 27B 19/19 (median 49.6 s). This was a single run with one attempt per case.
* Results go to `evals/results/latest.md`, which is committed. A per-reply JSON file is also written but ignored by git.

## 12. Gotchas

* **LM Studio port is 1235.** A "Connection error" in the chat almost always means LM Studio's server is not running.
* **`av<19`:** faster-whisper 1.2.1 calls `av.open(..., metadata_errors=...)`, which PyAV 19 removed. Tested with `av` 18.1.1 on Windows / Python 3.12. Versions below 15 have no Windows wheels.
* **Stale browser tabs:** after frontend changes, old tabs keep the old JavaScript. New state fields are saved but not rendered. The reload banner covers this; Ctrl+F5 fixes it.
* **Testing from Git Bash:** `curl -F "message=..."` with Cyrillic text is sent in the wrong encoding. Put the text in a UTF-8 file and use `-F "message=<file.txt"`. Python heredocs in Git Bash can also turn `\\n` into real newlines.
* **Editors** may flag `faster_whisper` and `edge_tts` as missing when they point at a different Python interpreter than the one running the server.

---

## 13. Known limitations and open risks

* No authentication or authorization. It is a local demo.
* The site state in SQLite is global. Chat sessions are separate, but all sessions edit the same site.
* Sessions (history, active Skills) are in memory and lost on restart.
* Chat attachments (images, briefs, Skill files) have **no size limit or content validation** yet. Only voice audio is limited, to 15 MB.
* Uploaded text and Skill files go straight into the prompt (prompt-injection surface). Guardrails in the tools limit the damage, because the model can only change validated fields.
* The ticket panel has no authentication and sends no notifications; anyone with access to the app can change a ticket's status.
* The contrast check supports 6-digit hex only.
* For public deployment the app still needs HTTPS, authentication, rate limits, security headers, upload limits and persistent per-user storage.

## 14. Ideas for next steps

* Ticket notifications (email or a Slack/Discord webhook) and a protected developer view.
* Upload size and type limits for chat attachments.
* Per-client site ownership and an approval workflow.
* An EN/BG UI language toggle.
