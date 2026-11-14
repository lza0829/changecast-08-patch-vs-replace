from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.config import FIXTURES_DIR
from app.db import begin_write, connect
from app.engines.project import UPSERT_MODES, apply_events
from app.repositories import batches as batch_repo
from app.repositories import projections as proj_repo
from app.repositories import streams as stream_repo

MODE_LABELS = {
    "patch": "字段级补丁：只更新事件载荷里出现的键，未出现字段保留投影原值",
    "replace": "整行替换：事件载荷成为该实体完整新行，未出现字段视为清除",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_fixture(name: str) -> list[dict[str, Any]]:
    path = FIXTURES_DIR / name
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"fixture {name} must be a JSON array")
    return data


def _projection_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        r["entity_key"]: {
            "entity_key": r["entity_key"],
            "payload": r.get("payload"),
            "applied_seq": r["applied_seq"],
            "applied_event_id": r["applied_event_id"],
            "deleted": bool(r.get("deleted")),
        }
        for r in rows
    }


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        mode_counts = {mode: 0 for mode in UPSERT_MODES}
        for s in streams:
            mode_counts[s["upsert_mode"]] = mode_counts.get(s["upsert_mode"], 0) + 1
        return {
            "stream_count": conn.execute("SELECT COUNT(*) c FROM project_streams").fetchone()["c"],
            "enabled_streams": conn.execute(
                "SELECT COUNT(*) c FROM project_streams WHERE enabled=1"
            ).fetchone()["c"],
            "batch_count": conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"],
            "projection_alive": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=0"
            ).fetchone()["c"],
            "projection_deleted": conn.execute(
                "SELECT COUNT(*) c FROM projection_rows WHERE deleted=1"
            ).fetchone()["c"],
            "ooo_buffered": conn.execute("SELECT COUNT(*) c FROM ooo_buffer").fetchone()["c"],
            "apply_order": conn.execute(
                "SELECT value FROM settings WHERE key='apply_order'"
            ).fetchone()["value"],
            "upsert_mode_counts": mode_counts,
        }
    finally:
        conn.close()


def list_streams() -> list[dict[str, Any]]:
    conn = connect()
    try:
        streams = stream_repo.list_streams(conn)
        for s in streams:
            s["upsert_mode_label"] = MODE_LABELS.get(s["upsert_mode"], s["upsert_mode"])
            s["has_running_batch"] = bool(batch_repo.list_running_batches(conn, s["id"]))
        return streams
    finally:
        conn.close()


def list_batches(limit: int = 50) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = batch_repo.list_batches(conn, limit=limit)
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
            r["upsert_mode_label"] = MODE_LABELS.get(r.get("upsert_mode"), r.get("upsert_mode"))
        return rows
    finally:
        conn.close()


def list_projections(stream_id: int | None = None, include_deleted: bool = True) -> list[dict[str, Any]]:
    conn = connect()
    try:
        rows = proj_repo.list_projections(
            conn, stream_id=stream_id, include_deleted=include_deleted
        )
        streams = {s["id"]: s for s in stream_repo.list_streams(conn)}
        for r in rows:
            s = streams.get(r["stream_id"])
            r["stream_code"] = s["code"] if s else None
            r["stream_title"] = s["title"] if s else None
        return rows
    finally:
        conn.close()


