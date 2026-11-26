"""Regression tests for the BugHunter audit fixes (2026-11-26).

One section per confirmed bug, in audit order.  PINs used here are
6+ characters so they stay valid under the C5 minimum-length rule.
"""
import pytest
from fastapi.testclient import TestClient

from deskdora import server

PIN = "654321"


@pytest.fixture()
def pin_set():
    """Set a known companion PIN through the config file, restore
    the previous value afterwards."""
    original = server.load_config().get("companion_pin", "")
    server.save_config({**server.load_config(), "companion_pin": PIN})
    yield PIN
    server.save_config({**server.load_config(), "companion_pin": original})


def _remote():
    return TestClient(server.app, client=("192.168.1.50", 50000))


def _local():
    return TestClient(server.app, client=("127.0.0.1", 50000))


# ---------------------------------------------------------------- C1
# The companion PIN must never appear in an API response.

def test_c1_config_and_status_hide_pin_for_remote_and_loopback(pin_set):
    # Loopback: both endpoints answer, scrubbed.
    for path in ("/api/config", "/api/status"):
        res = _local().get(path)
        assert res.status_code == 200
        assert PIN not in res.text, path
        body = res.json()
        cfg = body["config"] if path == "/api/status" else body
        assert "companion_pin" not in cfg
        assert cfg["has_pin"] is True
    # Remote: status answers scrubbed; config is behind companion
    # auth since the C4 fix (401 without credentials) and scrubbed
    # when authenticated.  Either way the PIN never appears.
    res = _remote().get("/api/status")
    assert res.status_code == 200
    assert PIN not in res.text
    assert res.json()["config"]["has_pin"] is True
    res = _remote().get("/api/config")
    assert PIN not in res.text
    res = _remote().get("/api/config", headers={"x-companion-pin": PIN})
    assert res.status_code == 200
    assert PIN not in res.text
    assert "companion_pin" not in res.json()


def test_c1_pin_still_settable_and_blank_keeps_current(pin_set):
    local = _local()
    # Setting a new PIN via POST still works.
    res = local.post("/api/config", json={"companion_pin": "987654"})
    assert res.status_code == 200
    assert server.load_config()["companion_pin"] == "987654"
    assert "987654" not in res.text
    assert res.json()["has_pin"] is True
    # A blank PIN field on save means keep the current PIN.
    res = local.post("/api/config", json={"companion_pin": ""})
    assert res.status_code == 200
    assert server.load_config()["companion_pin"] == "987654"


# ---------------------------------------------------------------- C2
# Clicks with no resolvable name (element index, coordinates) must
# pass the mandatory confirmation gate.

def test_c2_unnamed_clicks_are_destructive():
    from deskdora.safety import classify_step
    assert classify_step({"action": "click", "element": 3}) == "destructive"
    assert classify_step({"action": "double_click",
                          "element": 3}) == "destructive"
    assert classify_step({"action": "click_at", "x": 10,
                          "y": 10}) == "destructive"
    assert classify_step({"action": "click",
                          "target": {"role": "push button"}}) == "destructive"


def test_c2_named_clicks_unchanged():
    from deskdora.safety import classify_step
    assert classify_step({"action": "click",
                          "target": {"name_contains": "Save"}}) == "safe"
    assert classify_step({"action": "click",
                          "target": {"label_contains": "Open"}}) == "safe"
    assert classify_step({"action": "click",
                          "target": {"name_contains": "Delete"}}) == \
        "destructive"


def test_c2_unnamed_clicks_require_confirmation_by_default():
    from deskdora.autonomy import evaluate
    for step in ({"action": "click", "element": 3},
                 {"action": "double_click", "element": 3},
                 {"action": "click_at", "x": 10, "y": 10}):
        decision, _category, significant = evaluate(None, step)
        assert decision == "confirm", step
        assert significant is True, step
    # A named safe click still runs without confirmation.
    decision, _category, significant = evaluate(
        None, {"action": "click", "target": {"name_contains": "Save"}})
    assert decision == "allow" and significant is False


