"""v3.0: per-category autonomy policy + proactive proposals."""
import os

import pytest

from deskdora.autonomy import (AutonomyPolicy, PolicyError,
                               categorise_step, evaluate,
                               validate_policy)
from deskdora.proactive import (ProposalStore, ProactiveWatcher,
                                loose_files)


# ------------------------------------------------------------- policy

def test_money_floor_is_hard():
    with pytest.raises(PolicyError, match="money"):
        validate_policy({"money": "free"})
    policy = validate_policy({"money": "never", "files": "free"})
    assert policy["money"] == "never"
    assert policy["files"] == "free"
    with pytest.raises(PolicyError):
        validate_policy({"money": "sometimes"})
    with pytest.raises(PolicyError):
        validate_policy({"teleport": "free"})


def test_categorisation():
    submit = {"action": "click",
              "target": {"role": "push button", "name_contains": "Submit"}}
    assert categorise_step(submit) == ("messages", True)
    pay = {"action": "click",
           "target": {"role": "push button", "name_contains": "Pay now"}}
    assert categorise_step(pay) == ("money", True)
    delete = {"action": "click",
              "target": {"role": "push button", "name_contains": "Delete"}}
    assert categorise_step(delete) == ("files", True)
    install = {"action": "click",
               "target": {"role": "push button",
                          "name_contains": "Install"}}
    assert categorise_step(install) == ("apps", True)
    launch_browser = {"action": "launch", "command": "chromium http://x"}
    # Launch is significant since the BugHunter C3 fix: running a
    # command always requires confirmation under a confirm policy.
    assert categorise_step(launch_browser) == ("web", True)
    launch_term = {"action": "launch", "command": "xfce4-terminal"}
    assert categorise_step(launch_term) == ("apps", True)
    wait = {"action": "wait", "seconds": 1}
    assert categorise_step(wait)[1] is False


def test_evaluate_levels():
    submit = {"action": "click",
              "target": {"role": "push button", "name_contains": "Submit"}}
    # default (confirm): significant steps confirm, safe steps allow
    assert evaluate(None, submit)[0] == "confirm"
    assert evaluate(None, {"action": "wait", "seconds": 1})[0] == "allow"
    free = AutonomyPolicy({"messages": "free"})
    assert evaluate(free, submit) == ("allow", "messages", True)
    never = AutonomyPolicy({"messages": "never"})
    assert evaluate(never, submit)[0] == "deny"
    # never closes the category even for non-significant steps
    web_never = AutonomyPolicy({"web": "never"})
    assert evaluate(web_never,
                    {"action": "launch", "command": "chromium"})[0] == "deny"
    # money can be confirm/never, and confirm still confirms
    money_confirm = AutonomyPolicy({"money": "confirm"})
    pay = {"action": "click",
           "target": {"role": "push button", "name_contains": "Pay now"}}
    assert evaluate(money_confirm, pay)[0] == "confirm"


