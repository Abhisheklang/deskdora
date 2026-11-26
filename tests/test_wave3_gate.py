"""Wave 3 regression tests for vibe S1/V2/V3/V5.

The composed path is what matters: autonomy.evaluate() orders a
step confirmed, and the ConfirmationGate must honour that verdict
instead of re-classifying the step as safe.  Before the fix, a
launch step and a click on a "Buy" button were auto-approved by a
deny-all gate without the human policy ever being consulted.
"""
import pytest

from deskdora.agent import Agent
from deskdora.audit import RunLog
from deskdora.autonomy import AutonomyPolicy, PolicyError, evaluate, validate_policy
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Observation
from deskdora.safety import (AllowList, ConfirmationGate, KillSwitch,
                             approve_all, classify_step, deny_all)


class FakeDriver(DesktopDriver):
    id = "fake"
    implemented = frozenset()

    def __init__(self):
        self.launched = []
        self.clicks = []

    def observe(self):
        return Observation(elements=[], windows=[], texts={})

    def click(self, x, y):
        self.clicks.append((x, y))

    def launch(self, command):
        self.launched.append(command)
        return command

    def screenshot(self, path):
        return path


def make_agent(tmp_path, driver, gate):
    return Agent(planner=None, planner_mode="builtin", gate=gate,
                 kill_switch=KillSwitch(stop_file=str(tmp_path / "STOP")),
                 allow_list=AllowList(),
                 run_log=RunLog(str(tmp_path), "goal", "builtin"),
                 driver=driver)


LAUNCH = {"action": "launch", "command": "xfce4-terminal"}
BUY_CLICK = {"action": "click", "target": {"name_contains": "Buy"}}
PAY_CLICK = {"action": "click", "target": {"name_contains": "Pay"}}


def test_gate_alone_no_longer_auto_approves_launch_or_money():
    gate = ConfirmationGate(deny_all)
    assert gate.check(LAUNCH) is False
    assert gate.check(BUY_CLICK) is False
    assert gate.check(PAY_CLICK) is False
    assert len(gate.history) == 3  # the human policy was consulted


def test_gate_defers_to_autonomy_verdict():
    gate = ConfirmationGate(deny_all)
    # Even a step the classifier alone would call safe must reach
    # the policy when the autonomy layer ordered it confirmed.
    safe_step = {"action": "type", "text": "hello"}
    assert gate.check(safe_step, requires_confirmation=True) is False
    assert gate.history and gate.history[0]["approved"] is False


def test_end_to_end_launch_reaches_human_and_is_blocked(tmp_path):
    driver = FakeDriver()
    gate = ConfirmationGate(deny_all)
    agent = make_agent(tmp_path, driver, gate)
    result = agent.execute_plan(
        "open a terminal", [dict(LAUNCH), {"action": "done"}])
    assert result["status"] == "blocked"
    assert driver.launched == []  # never executed without approval
    assert gate.history and gate.history[0]["approved"] is False


def test_end_to_end_buy_click_reaches_human_and_is_blocked(tmp_path):
    driver = FakeDriver()
    gate = ConfirmationGate(deny_all)
    agent = make_agent(tmp_path, driver, gate)
    result = agent.execute_plan(
        "buy something", [dict(BUY_CLICK), {"action": "done"}])
    assert result["status"] == "blocked"
    assert driver.clicks == []
    assert gate.history and gate.history[0]["approved"] is False


def test_end_to_end_launch_runs_when_human_approves(tmp_path):
    driver = FakeDriver()
    gate = ConfirmationGate(approve_all)
    agent = make_agent(tmp_path, driver, gate)
    result = agent.execute_plan(
        "open a terminal", [dict(LAUNCH), {"action": "done", "answer": "ok"}])
    assert result["status"] == "success"
    assert driver.launched == ["xfce4-terminal"]
    assert gate.history and gate.history[0]["approved"] is True


def test_money_hard_floor_intact():
    with pytest.raises(PolicyError):
        validate_policy({"money": "free"})
    policy = AutonomyPolicy({"money": "confirm"})
    decision, category, significant = evaluate(policy, BUY_CLICK)
    assert (decision, category, significant) == ("confirm", "money", True)
    # Even with every other category free, money still confirms.
    policy = AutonomyPolicy({"files": "free", "apps": "free",
                             "web": "free", "messages": "free"})
    assert evaluate(policy, BUY_CLICK)[0] == "confirm"
    # Under the default all-confirm policy a launch confirms too.
    assert evaluate(None, LAUNCH)[0] == "confirm"
    assert evaluate(AutonomyPolicy({}), LAUNCH)[0] == "confirm"


def test_single_classifier_covers_launch_and_money():
    assert classify_step(LAUNCH) == "destructive"
    assert classify_step(BUY_CLICK) == "destructive"
    assert classify_step(PAY_CLICK) == "destructive"
    assert classify_step({"action": "click",
                          "target": {"name_contains": "Checkout"}}) == "destructive"
    assert classify_step({"action": "type", "text": "hello"}) == "safe"
