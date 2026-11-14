import pytest

from app.db import connect
from app.services import project_service as svc


def _member_id():
    for s in svc.list_streams():
        if s["code"] == "member_profile":
            return s["id"]
    raise AssertionError("member_profile stream missing")


def _store_id():
    for s in svc.list_streams():
        if s["code"] == "store_archive":
            return s["id"]
    raise AssertionError("store_archive stream missing")


def _payload(stream_id, entity_key):
    for row in svc.list_projections(stream_id=stream_id, include_deleted=False):
        if row["entity_key"] == entity_key:
            return row["payload"], row
    return None, None


def test_patch_stream_preserves_untouched_fields(fresh_db):
    sid = _member_id()
    assert svc.get_stream_policy(sid)["upsert_mode"] == "patch"
    svc.run_project(sid)

    payload, row = _payload(sid, "M1001")
    # Fixture: mp-007 sends only {"tier": "platinum"}; region/display_name survive.
    assert payload == {"display_name": "林晓", "tier": "platinum", "region": "east"}
    assert row["applied_seq"] == 7

    events = svc.list_events(stream_id=sid)
    last = [e for e in events if e["event_id"] == "mp-007"][0]
    assert last["merge_mode"] == "patch"
    assert set(last["preserved_fields"]) == {"display_name", "region"}
    assert last["cleared_fields"] == []


def test_replace_stream_clears_absent_fields(fresh_db):
    sid = _store_id()
    assert svc.get_stream_policy(sid)["upsert_mode"] == "replace"
    svc.run_project(sid)

    payload, row = _payload(sid, "S01")
    # sa-006 sends only {"status": "renovating"}; name/city are cleared.
    assert payload == {"status": "renovating"}
    assert row["applied_seq"] == 6

    events = svc.list_events(stream_id=sid)
    last = [e for e in events if e["event_id"] == "sa-006"][0]
    assert {c["field"]: c["reason"] for c in last["cleared_fields"]} == {
        "name": "missing",
        "city": "missing",
    }


def test_batch_snapshot_records_mode_and_counts_agree(fresh_db):
    sid = _store_id()
    batch = svc.run_project(sid)
    assert batch["upsert_mode"] == "replace"
    assert batch["status"] == "completed"
    assert batch["applied_upsert"] == 6
    assert batch["applied_delete"] == 0
    assert batch["max_seq"] == 6

    alive = svc.list_projections(stream_id=sid, include_deleted=False)
    deleted = svc.list_projections(stream_id=sid, include_deleted=True)
    assert len(alive) == batch["projected_alive"] == 3
    assert len(deleted) == batch["projected_alive"] + batch["projected_deleted"]


def test_mode_change_applies_to_next_batch_not_history(fresh_db):
    sid = _member_id()
    first = svc.run_project(sid)
    assert first["upsert_mode"] == "patch"

    change = svc.change_upsert_mode(sid, "replace", note="演练整行替换")
    assert change["changed"] is True
    assert change["effective_after_batch_id"] == first["id"]

    second = svc.run_project(sid)
    assert second["upsert_mode"] == "replace"

    # Historical batch口径保持不变。
    batches = {b["id"]: b for b in svc.list_batches(limit=10)}
    assert batches[first["id"]]["upsert_mode"] == "patch"
    assert batches[first["id"]]["applied_upsert"] == first["applied_upsert"]
    assert batches[second["id"]]["upsert_mode"] == "replace"

    # Under replace, M1001 loses display_name/region at mp-007.
    payload, _ = _payload(sid, "M1001")
    assert payload == {"tier": "platinum"}

    # Historical event traces keep their own merge mode.
    old_trace = svc.list_events(batch_id=first["id"])
    assert {e["merge_mode"] for e in old_trace} == {"patch"}
    new_trace = svc.list_events(batch_id=second["id"])
    assert {e["merge_mode"] for e in new_trace} == {"replace"}


def test_policy_change_refused_while_batch_running(fresh_db):
    sid = _member_id()
    conn = connect()
    try:
        conn.execute(
            """
            INSERT INTO project_batches(
                batch_no, stream_id, status, source_events, applied_upsert,
                applied_delete, skipped_duplicate, projected_alive,
                projected_deleted, max_seq, note, created_at, upsert_mode
            ) VALUES ('PB-RUNNING', ?, 'running', 0,0,0,0,0,0,0,NULL,'2026-01-01T00:00:00Z','patch')
            """,
            (sid,),
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError):
        svc.change_upsert_mode(sid, "replace")
    with pytest.raises(ValueError):
        svc.run_project(sid)

    conn = connect()
    try:
        conn.execute("DELETE FROM project_batches WHERE batch_no='PB-RUNNING'")
        conn.commit()
    finally:
        conn.close()

    assert svc.change_upsert_mode(sid, "replace")["changed"] is True


def test_merge_compare_shows_both_outcomes_from_same_snapshot(fresh_db):
    sid = _member_id()
    report = svc.merge_compare(sid)
    assert report["current_mode"] == "patch"
    entities = {e["entity_key"]: e for e in report["entities"]}
    m1001 = entities["M1001"]
    assert m1001["diverges"] is True
    assert m1001["patch"]["payload"] == {
        "display_name": "林晓",
        "tier": "platinum",
        "region": "east",
    }
    assert m1001["replace"]["payload"] == {"tier": "platinum"}
    diffs = {d["field"]: d for d in m1001["field_diffs"]}
    assert diffs["display_name"]["patch_present"] is True
    assert diffs["display_name"]["replace_present"] is False

    # Compare is read-only.
    assert svc.list_batches(limit=5) == []
    assert svc.list_projections(stream_id=sid) == []


def test_patch_mode_never_blank_on_missing_key_even_across_batches(fresh_db):
    sid = _member_id()
    svc.run_project(sid)
    before, _ = _payload(sid, "M1003")
    svc.run_project(sid)
    after, _ = _payload(sid, "M1003")
    # mp-008 explicitly nulls region; that is a write, not a missing-key erase.
    assert after["region"] is None
    assert after["display_name"] == before["display_name"]
    assert after["tier"] == "gold"
