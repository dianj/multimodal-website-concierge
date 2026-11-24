"""Shared fixtures: a fake LM Studio, a throwaway SQLite database and the real app on a free port.

Environment variables must be set before `server` (and the `concierge` package) is imported, because they are
read at import time.
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
os.environ["DECISION_GATE"] = "0"  # the sign-off gate has its own tests (tests/test_sign_off_gate.py)

import server  # noqa: E402  (must come after the environment is prepared)
from concierge import config, db, lmstudio  # noqa: E402


@pytest.fixture(scope="session")
def fake_lm():
    yield _FAKE
    _FAKE.stop()


@pytest.fixture(scope="session")
def srv():
    """The imported server module (the HTTP layer: app, sessions, app_build)."""
    return server


@pytest.fixture(autouse=True)
def clean_state(fake_lm):
    """Every test starts from the default site, empty sessions, Gemma active and an empty script."""
    fake_lm.reset()
    fake_lm.loaded = {"gemma-4-26b-a4b-it"}
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    db.init_db()
    server.sessions.clear()
    lmstudio.model_state.update(active=config.MODEL_NAME, switching=False, target=None, error=None)
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
    uv_config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(uv_config)
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


def pytest_collection_modifyitems(items):
    """Browser tests last: Playwright's sync API leaves an event loop running in the main thread, after which
    asyncio.run() (the sign-off gate's tests) would fail."""
    items.sort(key=lambda item: "e2e" in item.nodeid.split("::")[0])
