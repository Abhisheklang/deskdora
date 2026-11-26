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
    res = client.delete("/api/routines/tidy?confirm=true")
    assert res.status_code == 200


def test_run_rejects_empty_goal():
    res = client.post("/api/run", json={"goal": "   "})
    assert res.status_code == 400


def test_queue_api_roundtrip():
    from deskdora.queue import TaskQueue

    class IdleRunner:
        def busy(self):
            return True      # desktop "busy": items stay queued

        def start(self, goal, planner):
            raise AssertionError("must not start while busy")

        def join(self):
            return {"status": "success"}

        def kill_tripped(self):
            return False

    from deskdora import server
    original = server.MANAGER.queue
    server.MANAGER.queue = TaskQueue(IdleRunner())
    try:
        res = client.post("/api/queue", json={"goal": "  "})
        assert res.status_code == 400
        res = client.post("/api/queue",
                          json={"goal": "Tidy the folder",
                                "planner": "builtin"})
        assert res.status_code == 200
        item_id = res.json()["id"]
        res = client.get("/api/queue")
        body = res.json()
        assert body["queued"] == 1
        assert body["items"][0]["goal"] == "Tidy the folder"
        res = client.post("/api/queue/pause")
        assert res.json()["paused"] is True
        res = client.post("/api/queue/resume")
        assert res.json()["paused"] is False
        res = client.delete(f"/api/queue/{item_id}")
        assert res.status_code == 200
        res = client.delete(f"/api/queue/{item_id}")
        assert res.status_code == 404
    finally:
        server.MANAGER.queue = original


def test_run_delete(tmp_path, monkeypatch):
    from deskdora import server
    run_dir = tmp_path / "20990101-000000-fake-run"
    run_dir.mkdir()
    (run_dir / "log.jsonl").write_text("{}\n")
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path))
    res = client.delete("/api/runs/20990101-000000-fake-run?confirm=true")
    assert res.status_code == 200
    assert not run_dir.exists()
    res = client.delete("/api/runs/20990101-000000-fake-run?confirm=true")
    assert res.status_code == 404
    res = client.delete("/api/runs/..%2F..%2Fetc")
    assert res.status_code in (404, 405)


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


def test_memory_api(tmp_path, monkeypatch):
    from deskdora import server
    from deskdora.memory import MemoryStore
    monkeypatch.setattr(server.MANAGER, "memory",
                        MemoryStore(str(tmp_path / "memory.json")))
    res = client.post("/api/memory/places",
                      json={"alias": "invoices", "path": "/tmp"})
    assert res.status_code == 200
    assert res.json()["exists"] is True
    body = client.get("/api/memory").json()
    assert body["places"]["invoices"]["path"] == "/tmp"
    res = client.post("/api/memory/forget",
                      json={"kind": "places", "key": "invoices"})
    assert res.status_code == 200
    assert client.get("/api/memory").json()["places"] == {}
    res = client.post("/api/memory/forget",
                      json={"kind": "places", "key": "invoices"})
    assert res.status_code == 404


def test_voice_api():
    body = client.get("/api/voice").json()
    assert "tts" in body and "stt" in body
    res = client.post("/api/voice/render",
                      json={"text": "DeskDora test render."})
    if res.status_code == 200:
        assert res.json()["bytes"] > 1000


def test_scheduler_events_api():
    res = client.get("/api/scheduler/events")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_routine_trigger_validation():
    res = client.post("/api/routines", json={
        "name": "bad-trigger", "goal": "goal",
        "trigger": {"type": "telepathy"}})
    assert res.status_code == 400
    res = client.post("/api/routines", json={
        "name": "folder-watcher", "goal": "goal",
        "trigger": {"type": "folder_change", "path": "/tmp"}})
    assert res.status_code == 200
    assert res.json()["trigger"]["type"] == "folder_change"
    client.delete("/api/routines/folder-watcher?confirm=true")


def test_companion_pin_guard():
    # Non-loopback mutating calls require the companion PIN; the
    # TestClient can present an arbitrary client host, which is the
    # same ASGI-level fact the middleware checks in production.
    from fastapi.testclient import TestClient
    from deskdora import server
    server.save_config({**server.load_config(), "companion_pin": "246810"})
    try:
        remote = TestClient(server.app, client=("192.168.1.50", 50000))
        assert remote.post("/api/kill").status_code == 401
        assert remote.post(
            "/api/kill",
            headers={"X-Companion-Pin": "000000"}).status_code == 401
        assert remote.post(
            "/api/kill",
            headers={"X-Companion-Pin": "246810"}).status_code == 200
        # reads stay open for the companion's status view
        assert remote.get("/api/status").status_code == 200
        # loopback (the owner at the machine) is never challenged
        local = TestClient(server.app, client=("127.0.0.1", 50000))
        assert local.post("/api/kill").status_code == 200
    finally:
        cfg = server.load_config()
        cfg["companion_pin"] = ""
        server.save_config(cfg)


def test_config_rejects_money_free():
    res = client.post("/api/config", json={"autonomy": {"money": "free"}})
    assert res.status_code == 400
    res = client.post("/api/config", json={"autonomy": {"money": "never"}})
    assert res.status_code == 200
    res = client.post("/api/config", json={"autonomy": {
        "files": "confirm", "apps": "confirm", "web": "confirm",
        "messages": "confirm", "money": "confirm"}})
    assert res.status_code == 200
