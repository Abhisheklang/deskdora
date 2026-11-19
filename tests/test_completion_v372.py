"""Completion response (v3.7.2) — regression tests.

User order, 2026-11-19: when the app finishes a task it "just
stops" — no acknowledgement.  A successful run now ends in a
completion moment of its own:

* the server records a completion payload (goal, steps executed,
  elapsed — measured server-side) in the run state, riding
  /api/status; the Run view's WORK DONE card and the floating
  window's DONE state render exactly that payload;
* the existing run-completed tone (v3.3 sounds) and desktop
  notification (v3.4) fire on success — and only on success, and
  only when their Settings toggles allow them;
* a failed or killed run NEVER sets the completion state — it
  keeps its own honest end state;
* the next run (including a queued task starting through the
  same start_run path), a reset, and the dismiss endpoint each
  clear it; the v3.7.1 settle-to-idle logic keys on the result,
  which a completion always accompanies, so the two never fight.
"""
import json
import threading
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from deskdora import overlay, server
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Observation
from deskdora.safety import KillSwitch


# ----------------------------------------------------------------------
# Harness (modelled on test_wedge_fix): a real RunManager against a
# fake driver and a static planner, with the REAL sounds / notify
# modules in the path — tones are observed at the server's
# dispatch queue (the window plays them; dispatch is the provable
# layer) and notify-send is replaced by a recorder, so the
# Settings-toggle gating inside those modules is exercised, not
# bypassed.
# ----------------------------------------------------------------------

class FakeDriver(DesktopDriver):
    id = "fake"
    implemented = frozenset()

    def __init__(self, block_first_key=False):
        self.keys = []
        self.block_first_key = block_first_key
        self.entered = threading.Event()
        self.release = threading.Event()

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
        self.keys.append(combo)
        if self.block_first_key and len(self.keys) == 1:
            self.entered.set()
            self.release.wait(timeout=60)

    def scroll(self, x, y, direction="down"):
        pass

    def launch(self, command):
        pass

    def screenshot(self, path):
        pass


class StaticPlanner:
    def __init__(self, steps):
        self._steps = steps

    def plan(self, goal, observation, history=None):
        return [dict(s) for s in self._steps]


KEY_PLAN = [{"action": "key", "key": "alt+Up"},
            {"action": "key", "key": "ctrl+x"},
            {"action": "done"}]
FAIL_PLAN = [{"action": "fail", "reason": "no such element"}]


