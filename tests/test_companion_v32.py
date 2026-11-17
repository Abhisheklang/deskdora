"""v3.2 companion + overlay surface: pause/resume/reset controls,
live progress payload, planner display names, screen-share auth and
toggle, companion session cookie, MCP info, overlay state mapping."""
import time

import pytest
from fastapi.testclient import TestClient

from deskdora import server
from deskdora.audit import RunLog
from deskdora.overlay import overlay_state
from deskdora.safety import KillSwitch, PauseGate

client = TestClient(server.app)


@pytest.fixture(autouse=True)
def clean_manager():
    yield
    server.MANAGER.pause_gate.clear()
    server.MANAGER.queue.resume()
    if not server.MANAGER.state["running"]:
        try:
            server.MANAGER.reset_state()
        except Exception:
            pass


# -- planner display ---------------------------------------------------
def test_planner_display_names():
    assert server.planner_display("builtin") == "Built-in"
    assert server.planner_display("ollama:qwen2.5:1.5b") == \
        "Ollama · qwen2.5:1.5b"
    assert server.planner_display("anthropic:claude-sonnet-4-5") == \
        "Anthropic Claude · claude-sonnet-4-5"
    assert server.planner_display("") == ""


# -- progress payload ----------------------------------------------------
def test_status_progress_shape():
    body = client.get("/api/status").json()
    prog = body["progress"]
    for key in ("goal", "planner_mode", "planner_display", "running",
                "paused", "step_index", "step_total", "steps_done",
                "percent", "current_action", "queue_position",
                "queue_total"):
        assert key in prog, key
    assert body["screen_share"] == {"enabled": False, "viewers": 0}


# -- pause / resume / reset ----------------------------------------------
def test_pause_resume_endpoints():
    assert client.post("/api/pause").json()["paused"] is True
    assert server.MANAGER.pause_gate.paused
    assert server.MANAGER.queue.snapshot()["paused"] is True
    assert client.post("/api/resume").json()["paused"] is False
    assert not server.MANAGER.pause_gate.paused


def test_reset_clears_finished_run_and_refuses_while_running():
    mgr = server.MANAGER
    mgr.state.update({"running": False, "goal": "Old task",
                      "result": {"status": "killed"}})
    assert client.post("/api/reset").json()["ok"] is True
    assert mgr.state["goal"] == "" and mgr.state["result"] is None
    mgr.state["running"] = True
    try:
        assert client.post("/api/reset").status_code == 409
    finally:
        mgr.state["running"] = False


def test_pause_gate_kill_during_wait_aborts():
    gate = PauseGate()
    kill = KillSwitch(stop_file="/tmp/dd-test-stop-pause")
    kill.reset()
    gate.request()
    result = {}

    def waiter():
        result["ok"] = gate.wait(kill, poll=0.02)

    import threading
    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.1)
    assert t.is_alive()          # still frozen at the boundary
    kill.trip()                  # kill lands while paused
    t.join(timeout=2)
    assert result["ok"] is False  # aborts; never resumes
    kill.reset()


def test_pause_gate_resume_releases():
    gate = PauseGate()
    kill = KillSwitch(stop_file="/tmp/dd-test-stop-pause2")
    kill.reset()
    gate.request()
    import threading
    t = threading.Thread(
        target=lambda: gate.wait(kill, poll=0.02))
    t.start()
    time.sleep(0.1)
    gate.clear()
    t.join(timeout=2)
    assert not t.is_alive()


# -- history carries the planner display ---------------------------------
def test_runs_list_has_planner_display(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path))
    log = RunLog(str(tmp_path), "Tidy the folder", "builtin")
    log.finish("success", answer="done")
    runs = client.get("/api/runs").json()
    assert runs and runs[0]["planner_display"] == "Built-in"
    assert runs[0]["source"] == "ui"
    detail = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert detail["planner_display"] == "Built-in"


# -- MCP info ---------------------------------------------------------------
def test_mcp_info_shape():
    body = client.get("/api/mcp").json()
    assert body["command"] == "deskdora-mcp"
    assert body["transport"] == "stdio"
    assert "available" in body
    assert body["config"]["mcpServers"]["deskdora"]["command"] == \
        "deskdora-mcp"


# -- screen share -------------------------------------------------------------
@pytest.fixture()
def share_cfg(monkeypatch):
    cfg = dict(server.DEFAULT_CONFIG)
    cfg.update({"companion_pin": "2468", "screen_share": False})
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr(server.MANAGER, "frame_provider",
                        lambda: (b"\xff\xd8\xff\xe0fakejpeg",
                                 "image/jpeg"))
    return cfg


