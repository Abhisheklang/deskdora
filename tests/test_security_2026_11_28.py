"""Security fixes, 2026-11-28 audit round.

Three proven bugs, one section each:

1. GET /api/status handed unauthenticated LAN clients the run's
   goal, its result (answers included: text extracted from the
   screen and the clipboard), the pending plan and the pending
   confirmation.  The endpoint's auth exemption dated from when
   the payload was only flags.
2. Typing into a terminal was ungated (the recorded C3 sub-gap):
   the classifiers are per-step, and no caller ever supplied the
   execution context that the terminal and file-manager rules
   key on, so a type step plus a Return key step into a terminal
   evaluated as allow under the default all-confirm policy.  The
   agent now computes the context from where input will land.
3. The file-change journal trusted planner-supplied file_op
   metadata: a step that moved nothing could journal a fabricated
   "move", and Undo would then relocate a real file on the
   strength of it.  Moves are now journaled only when the step
   demonstrably caused them (source present before, gone after,
   destination present).
"""
import os

import pytest
from fastapi.testclient import TestClient

from deskdora import server

PIN = "654321"


@pytest.fixture()
def pin_set():
    original = server.load_config().get("companion_pin", "")
    server.save_config({**server.load_config(), "companion_pin": PIN})
    yield PIN
    server.save_config({**server.load_config(), "companion_pin": original})


@pytest.fixture()
def run_state():
    """Plant a finished run + a pending confirmation on the
    manager, restore afterwards."""
    saved_result = server.MANAGER.state["result"]
    saved_goal = server.MANAGER.state["goal"]
    saved_pending = server.MANAGER.pending
    server.MANAGER.state["result"] = {
        "status": "success", "answer": "the capital is Paris",
        "answers": {"clipboard": "clipboard-marker-text"},
        "steps_executed": 2}
    server.MANAGER.state["goal"] = "goal-marker organise my files"
    server.MANAGER.pending = {
        "id": "cid12345",
        "step": {"action": "click",
                 "target": {"name_contains": "Pay now"}},
        "description": "pending-marker click Pay now",
        "created_at": 0, "event": None, "approved": None}
    yield
    server.MANAGER.state["result"] = saved_result
    server.MANAGER.state["goal"] = saved_goal
    server.MANAGER.pending = saved_pending


def _remote():
    return TestClient(server.app, client=("192.168.1.50", 50000))


def _local():
    return TestClient(server.app, client=("127.0.0.1", 50000))


# ------------------------------------------------------------ 1. status

def test_status_remote_unauthenticated_is_minimal(pin_set, run_state):
    res = _remote().get("/api/status")
    assert res.status_code == 200
    for marker in ("goal-marker", "clipboard-marker-text",
                   "the capital is Paris", "pending-marker",
                   "cid12345"):
        assert marker not in res.text
    body = res.json()
    assert body["config"]["has_pin"] is True
    assert body["pending_confirmation"] is None
    assert body["state"]["result"] is None
    assert body["authenticated"] is False


def test_status_remote_with_pin_gets_full_payload(pin_set, run_state):
    res = _remote().get("/api/status",
                        headers={"x-companion-pin": PIN})
    assert res.status_code == 200
    assert "goal-marker" in res.text
    assert res.json()["pending_confirmation"]["id"] == "cid12345"


def test_status_loopback_gets_full_payload(pin_set, run_state):
    res = _local().get("/api/status")
    assert res.status_code == 200
    assert "goal-marker" in res.text


# ------------------------------------------------- 2. terminal gating

from deskdora import autonomy  # noqa: E402
from deskdora.safety import classify_step  # noqa: E402


def test_classify_terminal_context_input_is_destructive():
    assert classify_step({"action": "type", "text": "ls",
                          "context": "terminal"}) == "destructive"
    assert classify_step({"action": "key", "key": "Return",
                          "context": "terminal"}) == "destructive"
    assert classify_step({"action": "build_sheet",
                          "source": "clipboard", "save_as": "x",
                          "context": "terminal"}) == "destructive"
    # The same steps outside a terminal keep their old verdicts.
    assert classify_step({"action": "type", "text": "ls"}) == "safe"
    assert classify_step({"action": "key", "key": "Return"}) == "safe"


