"""Shared fixtures: a fake LM Studio, a throwaway SQLite database and the real app on a free port.

Environment variables must be set before `server` is imported, because it reads them at import time.
"""
import os
import socket
import sys
import tempfile
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fake_lmstudio import FakeLMStudio  # noqa: E402

_TMP_DIR = tempfile.mkdtemp(prefix="concierge-tests-")
_FAKE = FakeLMStudio().start()
os.environ["WEBSITE_DB_PATH"] = os.path.join(_TMP_DIR, "test_cms.db")
os.environ["LLM_BASE_URL"] = _FAKE.base_url

import server  # noqa: E402  (must come after the environment is prepared)


@pytest.fixture(scope="session")
def fake_lm():
    yield _FAKE
    _FAKE.stop()


@pytest.fixture(scope="session")
def srv():
    """The imported server module (tools, guardrails, helpers)."""
    return server


@pytest.fixture(autouse=True)
def clean_state(fake_lm):
    """Every test starts from the default site, empty sessions, Gemma active and an empty script."""
    fake_lm.reset()
    fake_lm.loaded = {"gemma-4-26b-a4b-it"}
    if os.path.exists(server.DB_PATH):
        os.remove(server.DB_PATH)
    server.init_db()
    server.sessions.clear()
    server.model_state.update(active=server.MODEL_NAME, switching=False, target=None, error=None)
    yield


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def app_url(fake_lm):
    """Run the real FastAPI app (uvicorn) in a background thread for API and browser tests."""
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not uv.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    uv.should_exit = True
    thread.join(timeout=5)
