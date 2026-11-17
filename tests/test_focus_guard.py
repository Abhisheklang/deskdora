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


def test_guard_adopts_preexisting_window_of_launched_app(rig):
    # Regression (sheet tasks): the spreadsheet app was already
    # running when the plan launched it, so no *new* window appeared
    # and the pin stayed on the browser — the table was typed into
    # the browser window.  Adoption must also match by app identity.
    agent, driver, _events = rig
    driver.windows = ["PDF - Chrome", "Book1.gnumeric - Gnumeric"]
    driver.elements = [el("chrome", "PDF - Chrome", focused=True),
                       el("gnumeric", "Book1.gnumeric - Gnumeric")]
    agent.focus_app, agent.focus_window = "chrome", "PDF - Chrome"
    agent._exec_primitive({"action": "launch", "command": "gnumeric"})
    # focus is still on Chrome, but the guard must adopt gnumeric
    # and refocus before typing
    def flip():
        driver.elements = [el("chrome", "PDF - Chrome", focused=False),
                           el("gnumeric", "Book1.gnumeric - Gnumeric",
                              focused=True)]
    driver.activate_ok = True
    driver.on_activate = flip
    agent._exec_primitive({"action": "key", "key": "ctrl+Home"})
    assert driver.keys == ["ctrl+Home"]
    assert agent.focus_app == "gnumeric"


def test_guard_refuses_when_launched_app_never_appears(rig):
    agent, driver, _events = rig
    driver.windows = ["Form"]
    driver.elements = [el("formapp", "Form", focused=True)]
    agent._exec_primitive({"action": "launch", "command": "gnumeric"})
    with pytest.raises(StepFailure, match="did not present a window"):
        agent._exec_primitive({"action": "key", "key": "ctrl+s"})
    assert driver.keys == []


def test_guard_stale_webkit_focus_does_not_block(rig):
    # Regression (installed-app recording run): WebKitGTK keeps
    # reporting its web view as the AT-SPI focused element after the
    # DeskDora window loses activation.  The WM active window —
    # where keystrokes actually land — belongs to the target app,
    # so the guard must pass.
    agent, driver, _events = rig
    agent.focus_app, agent.focus_window = "thunar", "messy - Thunar"
    driver.elements = [el("window.py", "DeskDora", focused=True),
                       el("thunar", "messy - Thunar", focused=False)]
    driver.active_title = "messy - Thunar"
    driver.activate_ok = False
    agent._exec_primitive({"action": "key", "key": "ctrl+shift+n"})
    assert driver.keys == ["ctrl+shift+n"]


def test_guard_adoption_ignores_deskdora_furniture(rig):
    # v3.2 regression (measured live): the floating control window
    # maps when a run starts — after the launch snapshot — so its
    # elements look like "the new window".  Adoption must skip
    # DeskDora's own furniture and pin the launched app instead.
    agent, driver, _events = rig
    driver.elements = [el("deskdora", "DeskDora", focused=True)]
    agent._exec_primitive({"action": "launch", "command": "thunar /tmp/x"})
    # overlay appears (first in enumeration), Thunar appears with
    # no AT-SPI focused element yet
    driver.elements = [
        el("window.py", "DeskDora Control", role="push button",
           name="Stop"),
        el("Thunar", "messy - Thunar", role="text", name="notes.txt"),
    ]
    driver.active_title = "messy - Thunar"
    agent._exec_primitive({"action": "type", "text": "x"})
    assert agent.focus_app == "Thunar"
    assert agent.focus_window == "messy - Thunar"


def test_guard_initial_pin_ignores_furniture(rig):
    agent, driver, _events = rig
    driver.elements = [
        el("window.py", "DeskDora Control", focused=True,
           role="push button", name="Stop")]
    driver.active_title = "DeskDora Control"
    agent._guard_focus()
    assert agent.focus_app is None and agent.focus_window is None
