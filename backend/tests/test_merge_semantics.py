from app.engines.project import apply_events, merge_payload


def _ev(event_id, key, seq, payload, op="upsert"):
    return {
        "event_id": event_id,
        "entity_key": key,
        "op": op,
        "seq": seq,
        "event_time": "2026-01-01T00:00:00Z",
        "payload": payload,
    }


def test_patch_keeps_fields_absent_from_payload():
    events = [
        _ev("e1", "M1", 1, {"name": "林晓", "tier": "silver", "region": "east"}),
        _ev("e2", "M1", 2, {"tier": "gold"}),
    ]
    result = apply_events(events, upsert_mode="patch")
    row = result["projection"][0]
    assert row["payload"] == {"name": "林晓", "tier": "gold", "region": "east"}
    last = result["applied_log"][-1]
    assert last["preserved_fields"] == ["name", "region"]
    assert last["cleared_fields"] == []


def test_replace_clears_fields_absent_from_payload():
    events = [
        _ev("e1", "M1", 1, {"name": "林晓", "tier": "silver", "region": "east"}),
        _ev("e2", "M1", 2, {"tier": "gold"}),
    ]
    result = apply_events(events, upsert_mode="replace")
    row = result["projection"][0]
    assert row["payload"] == {"tier": "gold"}
    last = result["applied_log"][-1]
    assert last["cleared_fields"] == [
        {"field": "name", "reason": "missing"},
        {"field": "region", "reason": "missing"},
    ]


def test_explicit_null_differs_from_missing_under_replace():
    before = {"name": "林晓", "tier": "silver"}
    after_null, cleared_null, _ = merge_payload("replace", before, {"tier": None})
    after_missing, cleared_missing, _ = merge_payload("replace", before, {"name": "林晓"})

    assert after_null == {"tier": None}
    assert {c["field"]: c["reason"] for c in cleared_null} == {
        "name": "missing",
        "tier": "explicit_null",
    }
    assert {c["field"]: c["reason"] for c in cleared_missing} == {"tier": "missing"}
    assert after_null["tier"] is None
    assert after_missing == {"name": "林晓"}


def test_patch_writes_explicit_null_but_never_erases_missing_keys():
    after, cleared, preserved = merge_payload("patch", {"a": 1, "b": 2}, {"b": None})
    assert after == {"a": 1, "b": None}
    assert cleared == []
    assert preserved == ["a"]


def test_patch_does_not_resurrect_deleted_entity_fields():
    events = [
        _ev("e1", "M1", 1, {"a": 1, "b": 2}),
        _ev("e2", "M1", 2, None, op="delete"),
        _ev("e3", "M1", 3, {"b": 9}),
    ]
    result = apply_events(events, upsert_mode="patch")
    row = result["projection"][0]
    assert row["deleted"] is False
    assert row["payload"] == {"b": 9}


def test_same_events_same_final_state_regardless_of_mode_when_full_payload():
    events = [
        _ev("e1", "M1", 1, {"a": 1, "b": 2}),
        _ev("e2", "M1", 2, {"a": 3, "b": 4}),
    ]
    patch = apply_events(events, upsert_mode="patch")
    replace = apply_events(events, upsert_mode="replace")
    assert patch["projection"] == replace["projection"]
    assert patch["counts"] == replace["counts"]


def test_consecutive_patches_match_single_full_replace_terminal_state():
    # Same key: a chain of partial patches vs one replace carrying the merged row.
    patch_events = [
        _ev("p1", "M1", 1, {"a": 1}),
        _ev("p2", "M1", 2, {"b": 2}),
        _ev("p3", "M1", 3, {"c": 3}),
    ]
    replace_events = [_ev("r1", "M1", 3, {"a": 1, "b": 2, "c": 3})]

    patch = apply_events(patch_events, upsert_mode="patch")
    replace = apply_events(replace_events, upsert_mode="replace")

    assert patch["projection"][0]["payload"] == replace["projection"][0]["payload"]
    # applied_seq is the last applied event's seq under either strategy.
    assert patch["projection"][0]["applied_seq"] == 3
    assert replace["projection"][0]["applied_seq"] == 3
    assert patch["counts"]["applied_upsert"] == 3
    assert replace["counts"]["applied_upsert"] == 1
    assert patch["counts"]["projected_alive"] == replace["counts"]["projected_alive"] == 1



def test_unknown_mode_rejected():
    try:
        apply_events([], upsert_mode="merge")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown upsert_mode")
