from __future__ import annotations

import json
from typing import Any

import sqlite3

UPSERT_MODES = ("patch", "replace")


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    if r is None:
        return None
    d = dict(r)
    d["payload_fields"] = json.loads(d.pop("payload_fields_json"))
    d["enabled"] = bool(d["enabled"])
    d.setdefault("upsert_mode", "replace")
    return d


def list_streams(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM project_streams ORDER BY id").fetchall()
    return [_row(r) for r in rows]  # type: ignore[misc]


def get_stream(conn: sqlite3.Connection, stream_id: int) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM project_streams WHERE id=?", (stream_id,)).fetchone())


def set_watermark(conn: sqlite3.Connection, stream_id: int, watermark_seq: int) -> None:
    conn.execute(
        "UPDATE project_streams SET watermark_seq=? WHERE id=?",
        (watermark_seq, stream_id),
    )


def set_upsert_mode(conn: sqlite3.Connection, stream_id: int, upsert_mode: str) -> None:
    if upsert_mode not in UPSERT_MODES:
        raise ValueError(f"unsupported upsert_mode: {upsert_mode}")
    conn.execute(
        "UPDATE project_streams SET upsert_mode=? WHERE id=?",
        (upsert_mode, stream_id),
    )


def insert_mode_change(
    conn: sqlite3.Connection,
    *,
    stream_id: int,
    from_mode: str | None,
    to_mode: str,
    cutoff_batch_id: int | None,
    note: str | None,
    changed_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO stream_mode_changes(
            stream_id, from_mode, to_mode, cutoff_batch_id, note, changed_at
        ) VALUES (?,?,?,?,?,?)
        """,
        (stream_id, from_mode, to_mode, cutoff_batch_id, note, changed_at),
    )
    return int(cur.lastrowid)


def list_mode_changes(
    conn: sqlite3.Connection, stream_id: int | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    if stream_id is None:
        rows = conn.execute(
            "SELECT * FROM stream_mode_changes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM stream_mode_changes WHERE stream_id=? ORDER BY id DESC LIMIT ?",
            (stream_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
