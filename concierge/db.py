"""SQLite storage: versioned site snapshots and the ticket table (with in-place migrations)."""
import json
import sqlite3
from datetime import datetime

from . import config
from .site_state import DEFAULT_SITE_STATE, upgrade_state

# Version status values: 0 = active history, 1 = undone (available for redo), 2 = discarded redo branch
STATUS_ACTIVE, STATUS_UNDONE, STATUS_DISCARDED = 0, 1, 2

# Ticket fields added after the first version of the table (approval-only tickets).
TICKET_COLUMNS = {
    "type": "TEXT NOT NULL DEFAULT 'approval'",
    "title": "TEXT",
    "details": "TEXT",
    "status": "TEXT NOT NULL DEFAULT 'open'",
    "priority": "TEXT NOT NULL DEFAULT 'normal'",
    "updated_at": "TEXT",
}

def connect():
    # Looked up on every call, so tests can point config.DB_PATH at a throwaway database.
    return sqlite3.connect(config.DB_PATH)

def init_db():
    conn = connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS site_versions (
            version_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            change_summary TEXT,
            state_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS change_tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            version_id INTEGER,
            ticket_markdown TEXT
        )
    """)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(site_versions)")]
    if "status" not in columns:
        conn.execute("ALTER TABLE site_versions ADD COLUMN status INTEGER NOT NULL DEFAULT 0")
    # Ticket fields added after the first version of the table (approval-only tickets).
    ticket_columns = [row[1] for row in conn.execute("PRAGMA table_info(change_tickets)")]
    for name, ddl in TICKET_COLUMNS.items():
        if name not in ticket_columns:
            conn.execute(f"ALTER TABLE change_tickets ADD COLUMN {name} {ddl}")
    count = conn.execute("SELECT COUNT(*) FROM site_versions").fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO site_versions (timestamp, change_summary, state_json) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), "Initial website baseline", json.dumps(DEFAULT_SITE_STATE))
        )
    conn.commit()
    conn.close()

def get_latest_state():
    conn = connect()
    row = conn.execute(
        "SELECT version_id, state_json FROM site_versions WHERE status = ? ORDER BY version_id DESC LIMIT 1",
        (STATUS_ACTIVE,)
    ).fetchone()
    conn.close()
    return row[0], upgrade_state(json.loads(row[1]))

def save_new_state(new_state: dict, summary: str):
    conn = connect()
    cur = conn.cursor()
    # A new change after Undo invalidates the redo branch (kept in the DB for auditing).
    cur.execute("UPDATE site_versions SET status = ? WHERE status = ?", (STATUS_DISCARDED, STATUS_UNDONE))
    cur.execute(
        "INSERT INTO site_versions (timestamp, change_summary, state_json) VALUES (?, ?, ?)",
        (datetime.now().isoformat(), summary, json.dumps(new_state))
    )
    ver_id = cur.lastrowid
    conn.commit()
    conn.close()
    return ver_id