def wait_until(predicate, timeout=15.0, poll=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A RunManager on tmp dirs + a notification recorder.  Tones
    are observed where the server now dispatches them: the
    tone_events queue in the run state (the window plays them;
    the dispatch is what a test can prove)."""
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    notes = []
    monkeypatch.setattr(
        server.notify, "_notify_send",
        lambda title, body, **kw: notes.append(title) or True)
    mgr = server.RunManager()
    mgr.kill = KillSwitch(str(tmp_path / "STOP"))
    mgr.kill_grace_seconds = 0.6

    def tones():
        return [(e["event"], e["tone"])
                for e in mgr.state["tone_events"]]

    return mgr, tones, notes


def install(mgr, monkeypatch, driver, steps):
    planner = StaticPlanner(steps)
    monkeypatch.setattr(server, "get_planner",
                        lambda mode, **kw: (planner, "builtin"))
    monkeypatch.setattr("deskdora.drivers.get_driver",
                        lambda name=None: driver)
    return planner


def run_and_wait(mgr, goal):
    assert mgr.start_run(goal, "builtin")["ok"]
    assert wait_until(lambda: not mgr.state["running"]
                      and mgr.state["result"] is not None)
    return mgr.state["result"]


# ----------------------------------------------------------------------
# The completion payload: success only, with the server's numbers
# ----------------------------------------------------------------------

def test_success_sets_completion_payload(rig, monkeypatch):
    mgr, _tones, _notes = rig
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    result = run_and_wait(mgr, "organise the folder")
    assert result["status"] == "success"
    comp = mgr.state["completion"]
    assert comp is not None
    assert comp["goal"] == "organise the folder"
    # The steps are the run's own executed count, not a UI guess.
    assert comp["steps"] == result["steps_executed"] == 2
    assert comp["elapsed_seconds"] is not None
    assert comp["elapsed_seconds"] >= 0
    assert comp["finished_at"] > 0
    assert "answer" in comp


def test_failed_run_sets_no_completion(rig, monkeypatch):
    mgr, _tones, _notes = rig
    install(mgr, monkeypatch, FakeDriver(), FAIL_PLAN)
    result = run_and_wait(mgr, "do the impossible")
    assert result["status"] == "failed"
    assert mgr.state["completion"] is None


def test_killed_run_sets_no_completion(rig, monkeypatch):
    mgr, _tones, _notes = rig
    driver = FakeDriver(block_first_key=True)
    install(mgr, monkeypatch, driver, KEY_PLAN)
    assert mgr.start_run("organise the folder", "builtin")["ok"]
    assert driver.entered.wait(timeout=10)
    mgr.trip_kill()
    assert wait_until(lambda: not mgr.state["running"])
    assert mgr.state["result"]["status"] == "killed"
    assert mgr.state["completion"] is None
    driver.release.set()


# ----------------------------------------------------------------------
# Tone + notification: success only, and only when toggled on
# ----------------------------------------------------------------------

def test_completion_tone_and_notification_fire_on_success(
        rig, monkeypatch):
    mgr, tones, notes = rig
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    run_and_wait(mgr, "organise the folder")
    # The default completion tone, dispatched on success.
    assert ("run_completed", "chime") in tones()
    assert "DeskDora — run completed" in notes


def test_failure_fires_its_own_tone_not_the_completion_one(
        rig, monkeypatch):
    mgr, tones, notes = rig
    install(mgr, monkeypatch, FakeDriver(), FAIL_PLAN)
    run_and_wait(mgr, "do the impossible")
    assert ("run_failed", "low") in tones()
    assert all(ev != "run_completed" for ev, _t in tones())
    assert "DeskDora — run failed" in notes
    assert "DeskDora — run completed" not in notes


def test_killed_run_dispatches_no_tone(rig, monkeypatch):
    mgr, tones, _notes = rig
    driver = FakeDriver(block_first_key=True)
    install(mgr, monkeypatch, driver, KEY_PLAN)
    assert mgr.start_run("organise the folder", "builtin")["ok"]
    assert driver.entered.wait(timeout=10)
    mgr.trip_kill()
    assert wait_until(lambda: not mgr.state["running"])
    assert mgr.state["result"]["status"] == "killed"
    assert tones() == []
    driver.release.set()


def test_completion_tone_and_notification_respect_toggles(
        rig, monkeypatch, tmp_path):
    mgr, tones, notes = rig
    # The run-completed tone set to Off and its notification
    # toggled off in the user's config.  The completion STATE is
    # not gated (it is the app answering in its own window) —
    # the sound and the system notification are.
    (tmp_path / "config.json").write_text(json.dumps({
        "sounds": {"events": {"run_completed": "off"}},
        "notifications": {"events": {"run_completed": False}},
    }))
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    result = run_and_wait(mgr, "organise the folder")
    assert result["status"] == "success"
    assert mgr.state["completion"] is not None
    assert all(ev != "run_completed" for ev, _t in tones())
    assert "DeskDora — run completed" not in notes


# ----------------------------------------------------------------------
# Clearing: next run, queue advance, reset, dismiss
# ----------------------------------------------------------------------

def test_next_run_clears_completion(rig, monkeypatch):
    mgr, _tones, _notes = rig
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    run_and_wait(mgr, "first goal")
    assert mgr.state["completion"]["goal"] == "first goal"
    # start_run clears synchronously — this is also the exact
    # mechanism by which a queued task starting clears it (the
    # queue starts every item through start_run).
    assert mgr.start_run("second goal", "builtin")["ok"]
    assert mgr.state["completion"] is None
    assert wait_until(lambda: not mgr.state["running"]
                      and mgr.state["result"] is not None)
    assert mgr.state["completion"]["goal"] == "second goal"


def test_queue_advance_replaces_completion(rig, monkeypatch):
    mgr, _tones, _notes = rig
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    mgr.queue.add("queued first", "builtin")
    mgr.queue.add("queued second", "builtin")
    mgr.queue.start()
    try:
        assert wait_until(
            lambda: mgr.state["completion"] is not None
            and mgr.state["completion"]["goal"] == "queued second",
            timeout=30)
    finally:
        mgr.queue.stop()
    snap = mgr.queue.snapshot()
    assert [it["status"] for it in snap["items"]] == [
        "success", "success"]


def test_reset_clears_completion(rig, monkeypatch):
    mgr, _tones, _notes = rig
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    run_and_wait(mgr, "organise the folder")
    assert mgr.state["completion"] is not None
    mgr.reset_state()
    assert mgr.state["completion"] is None
    assert mgr.state["result"] is None


def test_dismiss_completion(rig, monkeypatch):
    mgr, _tones, _notes = rig
    install(mgr, monkeypatch, FakeDriver(), KEY_PLAN)
    run_and_wait(mgr, "organise the folder")
    assert mgr.dismiss_completion() == {"ok": True}
    assert mgr.state["completion"] is None
    # The result itself stays — History and the state pill keep it
    # until reset or the next run, as before this change.
    assert mgr.state["result"]["status"] == "success"


def test_dismiss_refused_while_running(rig):
    mgr, _tones, _notes = rig
    with mgr.lock:
        mgr.state["running"] = True
    with pytest.raises(HTTPException) as exc:
        mgr.dismiss_completion()
    assert exc.value.status_code == 409


# ----------------------------------------------------------------------
# /api/status carries it; the dismiss route clears it
# ----------------------------------------------------------------------

@pytest.fixture
def api_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    st = server.MANAGER.state
    saved = dict(st)
    st.update({"running": False, "planner_error": None,
               "connection_error": None, "goal": "", "result": None,
               "pending_plan": None, "run_dir": None,
               "completion": None})
    yield st
    st.update(saved)


def test_status_payload_and_dismiss_route(api_state):
    client = TestClient(server.app)
    api_state["completion"] = {
        "goal": "organise the folder", "steps": 139,
        "elapsed_seconds": 252.4, "finished_at": 1700000000.0,
        "answer": "Folder organised by type"}
    body = client.get("/api/status").json()
    assert body["state"]["completion"]["steps"] == 139
    assert body["state"]["completion"]["goal"] == \
        "organise the folder"
    assert client.post("/api/completion/dismiss").status_code == 200
    assert api_state["completion"] is None
    body = client.get("/api/status").json()
    assert body["state"]["completion"] is None


# ----------------------------------------------------------------------
# Floating window: DONE is success-only and carries goal + steps
# ----------------------------------------------------------------------

def _status(state, progress=None):
    return {"state": state, "progress": progress or {}}


def test_overlay_done_state_on_completion():
    st = overlay.overlay_state(_status({
        "running": False,
        "goal": "Organise folder /home/you/Downloads by type",
        "result": {"status": "success"},
        "completion": {
            "goal": "Organise folder /home/you/Downloads by type",
            "steps": 139, "elapsed_seconds": 252.4,
            "finished_at": 1700000000.0, "answer": ""}}))
    assert st["visible"] is True
    assert st["mode"] == "done"
    assert st["status_text"] == "DONE"
    assert st["goal"].startswith("Organise folder")
    assert st["steps_text"] == "139 STEPS · 4:12"
    assert st["percent"] == 100
    assert st["can_reset"] is True
    assert st["can_stop"] is False


def test_overlay_done_state_singular_step():
    st = overlay.overlay_state(_status({
        "running": False, "goal": "g",
        "result": {"status": "success"},
        "completion": {"goal": "g", "steps": 1,
                       "elapsed_seconds": None,
                       "finished_at": 1700000000.0, "answer": ""}}))
    assert st["mode"] == "done"
    assert st["steps_text"] == "1 STEP"


def test_overlay_failure_keeps_finished_state_not_done():
    st = overlay.overlay_state(_status({
        "running": False, "goal": "g",
        "result": {"status": "failed"}, "completion": None}))
    assert st["mode"] == "finished"
    assert st["status_text"] == "FAILED"


def test_overlay_killed_keeps_finished_state_not_done():
    st = overlay.overlay_state(_status({
        "running": False, "goal": "g",
        "result": {"status": "killed"}, "completion": None}))
    assert st["mode"] == "finished"
    assert st["status_text"] == "KILLED"


# ----------------------------------------------------------------------
# UI source guards: the card renders the payload, dismiss is wired
# ----------------------------------------------------------------------

def _read_index():
    import os
    path = os.path.join(os.path.dirname(__file__), "..",
                        "src", "deskdora", "static", "index.html")
    with open(path) as fh:
        return fh.read()


def test_ui_completion_card_source_guards():
    html = _read_index()
    assert 'id="doneCard"' in html
    assert 'id="doneMeta"' in html
    assert 'id="doneDismissBtn"' in html
    assert "WORK DONE" in html
    # The card renders the server payload — not client step counts.
    assert "st.state.completion" in html
    assert "comp.steps" in html
    assert "comp.elapsed_seconds" in html
    assert '"Work done"' in html
    assert "fmtElapsed" in html
    # Dismissal goes to the server endpoint from both move-on paths.
    assert html.count("/api/completion/dismiss") >= 2
