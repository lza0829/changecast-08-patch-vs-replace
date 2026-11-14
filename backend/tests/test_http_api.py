from fastapi.testclient import TestClient

from app.main import app


def _client():
    return TestClient(app, raise_server_exceptions=True)


def _stream_id(code):
    data = _client().get("/api/streams").json()
    return next(s["id"] for s in data if s["code"] == code)


def test_policy_endpoints_and_http_flow(fresh_db):
    sid = _stream_id("member_profile")
    with _client() as c:
        policy = c.get(f"/api/streams/{sid}/policy").json()
        assert policy["upsert_mode"] == "patch"
        assert policy["has_running_batch"] is False

        run = c.post("/api/project", json={"stream_id": sid}).json()
        assert run["upsert_mode"] == "patch"
        assert run["status"] == "completed"

        switch = c.put(
            f"/api/streams/{sid}/policy", json={"upsert_mode": "replace", "note": "http 测试"}
        ).json()
        assert switch["changed"] is True
        assert switch["effective_after_batch_id"] == run["id"]

        run2 = c.post("/api/project", json={"stream_id": sid}).json()
        assert run2["upsert_mode"] == "replace"

        history = c.get(f"/api/streams/{sid}/policy").json()["mode_changes"]
        assert history[0]["from_mode"] == "patch"
        assert history[0]["to_mode"] == "replace"

        bad = c.put(f"/api/streams/{sid}/policy", json={"upsert_mode": "merge"})
        assert bad.status_code == 400

        compare = c.get(f"/api/streams/{sid}/merge-compare").json()
        assert set(compare["modes"]) == {"patch", "replace"}


def test_stream_list_marks_one_mode_only(fresh_db):
    with _client() as c:
        streams = c.get("/api/streams").json()
    for s in streams:
        assert s["upsert_mode"] in {"patch", "replace"}
        assert "upsert_mode_label" in s