def test_agent_enforces_never_and_free(tmp_path):
    # End-to-end at the agent level with a fake driver/planner:
    # a 'never' category blocks before execution; 'free' executes a
    # destructive step with no gate call and logs the auto-approval.
    from deskdora.agent import Agent
    from deskdora.audit import RunLog
    from deskdora.drivers.base import DesktopDriver
    from deskdora.perception import Observation
    from deskdora.safety import (AllowList, ConfirmationGate, KillSwitch,
                                 deny_all)

    class FakeDriver(DesktopDriver):
        id = "fake"
        implemented = frozenset()

        def __init__(self):
            self.keys = []

        def observe(self):
            return Observation(elements=[], windows=[], texts={})

        def key(self, combo):
            self.keys.append(combo)

        def screenshot(self, path):
            return path

    class FixedPlanner:
        name = "fixed"

        def plan(self, goal, observation, history=None):
            return [{"action": "key", "key": "Delete"},
                    {"action": "done", "answer": "done"}]

    def make_agent(policy):
        driver = FakeDriver()
        events = []
        agent = Agent(FixedPlanner(), "fixed",
                      ConfirmationGate(deny_all),
                      KillSwitch(stop_file=str(tmp_path / "STOP")),
                      AllowList(), RunLog(str(tmp_path), "g", "fixed"),
                      driver=driver, autonomy=policy,
                      on_event=lambda k, **f: events.append((k, f)))
        agent.context = "files"
        return agent, driver, events

    # Delete key in files context is destructive/files.  never -> blocked.
    agent, driver, events = make_agent(AutonomyPolicy({"files": "never"}))
    result = agent.run("delete something")
    assert result["status"] == "blocked"
    assert "never" in result["reason"]
    assert driver.keys == []

    # free -> executes despite a deny-all gate; autonomy event logged.
    agent, driver, events = make_agent(AutonomyPolicy({"files": "free"}))
    result = agent.run("delete something")
    assert result["status"] == "success"
    assert driver.keys == ["Delete"]
    assert any(k == "autonomy" and f.get("decision") == "auto-approved"
               for k, f in events)


# ------------------------------------------------------------- proactive

def _seed_loose(folder, n):
    os.makedirs(folder, exist_ok=True)
    for i in range(n):
        with open(os.path.join(folder, f"f{i}.png"), "w") as fh:
            fh.write("x")


def test_loose_files_counts_only_organisable(tmp_path):
    _seed_loose(str(tmp_path), 3)
    with open(tmp_path / "notes.xyz", "w") as fh:
        fh.write("x")
    os.makedirs(tmp_path / "subdir")
    assert len(loose_files(str(tmp_path))) == 3


def test_watcher_proposes_and_approves(tmp_path):
    folder = str(tmp_path / "messy")
    _seed_loose(folder, 6)
    store = ProposalStore(str(tmp_path / "proposals.json"))
    runs = []
    watcher = ProactiveWatcher(
        store, lambda goal: (runs.append(goal), (True, "started"))[1],
        policy=AutonomyPolicy({"files": "confirm"}))
    made = watcher.scan([folder])
    assert len(made) == 1 and made[0]["status"] == "proposed"
    assert watcher.scan([folder]) == []          # no duplicate proposal
    result = watcher.approve(made[0]["id"])
    assert result["started"] is True
    assert runs == [f"Organise folder {folder} by type"]
    assert store.get(made[0]["id"])["status"] == "executed"
    # after execution the folder counts as handled: same mess level
    # does not re-propose
    assert watcher.scan([folder]) == []


def test_watcher_free_category_auto_executes(tmp_path):
    folder = str(tmp_path / "messy")
    _seed_loose(folder, 7)
    store = ProposalStore(str(tmp_path / "proposals.json"))
    runs = []
    watcher = ProactiveWatcher(
        store, lambda goal: (runs.append(goal), (True, "started"))[1],
        policy=AutonomyPolicy({"files": "free"}))
    made = watcher.scan([folder])
    assert made[0]["status"] == "executed"
    assert made[0]["auto"] is True
    assert runs == [f"Organise folder {folder} by type"]


def test_watcher_below_threshold_and_dismiss(tmp_path):
    folder = str(tmp_path / "tidy")
    _seed_loose(folder, 3)
    store = ProposalStore(str(tmp_path / "proposals.json"))
    watcher = ProactiveWatcher(store, lambda goal: (True, "started"))
    assert watcher.scan([folder]) == []
    for i in range(3, 7):                        # 7 loose in total
        with open(os.path.join(folder, f"f{i}.png"), "w") as fh:
            fh.write("x")
    made = watcher.scan([folder])
    assert len(made) == 1
    watcher.dismiss(made[0]["id"])
    assert store.get(made[0]["id"])["status"] == "dismissed"
    assert watcher.scan([folder]) == []          # dismissed: no nagging
