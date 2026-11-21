"""Ticket system: tools, migration of the old table, HTTP API and the agent flow (fake LLM)."""
import json
import sqlite3

import httpx
import pytest

from concierge import config, db, tickets


def call(fn, *args, **kwargs):
    return json.loads(fn(*args, **kwargs))


# --- Tools -----------------------------------------------------------------------------

def test_support_ticket_records_request_reason_and_partial_work():
    result = call(tickets.create_support_ticket, "Add a newsletter signup form",
                  "A newsletter form with an email field at the bottom of the page.",
                  "There is no tool for forms.", priority="high", partial_work="Renamed the services heading.")
    assert result == {"status": "ticket_created", "ticket_id": "CR-0001", "type": "feature_request",
                      "priority": "high", "ticket_status": "open"}
    ticket = tickets.get_ticket(1)
    assert ticket["status"] == "open" and ticket["priority"] == "high"
    assert "no tool for forms" in ticket["details"] and "Renamed the services heading" in ticket["ticket_markdown"]


def test_support_ticket_validation():
    assert call(tickets.create_support_ticket, "", "x", "y")["status"] == "error"
    assert call(tickets.create_support_ticket, "t", "x", "y", priority="urgent")["status"] == "error"
    assert call(tickets.create_support_ticket, "t", "x", "y", ticket_type="approval")["status"] == "error"


def test_approval_ticket_has_type_title_and_state():
    result = call(tickets.create_approval_ticket, "Client approved the green theme")
    ticket = tickets.get_ticket(1)
    assert result["type"] == "approval" and ticket["type"] == "approval"
    assert ticket["title"] == "Approved website changes (v1)"
    assert '"primary_color"' in ticket["ticket_markdown"]  # developers get the approved JSON state


def test_ticket_status_lookup_accepts_ticket_codes():
    call(tickets.create_support_ticket, "Custom page", "A careers page.", "No tool for new pages.")
    tickets.set_ticket_status(1, "in_progress")
    one = call(tickets.get_ticket_status, "cr-0001")["ticket"]
    assert one["status"] == "in_progress" and one["title"] == "Custom page"
    assert call(tickets.get_ticket_status)["tickets"][0]["code"] == "CR-0001"
    assert call(tickets.get_ticket_status, "CR-0099")["status"] == "error"
    assert call(tickets.get_ticket_status, "abc")["status"] == "error"


def test_old_ticket_table_is_migrated(tmp_path, monkeypatch):
    old_db = tmp_path / "old.db"
    conn = sqlite3.connect(old_db)
    conn.execute("CREATE TABLE change_tickets (ticket_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, version_id INTEGER, ticket_markdown TEXT)")
    conn.execute("INSERT INTO change_tickets (created_at, version_id, ticket_markdown) VALUES ('2026-01-01', 3, '# old')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", str(old_db))
    db.init_db()
    legacy = tickets.get_ticket(1)
    assert legacy["type"] == "approval" and legacy["status"] == "open" and legacy["priority"] == "normal"


# --- HTTP API --------------------------------------------------------------------------

@pytest.fixture()
def client(app_url):
    with httpx.Client(base_url=app_url, timeout=30) as c:
        yield c


def test_ticket_api_lists_and_updates_status(client):
    call(tickets.create_support_ticket, "Newsletter form", "Signup form.", "No form tool.")
    listing = client.get("/api/tickets").json()
    assert listing["open_count"] == 1 and listing["tickets"][0]["code"] == "CR-0001"
    assert client.get("/api/tickets/CR-0001").json()["type"] == "feature_request"
    assert client.post("/api/tickets/CR-0001/status", data={"status": "done"}).json()["ticket"]["status"] == "done"
    assert client.get("/api/tickets").json()["open_count"] == 0
    assert client.post("/api/tickets/CR-0001/status", data={"status": "lost"}).status_code == 400
    assert client.post("/api/tickets/CR-0042/status", data={"status": "done"}).status_code == 404


def test_agent_creates_ticket_after_client_agrees_and_reports_status(client, fake_lm):
    # Turn 1: the agent cannot build a form and offers a ticket (no tool call yet).
    fake_lm.script(reply="I can't add forms with my tools. Shall I create a developer ticket?")
    client.post("/api/chat", data={"message": "Add a newsletter signup form"})
    assert tickets.list_tickets() == []
    assert "TICKETS:" in fake_lm.last_system_prompt()

    # Turn 2: the client agrees -> ticket created.
    fake_lm.script(("create_support_ticket", {"title": "Newsletter signup form",
                                              "client_request": "Add a newsletter signup form.",
                                              "reason": "No tool can add forms."}),
                   reply="Done, your ticket is CR-0001.")
    data = client.post("/api/chat", data={"message": "Yes, please"}).json()
    assert data["tools_used"][0].startswith("create_support_ticket")
    assert tickets.get_ticket(1)["title"] == "Newsletter signup form"

    # A developer moves it forward; turn 3: the client asks for the status.
    tickets.set_ticket_status(1, "in_progress")
    fake_lm.script(("get_ticket_status", {"ticket_id": "CR-0001"}), reply="It is in progress.")
    client.post("/api/chat", data={"message": "What's the status of my ticket?"})
    tool_result = fake_lm.requests[-1]["messages"][-1]
    assert tool_result["role"] == "tool" and '"in_progress"' in tool_result["content"]
