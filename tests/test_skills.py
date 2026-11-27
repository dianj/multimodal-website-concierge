"""Review T21.A3: a Skill is a file the client chose to load as one; it is keyed by name, capped and removable."""
import httpx
import pytest


@pytest.fixture()
def client(app_url):
    with httpx.Client(base_url=app_url, timeout=30) as c:
        yield c


def upload(client, name, content: bytes, kind="auto", message=""):
    return client.post("/api/chat", data={"message": message, "attachment_type": kind},
                       files={"file": (name, content, "text/plain")})


def test_a_file_with_guideline_in_its_name_is_not_a_skill_unless_chosen(client, fake_lm):
    """Probe: a plain content-guidelines-for-hero.txt became a system-level Skill because of its name."""
    fake_lm.script(reply="Read it.")
    data = upload(client, "content-guidelines-for-hero.txt", b"Hero title: Fast websites.").json()
    assert data["skill_loaded"] is None
    request = fake_lm.requests[-1]
    assert "Fast websites" not in request["messages"][0]["content"]                  # not in the system prompt
    assert "[Attached Document: content-guidelines-for-hero.txt]" in str(request["messages"][-1]["content"])


def test_the_same_skill_loaded_twice_replaces_the_first(client, fake_lm):
    fake_lm.script(reply="Noted.")
    upload(client, "brand.md", b"Never use red.", kind="skill")
    fake_lm.script(reply="Noted again.")
    upload(client, "brand.md", b"Never use orange.", kind="skill")
    prompt = fake_lm.last_system_prompt()
    assert "Never use orange." in prompt and "Never use red." not in prompt and prompt.count("brand.md") == 1
    assert client.get("/api/state").json()["active_skills"] == ["brand.md"]


@pytest.mark.parametrize("name, kind, size", [
    ("brand.md", "skill", 20 * 1024 + 1),           # Skill: 20 KB (probe: a 4.8 MB Skill was accepted)
    ("brief.txt", "auto", 2 * 1024 * 1024 + 1),     # text: 2 MB
    ("shot.png", "auto", 10 * 1024 * 1024 + 1),     # image: 10 MB
])
def test_files_over_their_cap_are_refused_with_413(client, fake_lm, name, kind, size):
    response = upload(client, name, b"x" * size, kind=kind)
    assert response.status_code == 413 and "too large" in response.json()["reply"]
    assert fake_lm.requests == []                                    # the model was not called
    assert client.get("/api/state").json()["active_skills"] == []


def test_a_skill_can_be_removed(client, fake_lm):
    fake_lm.script(reply="Noted.")
    upload(client, "brand.md", b"Never use red.", kind="skill")
    assert client.delete("/api/skills/brand.md").json()["active_skills"] == []
    assert client.delete("/api/skills/brand.md").status_code == 404
    fake_lm.script(reply="Hi.")
    client.post("/api/chat", data={"message": "hello"})
    assert "Never use red." not in fake_lm.last_system_prompt()
