"""Kill-switch wedge fix (v3.4.1) — regression tests.

The defect (evidence: deskdora-deliverables/evidence/wedge-*):
the kill switch was only checked between steps, so a step that
never returned wedged the run forever — POST /api/kill tripped,
state.running stayed True, POST /api/reset was refused, and only
a server restart recovered.  A sibling defect in the same path:
RunLog.__init__ raising (audit disk failure) killed the worker
thread before any cleanup and stranded running=True the same way.

These tests are the proof standard for the fix: a real random
hang cannot be forced on demand in the live rig, so a synthetic
hang is injected at the driver — a step that never returns until
the test releases it — and the assertions cover the required
outcomes: the run ends within a bounded grace period, reset
works, the queue item is marked killed, and the released zombie
dispatches NO further desktop action (even though reset cleared
the shared kill switch before it woke — the per-run cancel event
is what fences it).
"""
import threading
import time

import pytest
from fastapi import HTTPException

from deskdora import server
from deskdora.agent import Agent
from deskdora.audit import RunLog
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Observation
from deskdora.safety import AllowList, ConfirmationGate, KillSwitch, approve_all


class FakeDriver(DesktopDriver):
    """Records every dispatch.  With block_first_key=True the first
    key() call hangs until the test sets .release — the synthetic
    wedge.  With cancel_on_key set, key() sets that event before
    returning — a force-clear landing mid-step, at agent level."""

    id = "fake"
    implemented = frozenset()

    def __init__(self, block_first_key=False, cancel_on_key=None):
        self.keys = []
        self.typed = []
        self.clicks = []
        self.launched = []
        self.block_first_key = block_first_key
        self.cancel_on_key = cancel_on_key
        self.entered = threading.Event()
        self.release = threading.Event()

    def observe(self):
        return Observation(elements=[], windows=[], texts={})

    def refind(self, element):
        return element

    def click(self, x, y):
        self.clicks.append((x, y))

    def double_click(self, x, y):
        self.clicks.append((x, y))

    def type_text(self, text):
        self.typed.append(text)

    def key(self, combo):
        self.keys.append(combo)
        if self.cancel_on_key is not None:
            self.cancel_on_key.set()
        if self.block_first_key and len(self.keys) == 1:
            self.entered.set()
            self.release.wait(timeout=60)  # the hang; test releases

    def scroll(self, x, y, direction="down"):
        pass

    def launch(self, command):
        self.launched.append(command)
        return command

    def screenshot(self, path):
        return path

    def read_clipboard(self):
        return ""

    def active_window_title(self):
        return ""

    def activate_window(self, title):
        return True


class StaticPlanner:
    """No .model attribute: behaves like the built-in planner
    (no replan) for validate_plan / execute_plan purposes."""

    def __init__(self, steps):
        self._steps = steps

    def plan(self, goal, observation, history=None):
        return [dict(s) for s in self._steps]


