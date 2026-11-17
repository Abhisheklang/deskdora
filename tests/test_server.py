import pytest
from fastapi.testclient import TestClient

from deskdora.benchmark import benchmark_tasks
from deskdora.server import app

client = TestClient(app)


def test_benchmark_has_25_unique_tasks():
    tasks = benchmark_tasks()
    assert len(tasks) == 25
    assert len({t.id for t in tasks}) == 25


def test_status_shape():
    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert "state" in body and "config" in body
    assert body["state"]["running"] is False


def test_config_roundtrip():
    res = client.post("/api/config", json={"planner": "builtin"})
    assert res.status_code == 200
    assert res.json()["planner"] == "builtin"
    res = client.post("/api/config", json={"planner": "auto"})
    assert res.json()["planner"] == "auto"


def test_routines_api(tmp_path, monkeypatch):
    from deskdora.routines import RoutineStore
    from deskdora import server
    monkeypatch.setattr(server.MANAGER, "routines",
                        RoutineStore(str(tmp_path)))
    res = client.post("/api/routines",
                      json={"name": "Tidy", "goal": "Open terminal"})
    assert res.status_code == 200
    res = client.get("/api/routines")
    assert any(r["name"] == "tidy" for r in res.json())
    res = client.delete("/api/routines/tidy")
    assert res.status_code == 200


def test_run_rejects_empty_goal():
    res = client.post("/api/run", json={"goal": "   "})
    assert res.status_code == 400


def test_confirmation_404():
    res = client.post("/api/confirmations/nope", json={"approved": True})
    assert res.status_code == 404


def test_ui_served():
    res = client.get("/")
    assert res.status_code == 200
    assert "DeskDora" in res.text
    assert "KILL SWITCH" in res.text


def test_live_view_removed():
    # The live view was removed at the user's order: the endpoint is
    # gone, the status state carries no live-view fields, and the UI
    # ships no live-view markup or polling code.
    res = client.get("/api/live/screenshot")
    assert res.status_code == 404
    state = client.get("/api/status").json()["state"]
    assert "last_target" not in state
    assert "last_screenshot" not in state
    ui = client.get("/").text
    assert "Live view" not in ui
    assert "liveImg" not in ui
    assert "/api/live/screenshot" not in ui
