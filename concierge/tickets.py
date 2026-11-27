"""Developer tickets (CR-XXXX): storage helpers and the three ticket tools."""
import json
import re
import sqlite3
from datetime import datetime

from .db import connect, get_latest_state, get_state
from .site_state import normalize_text

# approval        = the client approved the current site; developers receive the JSON state.
# feature_request = something the agent cannot do with its tools (e.g. a newsletter form).
# support         = a problem or question that needs a human.
TICKET_TYPES = ("approval", "feature_request", "support")
TICKET_STATUSES = ("open", "in_progress", "done", "rejected")
TICKET_PRIORITIES = ("low", "normal", "high")

def ticket_code(ticket_id: int) -> str:
    return f"CR-{ticket_id:04d}"

def insert_ticket(ticket_type: str, title: str, details: str, priority: str, version_id: int, markdown: str,
                  evidence: dict | None = None) -> int:
    now = datetime.now().isoformat()
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO change_tickets "
        "(created_at, updated_at, version_id, ticket_markdown, type, title, details, status, priority, evidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
        (now, now, version_id, markdown, ticket_type, title, details, priority,
         json.dumps(evidence, ensure_ascii=False) if evidence is not None else None)
    )
    ticket_id = cur.lastrowid
    conn.commit()
    conn.close()
    return ticket_id

def list_tickets(limit: int = 50) -> list:
    conn = connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ticket_id, created_at, updated_at, version_id, type, title, status, priority "
        "FROM change_tickets ORDER BY ticket_id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [{**dict(r), "code": ticket_code(r["ticket_id"])} for r in rows]

def get_ticket(ticket_id: int):
    conn = connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM change_tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    return {**dict(row), "code": ticket_code(row["ticket_id"])} if row else None

def parse_ticket_id(value) -> int:
    """Accept 'CR-0007', '7' or 7."""
    match = re.fullmatch(r"(?:CR-)?0*(\d+)", str(value).strip(), flags=re.IGNORECASE)
    if not match:
        raise ValueError("ticket_id must look like 'CR-0007'.")
    return int(match.group(1))

def set_ticket_status(ticket_id: int, status: str) -> bool:
    conn = connect()
    cur = conn.execute(
        "UPDATE change_tickets SET status = ?, updated_at = ? WHERE ticket_id = ?",
        (status, datetime.now().isoformat(), ticket_id)
    )
    conn.commit()
    conn.close()
    return cur.rowcount == 1

def state_changes(old, new, path: str = "") -> list[str]:
    """What differs between two site states, one line per changed value: 'theme.primary_color: "#2563eb" -> ...'."""
    if isinstance(old, dict) and isinstance(new, dict):
        lines = []
        for key in sorted(set(old) | set(new), key=str):
            lines += state_changes(old.get(key), new.get(key), f"{path}.{key}" if path else str(key))
        return lines
    if old == new:
        return []

    def short(value):
        text = json.dumps(value, ensure_ascii=False)
        return text if len(text) <= 120 else text[:117] + "..."
    return [f"{path}: {short(old)} -> {short(new)}"]


def approved_versions(conn) -> list[tuple[int, int]]:
    return conn.execute("SELECT ticket_id, version_id FROM change_tickets WHERE type = 'approval' "
                        "ORDER BY version_id").fetchall()


def evidence_markdown(evidence: dict) -> str:
    if evidence.get("gate") == "off":
        return f"## Client's sign-off\n> {evidence['client_message']}\n\n(The sign-off gate was off.)\n"

    def pct(value):
        return "—" if value is None else f"{value:.3f}"
    return (f"## Client's sign-off\n> {evidence['client_message']}\n\n"
            f"In reply to: \"{evidence['previous']}\"\n\n"
            f"## Sign-off gate\np(approve) {pct(evidence.get('p_approve'))} · p(flag) {pct(evidence.get('p_flag'))} · "
            f"action **{evidence['action']}** ({evidence['reason']}) · model `{evidence['model']}` · calibration "
            f"`{evidence['calibration_sha256'] or 'none'}`\n")