def test_screen_stream_requires_auth(share_cfg):
    share_cfg["screen_share"] = True
    assert client.get("/api/screen.mjpg").status_code == 401
    assert client.get("/api/screen.mjpg",
                      headers={"x-companion-pin": "0000"}
                      ).status_code == 401


def test_screen_stream_refused_when_off(share_cfg):
    assert client.get("/api/screen.mjpg",
                      headers={"x-companion-pin": "2468"}
                      ).status_code == 403


def test_screen_stream_serves_frames_when_on(share_cfg):
    share_cfg["screen_share"] = True
    with client.stream("GET", "/api/screen.mjpg",
                       headers={"x-companion-pin": "2468"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith(
            "multipart/x-mixed-replace")
        chunk = next(resp.iter_bytes())
        assert b"--frame" in chunk
        assert b"image/jpeg" in chunk
        assert b"fakejpeg" in chunk
    share_cfg["screen_share"] = False


def test_companion_auth_cookie_unlocks_stream(share_cfg):
    share_cfg["screen_share"] = True
    bad = client.post("/api/companion/auth",
                      headers={"x-companion-pin": "0000"})
    assert bad.status_code == 401
    ok = client.post("/api/companion/auth",
                     headers={"x-companion-pin": "2468"})
    assert ok.status_code == 200
    assert "dd_companion_session" in ok.headers.get("set-cookie", "")
    with client.stream("GET", "/api/screen.mjpg") as resp:
        assert resp.status_code == 200  # cookie carried by the client
    share_cfg["screen_share"] = False


# -- overlay state mapping ----------------------------------------------------
def _status(state=None, progress=None):
    base_p = {"goal": "", "planner_display": "", "paused": False,
              "step_total": 0, "steps_done": 0, "percent": 0,
              "current_action": "", "queue_total": 0,
              "queue_halted": False, "queue_paused": False}
    base_p.update(progress or {})
    return {"state": {"running": False, "result": None, "goal": "",
                      **(state or {})},
            "progress": base_p}


def test_overlay_idle_hidden():
    assert overlay_state(_status())["visible"] is False
    assert overlay_state(None)["visible"] is False


def test_overlay_running_shows_percent_and_freeze():
    st = overlay_state(_status(
        state={"running": True},
        progress={"goal": "Organise folder", "step_total": 10,
                  "steps_done": 4, "percent": 40,
                  "current_action": "click Save"}))
    assert st["visible"] and st["mode"] == "running"
    assert st["percent"] == 40 and st["steps_text"] == "STEP 4/10"
    assert st["can_freeze"] and st["can_stop"] and not st["can_resume"]


def test_overlay_paused_offers_resume_not_freeze():
    st = overlay_state(_status(
        state={"running": True},
        progress={"paused": True, "step_total": 3, "steps_done": 1}))
    assert st["mode"] == "paused"
    assert st["can_resume"] and not st["can_freeze"] and st["can_stop"]


def test_overlay_stopped_offers_reset_not_resume():
    st = overlay_state(_status(
        state={"result": {"status": "killed"}}))
    assert st["mode"] == "finished"
    assert st["can_reset"] and not st["can_resume"]
    assert st["status_text"] == "KILLED"


# -- edge light ------------------------------------------------------------
from deskdora.overlay import edge_state  # noqa: E402


def test_edge_light_mapping():
    running = _status(state={"running": True})
    assert edge_state(running, "single")["mode"] == "lime"
    assert edge_state(running, "flow")["visible"] is True
    assert edge_state(running, "off")["visible"] is False
    assert edge_state(running, "bogus")["visible"] is False

    confirming = _status(state={"running": True})
    confirming["pending_confirmation"] = {"id": "x"}
    st = edge_state(confirming, "single")
    assert st["mode"] == "amber-fast" and st["animated"] is True

    paused = _status(state={"running": True},
                     progress={"paused": True})
    st = edge_state(paused, "double")
    assert st["mode"] == "amber-steady" and st["animated"] is False

    finished = _status(state={"result": {"status": "success"}})
    assert edge_state(finished, "single")["visible"] is False
    assert edge_state(_status(), "single")["visible"] is False
    assert edge_state(None, "single")["visible"] is False


def test_edge_light_setting_persists(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(server, "CONFIG_PATH", str(cfg_file))
    assert server.load_config()["edge_light"] == "single"  # default
    body = client.post("/api/config",
                       json={"edge_light": "flow"}).json()
    assert body["edge_light"] == "flow"
    assert server.load_config()["edge_light"] == "flow"