def wait_until(predicate, timeout=15.0, poll=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    # Tones resolve to nothing in this harness (v3.7.2 dispatch:
    # resolve_tone is the gating point emit_tone consults).
    monkeypatch.setattr(server.sounds, "resolve_tone",
                        lambda *a, **k: None)
    monkeypatch.setattr(server.notify, "notify", lambda *a, **k: None)
    mgr = server.RunManager()
    mgr.kill = KillSwitch(str(tmp_path / "STOP"))
    mgr.kill_grace_seconds = 0.6
    return mgr


def install(mgr, monkeypatch, driver, steps):
    planner = StaticPlanner(steps)
    monkeypatch.setattr(server, "get_planner",
                        lambda mode, **kw: (planner, "builtin"))
    monkeypatch.setattr("deskdora.drivers.get_driver",
                        lambda name=None: driver)
    return planner


KEY_PLAN = [{"action": "key", "key": "alt+Up"},
            {"action": "key", "key": "ctrl+x"},
            {"action": "done"}]


# ----------------------------------------------------------------------
# (a) the wedge itself: hung step + kill -> bounded force-clear
# ----------------------------------------------------------------------

def test_kill_force_clears_wedged_run(manager, monkeypatch):
    driver = FakeDriver(block_first_key=True)
    install(manager, monkeypatch, driver, KEY_PLAN)
    assert manager.start_run("organise the folder", "builtin")["ok"]
    assert driver.entered.wait(timeout=10), "step never started"

    t0 = time.time()
    manager.trip_kill()
    assert wait_until(lambda: not manager.state["running"],
                      timeout=10), "run was not force-cleared"
    elapsed = time.time() - t0
    assert elapsed < 5, f"force-clear took {elapsed:.1f}s (grace 0.6s)"
    result = manager.state["result"]
    assert result["status"] == "killed"
    assert result.get("force_cleared") is True

    # Reset works immediately — no server restart (the wedge's
    # signature failure was reset refused forever).
    assert manager.reset_state()["ok"] is True

    # Release the stuck step.  Reset has cleared the shared kill
    # switch, so only the per-run cancel event can fence the
    # zombie: it must unwind without dispatching the next step.
    driver.release.set()
    manager.thread.join(timeout=10)
    assert not manager.thread.is_alive(), "zombie worker never unwound"
    assert driver.keys == ["alt+Up"], \
        f"zombie dispatched after force-clear: {driver.keys}"
    assert driver.typed == [] and driver.clicks == [] \
        and driver.launched == []


def test_queue_item_marked_killed_after_force_clear(manager,
                                                    monkeypatch):
    driver = FakeDriver(block_first_key=True)
    install(manager, monkeypatch, driver, KEY_PLAN)
    manager.queue.start()
    try:
        item = manager.queue.add("organise the folder", "builtin")

        def item_status():
            snap = manager.queue.snapshot()
            found = [it for it in snap["items"] if it["id"] == item["id"]]
            return found[0]["status"] if found else None

        assert wait_until(lambda: item_status() == "running",
                          timeout=10)
        assert driver.entered.wait(timeout=10)
        manager.trip_kill()
        # join() must not hang on the wedged thread: the item is
        # marked from the recorded result once the watchdog fires.
        assert wait_until(lambda: item_status() == "killed",
                          timeout=15), \
            f"queue item stuck at {item_status()}"
        assert manager.queue.snapshot()["halted"] is True
    finally:
        driver.release.set()
        manager.queue.stop()


def test_reset_force_clears_killed_wedged_run(manager, monkeypatch):
    # Reset pressed instead of waiting for the watchdog: after the
    # grace period it force-clears rather than refusing forever.
    driver = FakeDriver(block_first_key=True)
    install(manager, monkeypatch, driver, KEY_PLAN)
    assert manager.start_run("organise the folder", "builtin")["ok"]
    assert driver.entered.wait(timeout=10)
    manager.trip_kill()
    t0 = time.time()
    assert manager.reset_state()["ok"] is True
    assert time.time() - t0 < 10
    assert manager.state["running"] is False
    assert manager.state["goal"] == ""
    driver.release.set()
    manager.thread.join(timeout=10)
    assert driver.keys == ["alt+Up"]


def test_reset_still_refused_for_live_unkilled_run(manager,
                                                   monkeypatch):
    # Reset is not a second stop button: a running run that was
    # NOT killed still refuses, exactly as before the fix.
    driver = FakeDriver(block_first_key=True)
    install(manager, monkeypatch, driver, KEY_PLAN)
    assert manager.start_run("organise the folder", "builtin")["ok"]
    assert driver.entered.wait(timeout=10)
    with pytest.raises(HTTPException) as excinfo:
        manager.reset_state()
    assert excinfo.value.status_code == 409
    manager.trip_kill()
    assert wait_until(lambda: not manager.state["running"],
                      timeout=10)
    driver.release.set()
    manager.thread.join(timeout=10)


def test_ordinary_kill_between_steps_needs_no_watchdog(manager,
                                                       monkeypatch):
    # No regression to the healthy kill path: a kill that lands
    # while the worker can notice it (here, inside a wait step)
    # ends the run promptly with the agent's own result — the
    # watchdog must not fire or mark it force-cleared.
    driver = FakeDriver()
    install(manager, monkeypatch, driver,
            [{"action": "wait", "seconds": 30}, {"action": "done"}])
    manager.kill_grace_seconds = 30.0  # watchdog far away
    assert manager.start_run("wait a while", "builtin")["ok"]
    assert wait_until(lambda: manager.agent is not None, timeout=10)
    time.sleep(0.3)
    manager.trip_kill()
    assert wait_until(lambda: not manager.state["running"],
                      timeout=10)
    result = manager.state["result"]
    assert result["status"] == "killed"
    assert result.get("force_cleared") is not True
    manager.thread.join(timeout=10)


# ----------------------------------------------------------------------
# (b) run-setup failures clean up via the finally path
# ----------------------------------------------------------------------

def test_runlog_setup_failure_clears_running(manager, monkeypatch):
    driver = FakeDriver()
    install(manager, monkeypatch, driver, [{"action": "done"}])
    real_runlog = server.RunLog

    class BoomLog:
        def __init__(self, *args, **kwargs):
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(server, "RunLog", BoomLog)
    assert manager.start_run("anything", "builtin")["ok"]
    assert wait_until(lambda: not manager.state["running"],
                      timeout=10), "setup failure stranded running=True"
    result = manager.state["result"]
    assert result["status"] == "failed"
    assert "PermissionError" in result["reason"]
    manager.thread.join(timeout=10)
    # Not stranded: reset works and a later run completes normally.
    assert manager.reset_state()["ok"] is True
    monkeypatch.setattr(server, "RunLog", real_runlog)
    assert manager.start_run("anything", "builtin")["ok"]
    assert wait_until(lambda: not manager.state["running"],
                      timeout=10)
    assert manager.state["result"]["status"] == "success"


def test_planner_setup_failure_clears_running(manager, monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr("deskdora.drivers.get_driver",
                        lambda name=None: driver)

    def boom(mode, **kw):
        raise RuntimeError("planner exploded")

    monkeypatch.setattr(server, "get_planner", boom)
    assert manager.start_run("anything", "builtin")["ok"]
    assert wait_until(lambda: not manager.state["running"],
                      timeout=10), "planner failure stranded running=True"
    result = manager.state["result"]
    assert result["status"] == "failed"
    assert "RuntimeError" in result["reason"]
    assert manager.reset_state()["ok"] is True


# ----------------------------------------------------------------------
# agent level: the per-run cancel event fences every dispatch
# ----------------------------------------------------------------------

def _agent(tmp_path, driver, cancel_event):
    return Agent(planner=None, planner_mode="builtin",
                 gate=ConfirmationGate(approve_all),
                 kill_switch=KillSwitch(str(tmp_path / "STOP")),
                 allow_list=AllowList(),
                 run_log=RunLog(str(tmp_path), "goal", "builtin"),
                 driver=driver, cancel_event=cancel_event,
                 recovery_enabled=True)


def test_agent_cancelled_before_start_dispatches_nothing(tmp_path):
    cancel = threading.Event()
    cancel.set()
    driver = FakeDriver()
    agent = _agent(tmp_path, driver, cancel)
    result = agent.execute_plan("goal", KEY_PLAN)
    assert result["status"] == "killed"
    assert driver.keys == []


def test_agent_cancelled_mid_step_dispatches_nothing_further(tmp_path):
    # The first key() lands the force-clear (sets the cancel
    # event) and then returns normally — the exact zombie wake-up.
    # The run must end there: no second key, no recovery attempt.
    cancel = threading.Event()
    driver = FakeDriver(cancel_on_key=cancel)
    agent = _agent(tmp_path, driver, cancel)
    result = agent.execute_plan("goal", KEY_PLAN)
    assert result["status"] == "killed"
    assert driver.keys == ["alt+Up"]
