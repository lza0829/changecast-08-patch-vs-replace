from __future__ import annotations

import sqlite3

from app.config import DATA_DIR, DB_FILENAME

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / DB_FILENAME


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def begin_write(conn: sqlite3.Connection) -> None:
    """Take the write lock up front so read-then-write checks are serialized.

    Policy changes and batch starts both inspect "is a batch running?" before
    writing; an immediate transaction makes that check-and-act atomic instead
    of leaving a window where a switch slips in mid-start.
    """

    conn.execute("BEGIN IMMEDIATE")
