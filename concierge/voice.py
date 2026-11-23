"""Voice: local speech-to-text (faster-whisper) and text-to-speech helpers (edge-tts, online)."""
import asyncio
import re
import threading

from .config import (
    DEFAULT_VOICE_GENDER,
    PRIMARY_LANGUAGE,
    SUPPORTED_LANGUAGES,
    TTS_ATTEMPT_TIMEOUT,
    TTS_VOICES,
    WHISPER_MODEL_SIZE,
    WHISPER_PROMPTS,
)

_whisper_model = None
_whisper_lock = threading.Lock()

def get_whisper_model():
    """Load Whisper lazily on the first voice request so server start/reload stays fast."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        return _whisper_model

def pick_supported_language(model, audio) -> str:
    """Detect the spoken language, restricted to the supported ones (English or Bulgarian).

    Whisper sometimes labels short Bulgarian clips as Russian/Macedonian/Slovenian, so instead of
    trusting the top label we compare only the probabilities of the supported languages.
    """
    try:
        _, _, all_probs = model.detect_language(audio, vad_filter=True)
        probs = dict(all_probs)
        return max(SUPPORTED_LANGUAGES, key=lambda lang: probs.get(lang, 0.0))
    except Exception:
        return PRIMARY_LANGUAGE

def transcribe_audio(path: str):
    from faster_whisper import decode_audio
    model = get_whisper_model()
    audio = decode_audio(path)
    language = pick_supported_language(model, audio)
    segments, _ = model.transcribe(
        audio, language=language, vad_filter=True, beam_size=5, initial_prompt=WHISPER_PROMPTS[language]
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return text, language

def clean_for_speech(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)           # drop code blocks
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)          # [label](url) -> label
    text = re.sub(r"^\s{0,3}(#{1,6}|>|[-*•])\s+", "", text, flags=re.M)
    text = re.sub(r"[*`_#]", "", text)
    text = re.sub(r"https?://\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()

def trim_to_sentences(text: str, limit: int) -> str:
    """Cut text to at most `limit` characters, ending on a sentence boundary when possible."""
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    # Sentence end = . ! ? followed by whitespace/end, but not a list number like "2." or a decimal.
    ends = [m.end() for m in re.finditer(r"(?<!\d)[.!?…](?=\s|$)", cut)]
    if ends and ends[-1] >= limit * 0.4:
        return cut[:ends[-1]].strip()
    cut = cut[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut) + "..."

def detect_reply_language(text: str) -> str:
    cyrillic = len(re.findall(r"[Ѐ-ӿ]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return "bg" if cyrillic and cyrillic >= latin * 0.3 else PRIMARY_LANGUAGE

async def synthesize_speech(spoken: str, gender: str):
    """Text -> MP3 bytes with edge-tts. Returns (audio, None) or (None, error description)."""
    import edge_tts
    lang = detect_reply_language(spoken)
    # The online edge-tts service intermittently answers with "No audio was received";
    # retry with a short backoff and use a fallback voice on the last attempt.
    primary, fallback = TTS_VOICES.get(gender, TTS_VOICES[DEFAULT_VOICE_GENDER])[lang]
    voices = [primary, primary, fallback]
    async def synthesize(voice: str) -> bytes:
        audio = bytearray()
        async for chunk in edge_tts.Communicate(spoken, voice).stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio)

    last_error = None
    for attempt, voice in enumerate(voices):
        try:
            # Cap each attempt so a hanging connection does not keep the user waiting.
            audio = await asyncio.wait_for(synthesize(voice), timeout=TTS_ATTEMPT_TIMEOUT)
            if audio:
                return audio, None
            last_error = "empty audio"
        except TimeoutError:
            last_error = f"timeout after {TTS_ATTEMPT_TIMEOUT}s"
        except Exception as e:
            last_error = e
        await asyncio.sleep(0.6 * (attempt + 1))
    return None, f"Text-to-speech failed after {len(voices)} attempts: {last_error}"
