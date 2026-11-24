"""Settings read from the environment, plus fixed limits. Imported by every other module."""
import os

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:1235/v1")
LMSTUDIO_ROOT = LLM_BASE_URL.rstrip("/").removesuffix("/v1")
# Models offered in the UI as (LM Studio model key, display label). The first one is the default.
ALLOWED_MODELS = [
    ("gemma-4-26b-a4b-it", "Gemma 4 26B"),
    ("qwen3.8-27b", "Qwen 3.8 27B"),
]
MODEL_NAME = os.environ.get("LLM_MODEL", ALLOWED_MODELS[0][0])
LOAD_CONTEXT_LENGTH = int(os.environ.get("LLM_CONTEXT_LENGTH", "16384"))
MODEL_LOAD_TIMEOUT = 600
# Seconds to wait for one model answer; a slower answer ends the turn with a friendly message.
LLM_TIMEOUT_SECONDS = float(os.environ.get("LLM_TIMEOUT_SECONDS", "120"))

# --- Sign-off gate (docs/DECISIONS.md): the approval ticket needs a calibrated, clear approval ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISION_GATE = os.environ.get("DECISION_GATE", "1") == "1"
CALIBRATION_DIR = os.path.join(BASE_DIR, os.environ.get("CALIBRATION_DIR", "evals/decisions/calibration"))
DECISION_TIMEOUT_SECONDS = float(os.environ.get("DECISION_TIMEOUT_SECONDS", "30"))

# --- VOICE ---
# Speech-to-text runs locally with faster-whisper on the CPU (the GPU is reserved for the LLM).
# Text-to-speech uses edge-tts (Microsoft Edge online neural voices), same stack as the other voice projects.
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
# Voice per gender and language: [primary, fallback]. The fallback keeps the same gender
# (Bulgarian has only one voice per gender, so it is simply retried).
TTS_VOICES = {
    "male": {"en": ["en-US-AndrewNeural", "en-US-BrianNeural"], "bg": ["bg-BG-BorislavNeural", "bg-BG-BorislavNeural"]},
    "female": {"en": ["en-US-AvaNeural", "en-US-EmmaNeural"], "bg": ["bg-BG-KalinaNeural", "bg-BG-KalinaNeural"]},
}
DEFAULT_VOICE_GENDER = "male"
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MAX_TTS_CHARS = 1500
TTS_ATTEMPT_TIMEOUT = 15
MAX_BRIEF_TTS_CHARS = 500
# English is the primary language; Bulgarian is fully supported.
PRIMARY_LANGUAGE = "en"
SUPPORTED_LANGUAGES = ("en", "bg")
# Domain vocabulary hints for Whisper (per language): improve recognition of website-editing terms.
WHISPER_PROMPTS = {
    "en": (
        "Website editing commands: change the color, primary color, background, text, heading, subtitle, "
        "paragraph, section, services, testimonials, slider, dark mode, contact, working hours, button, image, "
        "reorder, undo, approve."
    ),
    "bg": (
        "Команди за редакция на уебсайт: смени цвета, основен цвят, фон, текст, заглавие, подзаглавие, "
        "параграф, секция, услуги, отзиви, слайдер, тъмен режим, контакти, работно време, бутон, снимка, "
        "подредба, отмени, одобри."
    ),
}

# The project root (one level above this package): index.html and the default database live there.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# WEBSITE_DB_PATH lets tests and evals use a throwaway database.
DB_PATH = os.environ.get("WEBSITE_DB_PATH", os.path.join(BASE_DIR, "website_cms.db"))
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
MAX_TOOL_ROUNDS = 8
