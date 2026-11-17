"""v3.3 server flows: dry-run plan approval end to end (stub
planner + fake driver), feature toggles refusing ask/undo, and
the plan-approval endpoint's contract."""
import time

import pytest
from fastapi.testclient import TestClient

from deskdora import server
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Observation

client = TestClient(server.app)


class StubPlanner:
    name = "builtin"

    def plan(self, goal, observation, history=None):
        return [{"action": "wait", "seconds": 0.01},
                {"action": "wait", "seconds": 0.01},
                {"action": "done", "answer": "stub done"}]


class StubDriver(DesktopDriver):
    id = "stub"
    implemented = frozenset()

    def observe(self):
        return Observation(elements=[], windows=[], texts={})

    def refind(self, element):
        return element

    def click(self, x, y):
        pass

    def double_click(self, x, y):
        pass

    def type_text(self, text):
        pass

    def key(self, combo):
        pass

    def scroll(self, x, y, direction="down"):
        pass

    def launch(self, command):
        return command

    def screenshot(self, path):
        return path

    def read_clipboard(self):
        return ""

    def active_window_title(self):
        return ""

    def activate_window(self, title):
        return True


@pytest.fixture()
def stubbed(monkeypatch):
    monkeypatch.setattr(server, "get_planner",
                        lambda *a, **k: (StubPlanner(), "builtin"))
    import deskdora.drivers as drivers
    monkeypatch.setattr(drivers, "get_driver",
                        lambda name=None: StubDriver())
    conf = dict(server.DEFAULT_CONFIG)
    conf.update({"planner": "builtin", "providers": {},
                 "custom_providers": []})
    monkeypatch.setattr(server, "load_config", lambda: conf)
    return conf


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_dry_run_waits_for_approval_and_skips(stubbed):
    res = client.post("/api/run", json={"goal": "stub goal",
                                        "dry_run": True})
    assert res.json()["ok"] is True
    assert _wait_for(
        lambda: client.get("/api/status").json()["state"]
        ["pending_plan"])
    plan = client.get("/api/status").json()["state"]["pending_plan"]
    assert plan["count"] == 3
    assert plan["planner"] == "builtin"
    assert "Wait" in plan["steps"][0]
    res = client.post("/api/runs/approve-plan",
                      json={"approved": True, "skip": [1]})
    assert res.json()["ok"] is True
    assert _wait_for(
        lambda: not client.get("/api/status").json()["state"]
        ["running"])
    st = client.get("/api/status").json()["state"]
    assert st["result"]["status"] == "success"
    # the skipped step is in the audit log as skipped-by-user
    run_id = st["run_dir"].rstrip("/").split("/")[-1]
    events = client.get(f"/api/runs/{run_id}").json()["events"]
    skipped = [e for e in events
               if e["kind"] == "step_result" and e.get("skipped")]
    assert len(skipped) == 1 and skipped[0]["index"] == 1


def test_dry_run_discard_kills_cleanly(stubbed):
    client.post("/api/run", json={"goal": "stub goal 2",
                                  "dry_run": True})
    assert _wait_for(
        lambda: client.get("/api/status").json()["state"]
        ["pending_plan"])
    client.post("/api/runs/approve-plan",
                json={"approved": False, "skip": []})
    assert _wait_for(
        lambda: not client.get("/api/status").json()["state"]
        ["running"])
    st = client.get("/api/status").json()["state"]
    assert st["result"]["status"] == "killed"
    assert "not approved" in st["result"]["reason"]


def test_approve_plan_without_pending_is_404(stubbed):
    res = client.post("/api/runs/approve-plan",
                      json={"approved": True, "skip": []})
    assert res.status_code == 404


def test_ask_refused_when_feature_off(stubbed):
    stubbed["feature_ask_screen"] = False
    res = client.post("/api/ask", json={"question": "what is open?"})
    assert res.status_code == 403


def test_undo_refused_when_feature_off(stubbed, tmp_path):
    stubbed["feature_undo"] = False
    res = client.post("/api/runs/whatever/undo")
    assert res.status_code == 403


def test_v33_config_defaults():
    cfg = server.DEFAULT_CONFIG
    assert cfg["dry_run_default"] is False
    assert cfg["self_recovery"] is True
    assert cfg["feature_ask_screen"] is True
    assert cfg["feature_app_permissions"] is True
    assert cfg["feature_undo"] is True
    assert cfg["app_deny_list"] == []
    assert cfg["sounds"]["enabled"] is True
