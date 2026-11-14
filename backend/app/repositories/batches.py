from __future__ import annotations

from typing import Any

import sqlite3


def insert_batch(
    conn: sqlite3.Connection,
    *,
    batch_no: str,
    stream_id: int,
    status: str,
    counts: dict[str, int],
    note: str | None,
    created_at: str,
    upsert_mode: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO project_batches(
            batch_no, stream_id, status, source_events, applied_upsert, applied_delete,
            skipped_duplicate, projected_alive, projected_deleted, max_seq, note,
            created_at, upsert_mode
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_no,
            stream_id,
            status,
            counts["source_events"],
            counts["applied_upsert"],
            counts["applied_delete"],
            counts["skipped_duplicate"],
            counts["projected_alive"],
            counts["projected_deleted"],
            counts["max_seq"],
            note,
            created_at,
            upsert_mode,
        ),
    )
    return int(cur.lastrowid)


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_batches ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM project_batches WHERE id=?", (batch_id,)).fetchone()
    return dict(r) if r else None


def last_completed_batch_id(conn: sqlite3.Connection, stream_id: int) -> int | None:
    r = conn.execute(
        "SELECT MAX(id) AS id FROM project_batches WHERE stream_id=? AND status='completed'",
        (stream_id,),
    ).fetchone()
    return int(r["id"]) if r and r["id"] is not None else None


def list_running_batches(conn: sqlite3.Connection, stream_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM project_batches WHERE stream_id=? AND status='running' ORDER BY id",
        (stream_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def finish_batch(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    status: str,
    counts: dict[str, int] | None,
) -> None:
    if counts is None:
        conn.execute(
            "UPDATE project_batches SET status=? WHERE id=?",
            (status, batch_id),
        )
        return
    conn.execute(
        """
        UPDATE project_batches SET
            status=?, source_events=?, applied_upsert=?, applied_delete=?,
            skipped_duplicate=?, projected_alive=?, projected_deleted=?, max_seq=?
        WHERE id=?
        """,
        (
            status,
            counts["source_events"],
            counts["applied_upsert"],
            counts["applied_delete"],
            counts["skipped_duplicate"],
            counts["projected_alive"],
            counts["projected_deleted"],
            counts["max_seq"],
            batch_id,
        ),
    )


def insert_applied_event(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    stream_id: int,
    event: dict[str, Any],
    created_at: str,
) -> None:
    import json

    conn.execute(
        """
        INSERT INTO applied_events(
            batch_id, stream_id, event_id, entity_key, op, seq, event_time, result,
            created_at, merge_mode, payload_json, before_json, after_json,
            cleared_json, preserved_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            stream_id,
            event["event_id"],
            event["entity_key"],
            event["op"],
            event["seq"],
            event["event_time"],
            event["result"],
            created_at,
            event.get("merge_mode"),
            json.dumps(event.get("payload"), ensure_ascii=False)
            if event.get("payload") is not None
            else None,
            json.dumps(event.get("before_payload"), ensure_ascii=False)
            if event.get("before_payload") is not None
            else None,
            json.dumps(event.get("after_payload"), ensure_ascii=False)
            if event.get("after_payload") is not None
            else None,
            json.dumps(event.get("cleared_fields") or [], ensure_ascii=False),
            json.dumps(event.get("preserved_fields") or [], ensure_ascii=False),
        ),
    )


def _decode_event(row: dict[str, Any]) -> dict[str, Any]:
    import json

    d = dict(row)
    for src, dest in (
        ("payload_json", "payload"),
        ("before_json", "before_payload"),
        ("after_json", "after_payload"),
    ):
        raw = d.pop(src, None)
        d[dest] = json.loads(raw) if raw else None
    for src, dest in (("cleared_json", "cleared_fields"), ("preserved_json", "preserved_fields")):
        raw = d.pop(src, None)
        d[dest] = json.loads(raw) if raw else []
    return d


def list_applied_events(
    conn: sqlite3.Connection,
    *,
    batch_id: int | None = None,
    stream_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM applied_events WHERE 1=1"
    params: list[Any] = []
    if batch_id is not None:
        sql += " AND batch_id=?"
        params.append(batch_id)
    if stream_id is not None:
        sql += " AND stream_id=?"
        params.append(stream_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [_decode_event(dict(r)) for r in conn.execute(sql, params).fetchall()]