def list_events(batch_id: int | None = None, stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_applied_events(conn, batch_id=batch_id, stream_id=stream_id)
    finally:
        conn.close()


def list_ooo_buffer(stream_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return proj_repo.list_ooo_buffer(conn, stream_id=stream_id)
    finally:
        conn.close()


def list_settings() -> dict[str, str]:
    conn = connect()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def get_stream_policy(stream_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise LookupError("stream not found")
        return {
            "stream_id": stream_id,
            "stream_code": stream["code"],
            "stream_title": stream["title"],
            "upsert_mode": stream["upsert_mode"],
            "upsert_mode_label": MODE_LABELS.get(stream["upsert_mode"], ""),
            "has_running_batch": bool(batch_repo.list_running_batches(conn, stream_id)),
            "mode_changes": stream_repo.list_mode_changes(conn, stream_id=stream_id, limit=20),
        }
    finally:
        conn.close()


def change_upsert_mode(stream_id: int, mode: str, note: str | None = None) -> dict[str, Any]:
    """Switch a stream's upsert semantics for *subsequent* batches only.

    A batch that is currently running keeps the mode captured at its start, and
    changing the mode while one runs is refused.  Completed batches are never
    rewritten: each one stores its own mode snapshot and event traces.
    """

    if mode not in UPSERT_MODES:
        raise ValueError(f"unsupported upsert_mode: {mode}")
    conn = connect()
    try:
        # Serialize against batch starts: the running-batch check and the mode
        # write happen inside one immediate write transaction.
        begin_write(conn)
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            conn.rollback()
            raise LookupError("stream not found")
        if batch_repo.list_running_batches(conn, stream_id):
            conn.rollback()
            raise ValueError("批次进行中，策略变更需等当前批次结束")
        previous = stream["upsert_mode"]
        if previous == mode:
            conn.rollback()
            return {
                "changed": False,
                "stream_id": stream_id,
                "upsert_mode": previous,
                "message": "策略未变化",
            }
        cutoff_batch_id = batch_repo.last_completed_batch_id(conn, stream_id)
        now = _now()
        stream_repo.set_upsert_mode(conn, stream_id, mode)
        change_id = stream_repo.insert_mode_change(
            conn,
            stream_id=stream_id,
            from_mode=previous,
            to_mode=mode,
            cutoff_batch_id=cutoff_batch_id,
            note=note,
            changed_at=now,
        )
        conn.commit()
        return {
            "changed": True,
            "id": change_id,
            "stream_id": stream_id,
            "from_mode": previous,
            "upsert_mode": mode,
            "upsert_mode_label": MODE_LABELS.get(mode, ""),
            "effective_after_batch_id": cutoff_batch_id,
            "changed_at": now,
            "message": "策略已切换，仅对之后新开批次生效",
        }
    finally:
        conn.close()


def run_project(stream_id: int, note: str | None = None) -> dict[str, Any]:
    """Run one projection batch, capturing the stream's mode at batch start."""

    conn = connect()
    try:
        # The mode is captured and the running batch row is claimed in one
        # immediate transaction, so a concurrent policy switch cannot land
        # between the check and the start.
        begin_write(conn)
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            conn.rollback()
            raise ValueError("stream not found")
        if not stream["enabled"]:
            conn.rollback()
            raise ValueError("stream disabled")
        if batch_repo.list_running_batches(conn, stream_id):
            conn.rollback()
            raise ValueError("该流已有进行中的批次，请稍后再试")

        upsert_mode = stream["upsert_mode"]
        events = _load_fixture(stream["fixture"])
        now = _now()
        seq = conn.execute("SELECT COUNT(*) c FROM project_batches").fetchone()["c"] + 1
        batch_no = f"PB-{now[:10].replace('-', '')}-{seq:04d}"
        try:
            batch_id = batch_repo.insert_batch(
                conn,
                batch_no=batch_no,
                stream_id=stream_id,
                status="running",
                counts={
                    "source_events": len(events),
                    "applied_upsert": 0,
                    "applied_delete": 0,
                    "skipped_duplicate": 0,
                    "projected_alive": 0,
                    "projected_deleted": 0,
                    "max_seq": 0,
                },
                note=note,
                created_at=now,
                upsert_mode=upsert_mode,
            )
        except Exception as exc:  # unique partial index: one running batch per stream
            raise ValueError("该流已有进行中的批次，请稍后再试") from exc
        conn.commit()

        try:
            initial = _projection_map(proj_repo.list_projections(conn, stream_id=stream_id))
            # Full fixture replay on top of the persisted projection: patch mode
            # keeps fields the payload omits; replace mode rewrites the whole row.
            result = apply_events(
                events,
                known_event_ids=set(),
                initial_projection=initial,
                upsert_mode=upsert_mode,
            )
            proj_repo.replace_stream_projection(
                conn, stream_id=stream_id, rows=result["projection"], updated_at=now
            )
            for ev in result["applied_log"]:
                batch_repo.insert_applied_event(
                    conn, batch_id=batch_id, stream_id=stream_id, event=ev, created_at=now
                )
            stream_repo.set_watermark(conn, stream_id, result["counts"]["max_seq"])
            batch_repo.finish_batch(
                conn, batch_id=batch_id, status="completed", counts=result["counts"]
            )
            conn.commit()
        except Exception:
            conn.rollback()
            final_conn = connect()
            try:
                batch_repo.finish_batch(
                    final_conn, batch_id=batch_id, status="failed", counts=None
                )
                final_conn.commit()
            finally:
                final_conn.close()
            raise

        batch = batch_repo.get_batch(conn, batch_id)
        assert batch is not None
        batch["stream_code"] = stream["code"]
        batch["stream_title"] = stream["title"]
        batch["apply_order"] = "by_seq"
        batch["upsert_mode_label"] = MODE_LABELS.get(upsert_mode, upsert_mode)
        batch["counts"] = result["counts"]
        batch["projection_preview"] = proj_repo.list_projections(conn, stream_id=stream_id)[:20]
        batch["event_preview"] = batch_repo.list_applied_events(conn, batch_id=batch_id, limit=20)
        return batch
    finally:
        conn.close()


def merge_compare(stream_id: int) -> dict[str, Any]:
    """Dry-run the stream fixture under both modes from the same starting snapshot.

    Nothing is persisted; this is the side-by-side explanation of what patch
    would preserve and what replace would clear.
    """

    conn = connect()
    try:
        stream = stream_repo.get_stream(conn, stream_id)
        if not stream:
            raise LookupError("stream not found")
        events = _load_fixture(stream["fixture"])
        initial = _projection_map(proj_repo.list_projections(conn, stream_id=stream_id))
        runs = {
            mode: apply_events(
                events,
                known_event_ids=set(),
                initial_projection=initial,
                upsert_mode=mode,
            )
            for mode in UPSERT_MODES
        }

        patch_rows = {r["entity_key"]: r for r in runs["patch"]["projection"]}
        replace_rows = {r["entity_key"]: r for r in runs["replace"]["projection"]}
        entities: list[dict[str, Any]] = []
        for key in sorted(set(patch_rows) | set(replace_rows)):
            p = patch_rows.get(key)
            q = replace_rows.get(key)
            p_payload = (p or {}).get("payload") or {}
            q_payload = (q or {}).get("payload") or {}
            fields = sorted(set(p_payload) | set(q_payload))
            field_diffs = []
            for field in fields:
                pv = p_payload.get(field, "<absent>")
                qv = q_payload.get(field, "<absent>")
                if pv != qv:
                    field_diffs.append(
                        {
                            "field": field,
                            "patch_value": None if pv == "<absent>" else pv,
                            "replace_value": None if qv == "<absent>" else qv,
                            "patch_present": field in p_payload,
                            "replace_present": field in q_payload,
                        }
                    )
            entities.append(
                {
                    "entity_key": key,
                    "patch": {
                        "payload": p_payload,
                        "applied_seq": (p or {}).get("applied_seq"),
                        "deleted": bool((p or {}).get("deleted")),
                    },
                    "replace": {
                        "payload": q_payload,
                        "applied_seq": (q or {}).get("applied_seq"),
                        "deleted": bool((q or {}).get("deleted")),
                    },
                    "field_diffs": field_diffs,
                    "diverges": bool(field_diffs),
                }
            )
        return {
            "stream_id": stream_id,
            "stream_code": stream["code"],
            "stream_title": stream["title"],
            "current_mode": stream["upsert_mode"],
            "event_count": len(events),
            "modes": {
                mode: {
                    "label": MODE_LABELS[mode],
                    "counts": runs[mode]["counts"],
                    "projection": runs[mode]["projection"],
                    "applied_log": runs[mode]["applied_log"],
                }
                for mode in UPSERT_MODES
            },
            "entities": entities,
        }
    finally:
        conn.close()
