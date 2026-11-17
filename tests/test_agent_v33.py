"""v3.3 engine behaviours: dry-run step skipping enforced by the
executor, per-app permission refusal at the action layer, and
one-shot failure self-recovery."""
import pytest

from deskdora.agent import Agent, StepFailure, describe_step
from deskdora.audit import RunLog
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Element, Observation
from deskdora.safety import AllowList, ConfirmationGate, KillSwitch, approve_all


def el(app, window, label="", focused=True):
    return Element(idx=0, app=app, window=window, role="button",
                   name=label, label=label, x=10, y=10, w=80, h=20,
                   focused=focused)


class FakeDriver(DesktopDriver):
    id = "fake"
    implemented = frozenset()

    def __init__(self):
        self.elements = []
        self.windows = []
        self.clicks = []
        self.launched = []
        self.refind_ok = True
        self.refind_failures = 0

    def observe(self):
        return Observation(elements=list(self.elements),
                           windows=list(self.windows), texts={})

    def refind(self, element):
        if self.refind_failures > 0:
            self.refind_failures -= 1
            return None
        return element if self.refind_ok else None

    def click(self, x, y):
        self.clicks.append((x, y))

    def double_click(self, x, y):
        pass

    def type_text(self, text):
        pass

    def key(self, combo):
        pass

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


def make_agent(tmp_path, driver, events, **kw):
    return Agent(planner=None, planner_mode="builtin",
                 gate=ConfirmationGate(approve_all),
                 kill_switch=KillSwitch(), allow_list=AllowList(),
                 run_log=RunLog(str(tmp_path), "goal", "builtin"),
                 driver=driver,
                 on_event=lambda kind, **f: events.append((kind, f)),
                 **kw)


WAIT_PLAN = [{"action": "wait", "seconds": 0.01},
             {"action": "wait", "seconds": 0.01},
             {"action": "done", "answer": "finished"}]


# ----------------------------------------------------------------------
# dry-run: the engine enforces skips
# ----------------------------------------------------------------------

def test_execute_plan_skips_unticked_steps(tmp_path):
    driver, events = FakeDriver(), []
    agent = make_agent(tmp_path, driver, events)
    result = agent.execute_plan("goal", list(WAIT_PLAN), skip={1})
    assert result["status"] == "success"
    skipped = [f for k, f in events
               if k == "step_result" and f.get("skipped")]
    assert len(skipped) == 1
    assert skipped[0]["index"] == 1
    assert "skipped by user" in skipped[0]["detail"]


def test_execute_plan_without_skip_runs_everything(tmp_path):
    driver, events = FakeDriver(), []
    agent = make_agent(tmp_path, driver, events)
    result = agent.execute_plan("goal", list(WAIT_PLAN))
    assert result["status"] == "success"
    assert not [f for k, f in events
                if k == "step_result" and f.get("skipped")]


def test_describe_step_is_readable():
    assert "thunar" in describe_step(
        {"action": "launch", "command": "thunar"})
    assert "Save" in describe_step(
        {"action": "click", "target": {"label_contains": "Save"}})
    assert describe_step({"action": "done"}).startswith("Finish")


# ----------------------------------------------------------------------
# per-app permissions: enforcement at the action layer
# ----------------------------------------------------------------------

def test_denied_app_click_refused(tmp_path):
    driver, events = FakeDriver(), []
    driver.elements = [el("keepassxc", "Vault", "Unlock")]
    agent = make_agent(tmp_path, driver, events,
                       denied_apps=["keepassxc"])
    agent.plan_observation = driver.observe()
    with pytest.raises(StepFailure, match="app permissions"):
        agent._exec_primitive(
            {"action": "click", "target": {"label_contains": "Unlock"}})
    assert driver.clicks == []


def test_denied_app_launch_refused(tmp_path):
    driver, events = FakeDriver(), []
    agent = make_agent(tmp_path, driver, events,
                       denied_apps=["keepassxc"])
    with pytest.raises(StepFailure, match="app permissions"):
        agent._exec_primitive(
            {"action": "launch", "command": "keepassxc"})
    assert driver.launched == []


def test_allowed_app_click_proceeds(tmp_path):
    driver, events = FakeDriver(), []
    driver.elements = [el("thunar", "Files", "Open")]
    agent = make_agent(tmp_path, driver, events,
                       denied_apps=["keepassxc"])
    agent.plan_observation = driver.observe()
    agent._exec_primitive(
        {"action": "click", "target": {"label_contains": "Open"}})
    assert driver.clicks == [(50, 20)]


def test_build_plan_hides_denied_elements(tmp_path):
    driver, events = FakeDriver(), []
    driver.elements = [el("keepassxc", "Vault", "Unlock"),
                       el("thunar", "Files", "Open")]
    seen = {}

    class StubPlanner:
        name = "builtin"

        def plan(self, goal, observation, history=None):
            seen["elements"] = list(observation.elements)
            return [{"action": "done", "answer": "ok"}]

    agent = make_agent(tmp_path, driver, events,
                       denied_apps=["keepassxc"])
    agent.planner = StubPlanner()
    agent.build_plan("goal")
    apps = {e.app for e in seen["elements"]}
    assert apps == {"thunar"}


# ----------------------------------------------------------------------
# self-recovery: one alternative attempt, then honesty
# ----------------------------------------------------------------------

def test_recovery_succeeds_on_fresh_match(tmp_path):
    driver, events = FakeDriver(), []
    driver.elements = [el("thunar", "Files", "Open")]
    driver.refind_failures = 1        # first refind goes stale
    agent = make_agent(tmp_path, driver, events)
    agent.plan_observation = driver.observe()
    plan = [{"action": "click", "element": 0},
            {"action": "done", "answer": "opened"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] == "success"
    recovery = [f for k, f in events if k == "recovery"]
    assert len(recovery) == 1 and recovery[0]["ok"] is True
    done = [f for k, f in events
            if k == "step_result" and f.get("recovered")]
    assert len(done) == 1


def test_recovery_gives_up_after_one_attempt(tmp_path):
    driver, events = FakeDriver(), []
    driver.elements = [el("thunar", "Files", "Open")]
    driver.refind_ok = False          # the element never comes back
    agent = make_agent(tmp_path, driver, events)
    agent.plan_observation = driver.observe()
    plan = [{"action": "click", "element": 0},
            {"action": "done", "answer": "opened"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] != "success"
    recovery = [f for k, f in events if k == "recovery"]
    assert len(recovery) == 1         # exactly one alternative attempt


def test_recovery_can_be_switched_off(tmp_path):
    driver, events = FakeDriver(), []
    driver.elements = [el("thunar", "Files", "Open")]
    driver.refind_failures = 1
    agent = make_agent(tmp_path, driver, events, recovery_enabled=False)
    agent.plan_observation = driver.observe()
    plan = [{"action": "click", "element": 0},
            {"action": "done", "answer": "opened"}]
    result = agent.execute_plan("goal", plan)
    assert result["status"] != "success"
    assert not [f for k, f in events if k == "recovery"]