def test_classify_command_surface_keys_are_destructive():
    for combo in ("ctrl+alt+t", "Ctrl+Alt+T", "alt+F2", "Alt+F2"):
        assert classify_step({"action": "key", "key": combo}) \
            == "destructive", combo
    assert classify_step({"action": "key", "key": "ctrl+c"}) == "safe"


def test_evaluate_terminal_context_confirms_by_default():
    policy = autonomy.AutonomyPolicy({})
    for step in ({"action": "type", "text": "rm -rf ~/Documents"},
                 {"action": "key", "key": "Return"}):
        decision, category, significant = autonomy.evaluate(
            policy, step, context="terminal")
        assert decision == "confirm", step
        assert category == "apps" and significant
    # And without the context the historical behaviour stands.
    decision, _, _ = autonomy.evaluate(
        policy, {"action": "type", "text": "hello"})
    assert decision == "allow"


def test_evaluate_delete_key_in_files_context_confirms():
    policy = autonomy.AutonomyPolicy({})
    decision, category, significant = autonomy.evaluate(
        policy, {"action": "key", "key": "Delete"}, context="files")
    assert decision == "confirm" and category == "files" and significant
    decision, _, _ = autonomy.evaluate(
        policy, {"action": "key", "key": "Delete"})
    assert decision == "allow"


# ------------------------------------------- 2b. agent context wiring

from deskdora.agent import Agent  # noqa: E402
from deskdora.audit import RunLog  # noqa: E402
from deskdora.drivers.base import DesktopDriver  # noqa: E402
from deskdora.perception import Observation  # noqa: E402
from deskdora.safety import (AllowList, ConfirmationGate,  # noqa: E402
                             KillSwitch)


class RecordingDriver(DesktopDriver):
    id = "fake"
    implemented = frozenset()

    def __init__(self, on_key=None):
        self.typed = []
        self.keys = []
        self.launched = []
        self._on_key = on_key

    def observe(self):
        return Observation(elements=[], windows=[], texts={})

    def refind(self, element):
        return element

    def click(self, x, y):
        pass

    def double_click(self, x, y):
        pass

    def type_text(self, text):
        self.typed.append(text)

    def key(self, combo):
        self.keys.append(combo)
        if self._on_key:
            self._on_key(combo)

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


def _agent(tmp_path, driver, gate, **kw):
    # An explicit per-test stop file: the shared default is the
    # live install's, and server tests earlier in the suite trip
    # the manager's kill switch, which would otherwise leak into
    # these agents as a pre-tripped switch.
    return Agent(planner=None, planner_mode="builtin", gate=gate,
                 kill_switch=KillSwitch(
                     stop_file=str(tmp_path / "STOP")),
                 allow_list=AllowList(),
                 run_log=RunLog(str(tmp_path), "goal", "builtin"),
                 driver=driver, settle=0, **kw)


def _recording_gate(verdicts):
    """A gate whose policy records every consultation; verdicts
    maps action -> bool (default deny)."""
    consulted = []

    def policy(step, description=""):
        consulted.append(step)
        return verdicts.get(step.get("action"), False)

    return ConfirmationGate(policy), consulted


