"""Apply CDC events onto a projection with configurable upsert merge semantics.

Two mutually exclusive upsert modes are supported per stream:

* ``patch``   -- only keys present in the event payload are written; keys that
  are absent keep the value already held by the projection.  An explicitly
  present ``null`` is still written (it is a value, not an omission).
* ``replace`` -- the event payload becomes the entity's complete new row.
  Keys absent from the payload are cleared, and the trace distinguishes an
  explicit ``null`` from an omitted key.

The engine is intentionally pure: it receives the current projection and
returns the new projection plus a per-event trace.  Deletes keep the existing
soft-delete tombstone behaviour.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

UPSERT_MODES = ("patch", "replace")
DEFAULT_UPSERT_MODE = "replace"


def _as_payload(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _copy_projection(
    initial_projection: Mapping[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, row in (initial_projection or {}).items():
        copied = dict(row)
        copied["payload"] = copy.deepcopy(copied.get("payload"))
        copied["deleted"] = bool(copied.get("deleted"))
        out[str(key)] = copied
    return out


def merge_payload(
    mode: str,
    before: Mapping[str, Any] | None,
    event_payload: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]], list[str]]:
    """Return ``(after, cleared_fields, preserved_fields)`` for one upsert.

    ``cleared_fields`` entries are ``{"field": name, "reason": reason}`` where
    ``reason`` is ``explicit_null`` or ``missing``.  It is always empty for
    patch mode: a missing key never clears a projected value.  ``preserved_fields``
    lists keys that existed before and were not supplied by the event.
    """

    if mode not in UPSERT_MODES:
        raise ValueError(f"unsupported upsert_mode: {mode}")

    before_payload = _as_payload(before)
    incoming = _as_payload(event_payload)

    if mode == "patch":
        after = copy.deepcopy(before_payload)
        after.update(copy.deepcopy(incoming))
        cleared: list[dict[str, str]] = []
        preserved = sorted(k for k in before_payload if k not in incoming)
        return after, cleared, preserved

    after = copy.deepcopy(incoming)
    cleared = []
    preserved = []
    for field, previous in before_payload.items():
        if field not in incoming:
            cleared.append({"field": field, "reason": "missing"})
        elif incoming[field] is None:
            cleared.append({"field": field, "reason": "explicit_null"})
        else:
            preserved.append(field)
    cleared.sort(key=lambda item: item["field"])
    preserved.sort()
    return after, cleared, preserved


def apply_events(
    events: list[dict[str, Any]],
    *,
    known_event_ids: set[str] | None = None,
    initial_projection: Mapping[str, dict[str, Any]] | None = None,
    upsert_mode: str = DEFAULT_UPSERT_MODE,
) -> dict[str, Any]:
    """Apply ``events`` in seq order on top of ``initial_projection``.

    ``initial_projection`` maps entity keys to rows shaped like projection
    rows (``entity_key``, ``payload``, ``applied_seq``, ``applied_event_id``,
    ``deleted``).  Passing the persisted projection is what makes patch mode
    preserve values across batches.
    """

    if upsert_mode not in UPSERT_MODES:
        raise ValueError(f"unsupported upsert_mode: {upsert_mode}")

    known = set(known_event_ids or ())
    projection = _copy_projection(initial_projection)
    applied_log: list[dict[str, Any]] = []
    counts = {
        "source_events": len(events),
        "applied_upsert": 0,
        "applied_delete": 0,
        "skipped_duplicate": 0,
        "max_seq": 0,
    }

    ordered = sorted(events, key=lambda e: (int(e.get("seq", 0)), str(e.get("event_id", ""))))

    for ev in ordered:
        event_id = str(ev["event_id"])
        entity_key = str(ev["entity_key"])
        op = str(ev.get("op", "upsert"))
        seq = int(ev.get("seq", 0))
        event_time = str(ev.get("event_time", ""))
        payload = ev.get("payload")
        counts["max_seq"] = max(counts["max_seq"], seq)

        existing = projection.get(entity_key)
        before_payload = (
            existing.get("payload") if existing and not existing.get("deleted") else None
        )

        if event_id in known:
            counts["skipped_duplicate"] += 1
            applied_log.append(
                {
                    "event_id": event_id,
                    "entity_key": entity_key,
                    "op": op,
                    "seq": seq,
                    "event_time": event_time,
                    "result": "skipped_duplicate",
                    "merge_mode": upsert_mode,
                    "payload": payload,
                    "before_payload": copy.deepcopy(before_payload),
                    "after_payload": copy.deepcopy(before_payload),
                    "cleared_fields": [],
                    "preserved_fields": [],
                }
            )
            continue

        known.add(event_id)

        if op == "delete":
            projection[entity_key] = {
                "entity_key": entity_key,
                "payload": None,
                "applied_seq": seq,
                "applied_event_id": event_id,
                "deleted": True,
            }
            counts["applied_delete"] += 1
            result = "applied_delete"
            after_payload = None
            cleared: list[dict[str, str]] = []
            preserved: list[str] = []
        else:
            after_payload, cleared, preserved = merge_payload(upsert_mode, before_payload, payload)
            projection[entity_key] = {
                "entity_key": entity_key,
                "payload": after_payload,
                "applied_seq": seq,
                "applied_event_id": event_id,
                "deleted": False,
            }
            counts["applied_upsert"] += 1
            result = "applied_upsert"

        applied_log.append(
            {
                "event_id": event_id,
                "entity_key": entity_key,
                "op": op,
                "seq": seq,
                "event_time": event_time,
                "result": result,
                "merge_mode": upsert_mode,
                "payload": payload,
                "before_payload": copy.deepcopy(before_payload),
                "after_payload": copy.deepcopy(after_payload),
                "cleared_fields": cleared,
                "preserved_fields": preserved,
            }
        )

    alive = [r for r in projection.values() if not r["deleted"]]
    deleted = [r for r in projection.values() if r["deleted"]]
    counts["projected_alive"] = len(alive)
    counts["projected_deleted"] = len(deleted)
    return {
        "counts": counts,
        "projection": list(projection.values()),
        "applied_log": applied_log,
    }
