"""Small SQLite store for recoverable interview sessions."""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DB_PATH = os.getenv("INTERVIEW_DB_PATH", str(Path(__file__).with_name("interviews.sqlite3")))


@contextmanager
def connection():
    """Open a SQLite connection and close it after the database operation."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create the interview table if it does not already exist."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS interviews (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()


def save_interview(session):
    """Insert or update a complete interview record in SQLite."""
    with connection() as conn:
        conn.execute(
            "INSERT INTO interviews(id,data,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP",
            (session["id"], json.dumps(session, ensure_ascii=False)),
        )
        conn.commit()


def load_interview(session_id):
    """Load one saved interview by ID, or return None if it is missing."""
    with connection() as conn:
        row = conn.execute("SELECT data FROM interviews WHERE id=?", (session_id,)).fetchone()
        return json.loads(row["data"]) if row else None


def recent_interview():
    """Load the interview updated most recently, if one exists."""
    with connection() as conn:
        row = conn.execute("SELECT data FROM interviews ORDER BY updated_at DESC LIMIT 1").fetchone()
        return json.loads(row["data"]) if row else None