def test_agent_gates_type_after_terminal_launch(tmp_path):
    driver = RecordingDriver()
    gate, consulted = _recording_gate({"launch": True})
    agent = _agent(tmp_path, driver, gate,
                   autonomy=autonomy.AutonomyPolicy({}))
    plan = [{"action": "launch", "command": "xfce4-terminal"},
            {"action": "wait", "seconds": 0},
            {"action": "type", "text": "rm -rf ~/Documents"},
            {"action": "key", "key": "Return"},
            {"action": "done", "answer": "done"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "blocked"
    assert [s["action"] for s in consulted] == ["launch", "type"]
    assert driver.typed == [] and driver.keys == []


def test_agent_gates_untargeted_type_when_terminal_focused(tmp_path):
    driver = RecordingDriver()
    gate, consulted = _recording_gate({})
    agent = _agent(tmp_path, driver, gate,
                   autonomy=autonomy.AutonomyPolicy({}))
    agent.focus_app = "gnome-terminal"
    plan = [{"action": "type", "text": "echo hi"},
            {"action": "done", "answer": "done"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "blocked"
    assert [s["action"] for s in consulted] == ["type"]
    assert driver.typed == []


def test_agent_does_not_gate_type_in_an_ordinary_app(tmp_path):
    driver = RecordingDriver()
    gate, consulted = _recording_gate({})
    agent = _agent(tmp_path, driver, gate,
                   autonomy=autonomy.AutonomyPolicy({}))
    agent.focus_app = "Firefox"
    plan = [{"action": "type", "text": "hello"},
            {"action": "done", "answer": "done"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "success"
    assert consulted == []
    assert driver.typed == ["hello"]


def test_agent_gates_delete_key_in_file_manager(tmp_path):
    driver = RecordingDriver()
    gate, consulted = _recording_gate({})
    agent = _agent(tmp_path, driver, gate,
                   autonomy=autonomy.AutonomyPolicy({}))
    agent.focus_app = "Thunar"
    plan = [{"action": "key", "key": "Delete"},
            {"action": "done", "answer": "done"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "blocked"
    assert [s["action"] for s in consulted] == ["key"]
    assert driver.keys == []


# ------------------------------------------------- 3. file_op journal

def test_fabricated_file_op_is_never_journaled(tmp_path):
    victim = tmp_path / "important.txt"
    victim.write_text("important contents")
    relocated = tmp_path / "elsewhere.txt"
    driver = RecordingDriver()
    gate, _ = _recording_gate({})
    agent = _agent(tmp_path, driver, gate)
    plan = [{"action": "wait", "seconds": 0,
             "file_op": {"op": "move", "files": [
                 {"src": str(relocated), "dst": str(victim)}]}},
            {"action": "done", "answer": "did nothing"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "success"
    assert agent.file_journal == []
    kinds = [kind for kind, _ in
             ((e["kind"], e) for e in _log_events(agent))]
    assert "file_op_unverified" in kinds
    assert victim.read_text() == "important contents"
    assert not relocated.exists()


def _log_events(agent):
    from deskdora.audit import read_log
    import glob
    logs = glob.glob(os.path.join(agent.log.dir, "log.jsonl"))
    return read_log(logs[0]) if logs else []


def test_real_move_is_journaled(tmp_path):
    src = tmp_path / "a.txt"
    dst_dir = tmp_path / "Text"
    dst = dst_dir / "a.txt"
    src.write_text("alpha")

    def do_paste(combo):
        if combo == "ctrl+v":
            dst_dir.mkdir(exist_ok=True)
            os.replace(src, dst)

    driver = RecordingDriver(on_key=do_paste)
    gate, _ = _recording_gate({})
    agent = _agent(tmp_path, driver, gate)
    plan = [{"action": "key", "key": "ctrl+v",
             "file_op": {"op": "move", "files": [
                 {"src": str(src), "dst": str(dst)}]}},
            {"action": "done", "answer": "organised"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "success"
    assert len(agent.file_journal) == 1
    record = agent.file_journal[0]
    assert record["src"] == str(src) and record["dst"] == str(dst)
    assert record["sha256"]


def test_unmoved_existing_files_are_not_journaled(tmp_path):
    # Both paths exist and the step changes nothing: the end
    # state alone looks like a completed move, which is exactly
    # why the pre-step snapshot decides.
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("alpha")
    dst.write_text("beta")
    driver = RecordingDriver()
    gate, _ = _recording_gate({})
    agent = _agent(tmp_path, driver, gate)
    plan = [{"action": "wait", "seconds": 0,
             "file_op": {"op": "move", "files": [
                 {"src": str(src), "dst": str(dst)}]}},
            {"action": "done", "answer": "did nothing"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "success"
    assert agent.file_journal == []
    assert src.read_text() == "alpha" and dst.read_text() == "beta"