def create_approval_ticket(summary_notes: str, evidence: dict | None = None):
    """One approval ticket per site version. `evidence` (the client's words and the gate's decision) is added by the
    agent loop, never by the model (review T21.A5)."""
    ver_id, state = get_latest_state()
    conn = connect()
    approved = approved_versions(conn)
    conn.close()
    existing = [t for t, v in approved if v == ver_id]
    if existing:
        return json.dumps({"status": "already_approved", "ticket_id": ticket_code(existing[0]), "version": ver_id,
                           "message": "This version already has an approval ticket; no new ticket was created."})
    earlier = [v for _, v in approved if v < ver_id]
    if earlier:
        changes = state_changes(get_state(earlier[-1]), state)
        changes_md = (f"## Changes since the last approved version (v{earlier[-1]})\n"
                      + ("".join(f"- `{line}`\n" for line in changes) if changes else "- (none)\n"))
    else:
        changes_md = "## Changes\nFirst approved version.\n"
    summary = normalize_text(str(summary_notes), True)[:2000]
    ticket_md = (
        f"# Approved Client Change Request (Version v{ver_id})\n\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n**Summary:** {summary}\n\n"
        + (evidence_markdown(evidence) + "\n" if evidence else "")
        + changes_md + "\n"
        f"```json\n{json.dumps(state, indent=2, ensure_ascii=False)}\n```"
    )
    t_id = insert_ticket("approval", f"Approved website changes (v{ver_id})", summary, "normal", ver_id, ticket_md,
                         evidence)
    return json.dumps({"status": "ticket_created", "ticket_id": ticket_code(t_id), "type": "approval",
                       "version": ver_id})

def create_support_ticket(title: str, client_request: str, reason: str, ticket_type: str = "feature_request",
                          priority: str = "normal", partial_work: str = None):
    if ticket_type not in ("feature_request", "support"):
        return json.dumps({"status": "error", "message": "ticket_type must be 'feature_request' or 'support'."})
    if priority not in TICKET_PRIORITIES:
        return json.dumps({"status": "error", "message": f"priority must be one of {list(TICKET_PRIORITIES)}."})
    fields = {"title": title, "client_request": client_request, "reason": reason}
    if any(not isinstance(v, str) or not v.strip() for v in fields.values()):
        return json.dumps({"status": "error", "message": "title, client_request and reason must be non-empty strings."})
    title = normalize_text(title, False)[:120]
    client_request = normalize_text(client_request, True)[:2000]
    reason = normalize_text(reason, True)[:1000]
    has_partial = isinstance(partial_work, str) and partial_work.strip()
    partial = normalize_text(partial_work, True)[:1000] if has_partial else None
    ver_id, _ = get_latest_state()
    details = f"Client request: {client_request}\n\nWhy the assistant could not do it: {reason}"
    if partial:
        details += f"\n\nAlready done by the assistant: {partial}"
    label = "Feature Request" if ticket_type == "feature_request" else "Support Request"
    ticket_md = (
        f"# {label}: {title}\n\n**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')} · **Priority:** {priority} · "
        f"**Site version:** v{ver_id}\n\n## Client request\n{client_request}\n\n"
        f"## Why the assistant could not do it\n{reason}\n"
        + (f"\n## Already done by the assistant\n{partial}\n" if partial else "")
    )
    t_id = insert_ticket(ticket_type, title, details, priority, ver_id, ticket_md)
    return json.dumps({"status": "ticket_created", "ticket_id": ticket_code(t_id), "type": ticket_type,
                       "priority": priority, "ticket_status": "open"})

def get_ticket_status(ticket_id: str = None):
    if ticket_id:
        try:
            ticket = get_ticket(parse_ticket_id(ticket_id))
        except ValueError as e:
            return json.dumps({"status": "error", "message": str(e)})
        if not ticket:
            return json.dumps({"status": "error", "message": f"Ticket {ticket_id} does not exist."})
        keys = ("code", "type", "title", "status", "priority", "created_at", "updated_at", "details")
        return json.dumps({"status": "success", "ticket": {k: ticket[k] for k in keys}})
    tickets = list_tickets(limit=10)
    return json.dumps({"status": "success", "tickets": [
        {k: t[k] for k in ("code", "type", "title", "status", "priority", "created_at")} for t in tickets
    ]})
