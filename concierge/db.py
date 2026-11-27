"""SQLite storage: versioned site snapshots and the ticket table (with in-place migrations)."""
import json
import sqlite3
from contextlib import contextmanager
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
    "evidence": "TEXT",     # approval tickets: the client's words and the sign-off gate's decision (JSON)
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

def get_state(version_id: int) -> dict | None:
    """A stored version (any status), upgraded to the current schema."""
    conn = connect()
    row = conn.execute("SELECT state_json FROM site_versions WHERE version_id = ?", (version_id,)).fetchone()
    conn.close()
    return upgrade_state(json.loads(row[0])) if row else None

class StaleVersion(Exception):
    """The state was changed by someone else after this change read it (review T21.A7: lost updates)."""


@contextmanager
def write_transaction():
    """One writer at a time: BEGIN IMMEDIATE takes the write lock before reading, so a check and its write cannot
    interleave with another writer's."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def latest_active_version(conn) -> int:
    return conn.execute("SELECT MAX(version_id) FROM site_versions WHERE status = ?", (STATUS_ACTIVE,)).fetchone()[0]


def save_new_state(new_state: dict, summary: str, parent: int | None = None):
    """Save a full snapshot. With `parent` (the version the change was made on) the save is refused with StaleVersion
    when another change was saved in between, instead of silently overwriting it."""
    with write_transaction() as conn:
        if parent is not None and latest_active_version(conn) != parent:
            raise StaleVersion(f"version {parent} is no longer the latest")
        # A new change after Undo invalidates the redo branch (kept in the DB for auditing).
        conn.execute("UPDATE site_versions SET status = ? WHERE status = ?", (STATUS_DISCARDED, STATUS_UNDONE))
        cur = conn.execute(
            "INSERT INTO site_versions (timestamp, change_summary, state_json) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), summary, json.dumps(new_state))
        )
        return cur.lastrowid