def test_c2_gate_is_consulted_for_element_click():
    from deskdora.safety import ConfirmationGate, deny_all
    gate = ConfirmationGate(deny_all)
    assert gate.check({"action": "click", "element": 3},
                      "click element 3") is False
    assert gate.history and gate.history[0]["approved"] is False


# ---------------------------------------------------------------- C3
# Launch steps: confirmation by default, no shell, no metacharacters.

def test_c3_launch_requires_confirmation_by_default():
    from deskdora.autonomy import categorise_step, evaluate
    step = {"action": "launch", "command": "xfce4-terminal"}
    assert categorise_step(step) == ("apps", True)
    assert evaluate(None, step)[0] == "confirm"
    assert evaluate(None, {"action": "launch",
                           "command": "thunar /tmp"})[0] == "confirm"


def test_c3_validate_plan_rejects_shell_metacharacters():
    from deskdora.planners import PlanError, validate_plan
    bad = ["xterm -e rm; echo done",
           "gedit & rm file", "cat a | tee b", "echo `id`",
           "echo $(id)", "prog > /tmp/x", "prog < /tmp/x",
           "prog\nrm file", "a && b", "a || b"]
    for command in bad:
        with pytest.raises(PlanError):
            validate_plan([{"action": "launch", "command": command},
                           {"action": "done", "answer": "x"}])


def test_c3_normal_launch_still_validates():
    from deskdora.planners import validate_plan
    plan = [{"action": "launch", "command": "thunar /home/user/Downloads"},
            {"action": "done", "answer": "opened"}]
    assert validate_plan(plan) is plan
    plan2 = [{"action": "launch", "command": "gedit --new-window notes.txt"},
             {"action": "done", "answer": "opened"}]
    assert validate_plan(plan2) is plan2


def test_c3_launch_executes_argv_without_shell(monkeypatch):
    from deskdora import actions
    calls = {}

    def fake_popen(argv, **kw):
        calls["argv"] = argv
        calls["kw"] = kw

    monkeypatch.setattr(actions.subprocess, "Popen", fake_popen)
    actions.launch('thunar "/tmp/some dir"', wait=0)
    # shlex parsing: the quoted path stays one argument, no shell.
    assert calls["argv"] == ["thunar", "/tmp/some dir"]
    assert calls["kw"].get("shell") is False


# ---------------------------------------------------------------- C4
# Non-loopback GETs beyond the minimal status payload require
# companion authentication.

PROTECTED_GETS = ("/api/runs", "/api/memory", "/api/backup/export",
                  "/api/access", "/api/diagnostics", "/api/config")


def test_c4_remote_gets_require_auth(pin_set):
    remote = _remote()
    for path in PROTECTED_GETS:
        assert remote.get(path).status_code == 401, path
        assert remote.get(
            path, headers={"x-companion-pin": "000000"}
        ).status_code == 401, path


def test_c4_remote_gets_work_with_pin_header(pin_set):
    remote = _remote()
    # /api/diagnostics is excluded here: calling it live builds a
    # real AT-SPI driver, which aborts in this headless VM (no
    # existing test calls that endpoint live either).  Its 401
    # behaviour is covered above, where the guard fires first.
    for path in PROTECTED_GETS:
        if path == "/api/diagnostics":
            continue
        res = remote.get(path, headers={"x-companion-pin": PIN})
        assert res.status_code == 200, path


def test_c4_remote_run_detail_and_report_require_auth(pin_set):
    remote = _remote()
    assert remote.get("/api/runs/some-run").status_code == 401
    assert remote.get("/api/runs/some-run/report").status_code == 401
    assert remote.get(
        "/api/runs/some-run/screenshots/step0.png").status_code == 401


def test_c4_loopback_gets_stay_open(pin_set):
    local = _local()
    for path in PROTECTED_GETS:
        if path == "/api/diagnostics":
            continue  # see the note in the with-PIN test above
        assert local.get(path).status_code == 200, path


def test_c4_remote_status_stays_open_and_minimal(pin_set):
    res = _remote().get("/api/status")
    assert res.status_code == 200
    assert PIN not in res.text
