"""Focus guard: untargeted type/key steps must verify that the
intended window actually holds focus, refocus it if possible, and
fail honestly otherwise (the v1.0 'typing follows focus' defect)."""
import pytest

from deskdora.agent import Agent, StepFailure
from deskdora.audit import RunLog
from deskdora.drivers.base import DesktopDriver
from deskdora.perception import Element, Observation
from deskdora.safety import AllowList, ConfirmationGate, KillSwitch, approve_all


def el(app, window, focused=False, role="text", name="", label=""):
    return Element(idx=0, app=app, window=window, role=role, name=name,
                   label=label, x=10, y=10, w=100, h=20, focused=focused)


class FakeDriver(DesktopDriver):
    id = "fake"
    implemented = frozenset()

    def __init__(self):
        self.elements = []
        self.windows = []
        self.typed = []
        self.keys = []
        self.launched = []
        self.active_title = ""
        self.activate_ok = False
        self.on_activate = None

    def observe(self):
        return Observation(elements=list(self.elements),
                           windows=list(self.windows), texts={})

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
        return self.active_title

    def activate_window(self, title):
        if self.activate_ok and self.on_activate:
            self.on_activate()
        return self.activate_ok


@pytest.fixture
def rig(tmp_path):
    driver = FakeDriver()
    events = []
    agent = Agent(planner=None, planner_mode="builtin",
                  gate=ConfirmationGate(approve_all), kill_switch=KillSwitch(),
                  allow_list=AllowList(),
                  run_log=RunLog(str(tmp_path), "goal", "builtin"),
                  driver=driver,
                  on_event=lambda kind, **f: events.append((kind, f)))
    return agent, driver, events


def test_targeted_type_pins_focus(rig):
    agent, driver, _events = rig
    driver.elements = [el("formapp", "Form", focused=True, label="name")]
    agent.plan_observation = driver.observe()
    agent._exec_primitive({"action": "type", "text": "Ada",
                           "target": {"label_contains": "name"}})
    assert driver.typed == ["Ada"]
    assert agent.focus_app == "formapp"
    assert agent.focus_window == "Form"


def test_guard_blocks_typing_into_wrong_app(rig):
    agent, driver, _events = rig
    agent.focus_app, agent.focus_window = "formapp", "Form"
    driver.elements = [el("otherapp", "Other", focused=True)]
    driver.activate_ok = False
    with pytest.raises(StepFailure, match="focus guard"):
        agent._exec_primitive({"action": "type", "text": "secret"})
    assert driver.typed == []          # nothing was typed anywhere


def test_guard_refocuses_and_proceeds(rig):
    agent, driver, events = rig
    agent.focus_app, agent.focus_window = "formapp", "Form"
    driver.elements = [el("otherapp", "Other", focused=True)]

    def flip():
        driver.elements = [el("formapp", "Form", focused=True)]

    driver.activate_ok = True
    driver.on_activate = flip
    agent._exec_primitive({"action": "key", "key": "Return"})
    assert driver.keys == ["Return"]
    assert any(k == "focus_guard" and f.get("result") == "refocused"
               for k, f in events)


def test_guard_adopts_launched_window(rig):
    agent, driver, _events = rig
    driver.windows = ["DeskDora"]
    driver.elements = [el("deskdora", "DeskDora", focused=True)]
    agent._exec_primitive({"action": "launch", "command": "thunar /tmp/x"})
    # the launched app appears and takes focus
    driver.windows = ["DeskDora", "messy"]
    driver.elements = [el("thunar", "messy", focused=True)]
    agent._exec_primitive({"action": "key", "key": "ctrl+shift+n"})
    assert driver.keys == ["ctrl+shift+n"]
    assert agent.focus_app == "thunar"


def test_guard_same_app_title_change_is_fine(rig):
    # Thunar retitles itself as it navigates folders; the guard keys
    # on the application, not the exact title.
    agent, driver, _events = rig
    agent.focus_app, agent.focus_window = "thunar", "messy"
    driver.elements = [el("thunar", "Documents", focused=True)]
    agent._exec_primitive({"action": "key", "key": "ctrl+v"})
    assert driver.keys == ["ctrl+v"]
    assert agent.focus_window == "Documents"


def test_guard_unverified_platform_still_types(rig):
    # Wayland-like: no focused element, no active-window query.  The
    # guard must not block, but must leave an 'unverified' trace.
    agent, driver, events = rig
    agent.focus_app, agent.focus_window = "thunar", "messy"
    driver.elements = [el("thunar", "messy", focused=False)]
    driver.active_title = ""
    agent._exec_primitive({"action": "type", "text": "hello"})
    assert driver.typed == ["hello"]
    assert any(k == "focus_guard" and f.get("result") == "unverified"
               for k, f in events)


def test_guard_dialog_of_same_app_does_not_trip(rig):
    # Regression (v1.1 gate): Thunar's "Create New Folder" dialog made
    # the guard pin a transient title and then refuse to type, even
    # though focus was in the right app.  With no focused element
    # visible, the active window is mapped to its owning app.
    agent, driver, _events = rig
    agent.focus_app, agent.focus_window = "thunar", "Create New Folder"
    driver.elements = [el("thunar", "messy", focused=False)]
    driver.active_title = "messy - Thunar"     # WM title of the parent
    driver.activate_ok = False
    agent._exec_primitive({"action": "type", "text": "Documents"})
    assert driver.typed == ["Documents"]


def test_guard_active_window_in_other_app_blocks(rig):
    agent, driver, _events = rig
    agent.focus_app, agent.focus_window = "thunar", "messy"
    driver.elements = [el("thunar", "messy", focused=False),
                       el("mousepad", "notes.txt", focused=False)]
    driver.active_title = "notes.txt"
    driver.activate_ok = False
    with pytest.raises(StepFailure, match="focus guard"):
        agent._exec_primitive({"action": "type", "text": "x"})
    assert driver.typed == []
