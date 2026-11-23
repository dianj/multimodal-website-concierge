"""Voice helpers with fake Whisper and edge-tts objects: no model download, no network."""
import asyncio
from asyncio import sleep as real_sleep  # kept before the backoff is patched out
from concurrent.futures import ThreadPoolExecutor

import edge_tts
import faster_whisper
import pytest

from concierge import config, voice


class FakeWhisper:
    def __init__(self, probs=None, fail_detection=False, text=" Change the colour "):
        self.probs, self.fail_detection, self.text = probs or {}, fail_detection, text
        self.transcribe_kwargs = None

    def detect_language(self, audio, **kwargs):
        if self.fail_detection:
            raise RuntimeError("no speech")
        return "xx", 1.0, list(self.probs.items())

    def transcribe(self, audio, language, **kwargs):
        self.transcribe_kwargs = {"language": language, **kwargs}
        return [type("Segment", (), {"text": part})() for part in self.text.split("|")], None


def test_language_detection_only_compares_supported_languages():
    # Short Bulgarian clips often come back as Russian; only English and Bulgarian are compared.
    model = FakeWhisper({"ru": 0.6, "bg": 0.3, "en": 0.1})
    assert voice.pick_supported_language(model, "AUDIO") == "bg"
    assert voice.pick_supported_language(FakeWhisper({"en": 0.2, "de": 0.7}), "AUDIO") == "en"


def test_language_detection_failure_falls_back_to_the_primary_language():
    assert voice.pick_supported_language(FakeWhisper(fail_detection=True), "AUDIO") == config.PRIMARY_LANGUAGE


def test_transcription_uses_the_detected_language_and_its_vocabulary(monkeypatch):
    model = FakeWhisper({"bg": 0.9, "en": 0.1}, text=" Смени | цвета ")
    monkeypatch.setattr(voice, "get_whisper_model", lambda: model)
    monkeypatch.setattr(faster_whisper, "decode_audio", lambda path: "AUDIO")
    assert voice.transcribe_audio("clip.webm") == ("Смени цвета", "bg")
    assert model.transcribe_kwargs["language"] == "bg"
    assert model.transcribe_kwargs["initial_prompt"] == config.WHISPER_PROMPTS["bg"]


def test_whisper_is_loaded_once_and_only_when_first_needed(monkeypatch):
    created = []
    monkeypatch.setattr(faster_whisper, "WhisperModel", lambda *args, **kwargs: created.append(kwargs) or object())
    monkeypatch.setattr(voice, "_whisper_model", None)
    first = voice.get_whisper_model()
    assert voice.get_whisper_model() is first
    assert created == [{"device": "cpu", "compute_type": "int8"}]  # CPU: the GPU is reserved for the LLM


def test_markdown_links_code_and_urls_are_not_read_aloud():
    text = "## Done\n- **Primary** colour is now green, see [the guide](https://x.io).\n```css\na{}\n```\nhttps://x.io"
    assert voice.clean_for_speech(text) == "Done Primary colour is now green, see the guide."


# --- text-to-speech ---------------------------------------------------------------------

def fake_communicate(plan, voices_used):
    """edge_tts.Communicate stand-in: each attempt takes the next behaviour from `plan`."""
    class Communicate:
        def __init__(self, text, voice):
            voices_used.append(voice)
            self.behaviour = plan.pop(0)

        async def stream(self):
            if self.behaviour == "error":
                raise ConnectionError("No audio was received")
            if self.behaviour == "hang":
                await real_sleep(5)
            if self.behaviour == "audio":
                yield {"type": "WordBoundary"}
                yield {"type": "audio", "data": b"MP3"}
    return Communicate


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(voice.asyncio, "sleep", lambda seconds: real_sleep(min(seconds, 0.01)))


def run_async(coroutine):
    """asyncio.run in its own thread: the Playwright tests keep an event loop running in the main thread."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


def speak(monkeypatch, plan, text="Done, the colour is green."):
    voices_used = []
    monkeypatch.setattr(edge_tts, "Communicate", fake_communicate(list(plan), voices_used))
    return run_async(voice.synthesize_speech(text, "female")), voices_used


def test_tts_returns_audio_with_the_voice_for_the_reply_language(monkeypatch, no_backoff):
    (audio, error), voices_used = speak(monkeypatch, ["audio"], "Готово, цветът е зелен.")
    assert (audio, error) == (b"MP3", None)
    assert voices_used == [config.TTS_VOICES["female"]["bg"][0]]


def test_tts_retries_and_uses_the_fallback_voice_last(monkeypatch, no_backoff):
    (audio, error), voices_used = speak(monkeypatch, ["error", "empty", "audio"])
    primary, fallback = config.TTS_VOICES["female"]["en"]
    assert audio == b"MP3" and error is None
    assert voices_used == [primary, primary, fallback]


def test_tts_gives_up_with_a_reason_after_all_attempts(monkeypatch, no_backoff):
    monkeypatch.setattr(voice, "TTS_ATTEMPT_TIMEOUT", 0.05)
    (audio, error), _ = speak(monkeypatch, ["error", "empty", "hang"])
    assert audio is None
    assert error == "Text-to-speech failed after 3 attempts: timeout after 0.05s"
