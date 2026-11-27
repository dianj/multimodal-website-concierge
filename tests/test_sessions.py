"""Review T21.A6 / T25.S5: sessions have a cap, cookieless polling makes none, a client address has a chat budget, and
one session runs one turn at a time."""
import threading

import httpx
import pytest

from concierge import config


@pytest.fixture()
def client(app_url):
    with httpx.Client(base_url=app_url, timeout=30) as c:
        yield c


def test_cookieless_polling_makes_no_sessions(app_url, srv):
    """Probe: 1,000 cookieless GETs made 1,000 sessions."""
    with httpx.Client(base_url=app_url) as c:
        for _ in range(300):
            c.cookies.clear()                                            # like a new client each time
            assert c.get("/api/state").status_code == 200
    assert len(srv.sessions) == 0


def test_sessions_are_capped_and_the_least_recently_used_goes_first(client, app_url, fake_lm, srv, monkeypatch):
    monkeypatch.setattr(srv, "MAX_SESSIONS", 5)
    client.post("/api/chat", data={"message": "hi"})                      # the guest's own session
    mine = client.cookies["concierge_session"]
    for i in range(10):
        httpx.post(f"{app_url}/api/chat", data={"message": f"other {i}"})
        client.get("/api/state")                                         # the guest keeps using the app
    assert len(srv.sessions) == 5 and mine in srv.sessions


def test_the_61st_chat_in_a_minute_from_one_address_gets_429(client, fake_lm, srv, monkeypatch):
    monkeypatch.setattr(srv.chat_limiter, "capacity", 3)
    monkeypatch.setattr(srv.chat_limiter, "rate", 3 / 60)
    for _ in range(3):
        assert client.post("/api/chat", data={"message": "hi"}).status_code == 200
    response = client.post("/api/chat", data={"message": "hi"})
    assert response.status_code == 429 and "Retry-After" in response.headers
    assert config.CHAT_PER_MINUTE == 60


def test_one_session_runs_one_turn_at_a_time(client, fake_lm):
    """Two messages sent at once in one session: the second turn sees the first one's reply in its history."""
    client.post("/api/chat", data={"message": "start"})                  # makes the session
    fake_lm.reset()
    fake_lm.delay_s = 0.4
    fake_lm.script(reply="First answer.")
    fake_lm.script(reply="Second answer.")
    cookies = dict(client.cookies)
    results = {}

    def send(name):
        with httpx.Client(base_url=str(client.base_url), cookies=cookies, timeout=30) as c:
            results[name] = c.post("/api/chat", data={"message": name}).json()["reply"]

    threads = [threading.Thread(target=send, args=(name,)) for name in ("one", "two")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    second = fake_lm.requests[1]["messages"]
    assert any(m["role"] == "assistant" and m["content"] == "First answer." for m in second)


def test_removing_a_skill_without_a_session_is_a_404(app_url):
    assert httpx.delete(f"{app_url}/api/skills/brand.md").status_code == 404
