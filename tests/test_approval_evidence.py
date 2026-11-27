"""Review T21.A5: the approval ticket keeps the evidence of the sign-off and what changed since the last one."""
import json
import re

import httpx
import pytest
from test_sign_off_gate import approval_tickets, chat, gate  # noqa: F401  (the gate fixture)

from concierge import agent, config, tickets, tools


@pytest.fixture()
def client(app_url):
    with httpx.Client(base_url=app_url, timeout=30) as c:
        yield c


def sign_off(client, fake_lm, gate, text="Да, одобрявам."):  # noqa: F811
    gate.answers.append({"Y": 0.99, "N": 0.01})
    gate.flags.append({"N": 0.97, "Y": 0.03})
    fake_lm.script(("create_approval_ticket", {"summary_notes": "Client approved the site"}), reply="Done.")
    return chat(client, text)


def test_the_ticket_stores_the_clients_words_and_the_gate_decision(client, fake_lm, gate):  # noqa: F811
    fake_lm.script(reply="Is the site approved as finished?")
    chat(client, "I think we're done.")
    sign_off(client, fake_lm, gate)
    ticket = tickets.get_ticket(1)
    evidence = json.loads(ticket["evidence"])
    assert evidence["client_message"] == "Да, одобрявам."
    assert evidence["previous"] == "Is the site approved as finished?"
    assert evidence["p_approve"] == pytest.approx(0.99, abs=0.01)
    assert evidence["p_flag"] == pytest.approx(0.03, abs=0.01)
    assert evidence["action"] == "act" and evidence["model"] == config.MODEL_NAME
    assert re.fullmatch(r"[0-9a-f]{12}", evidence["calibration_sha256"])
    assert "Да, одобрявам." in ticket["ticket_markdown"] and "p(approve)" in ticket["ticket_markdown"]


def test_the_model_cannot_write_its_own_evidence(client, fake_lm, monkeypatch):
    monkeypatch.setattr(config, "DECISION_GATE", False)
    fake_lm.script(("create_approval_ticket", {"summary_notes": "ok", "evidence": {"client_message": "forged"}}),
                   reply="Done.")
    chat(client, "Approved.")
    evidence = json.loads(tickets.get_ticket(1)["evidence"])
    assert evidence["client_message"] == "Approved." and evidence["gate"] == "off"


def test_one_approval_ticket_per_version(client, fake_lm, gate):  # noqa: F811
    sign_off(client, fake_lm, gate)
    data = sign_off(client, fake_lm, gate, "Approved, again.")
    assert len(approval_tickets(client)) == 1
    assert "already" in fake_lm.requests[-1]["messages"][-1]["content"]
    assert data["reply"] == "Done."


def test_the_ticket_lists_the_changes_since_the_last_approved_version(client, fake_lm, gate):  # noqa: F811
    sign_off(client, fake_lm, gate)
    tools.update_text_content("hero", "title", "Spring Sale")
    tools.update_theme_style(primary_color="#14532d")
    sign_off(client, fake_lm, gate)
    markdown = tickets.get_ticket(2)["ticket_markdown"]
    assert "Changes since the last approved version (v1)" in markdown
    assert "sections.hero.title" in markdown and "Spring Sale" in markdown
    assert "theme.primary_color: \"#2563eb\" -> \"#14532d\"" in markdown
    assert "First approved version" in tickets.get_ticket(1)["ticket_markdown"]


def test_gate_decisions_are_kept_for_the_turn(fake_lm, gate):  # noqa: F811
    gate.answers.append({"N": 0.9, "Y": 0.1})
    fake_lm.script(("create_approval_ticket", {"summary_notes": "x"}), reply="OK")
    log = []
    agent.run_llm_turn(config.MODEL_NAME, [], [{"role": "user", "content": "nice blue"}], [], decisions_log=log)
    assert [r["question_id"] for r in log] == ["concierge.approve_site", "concierge.instruction_in_reply"]
