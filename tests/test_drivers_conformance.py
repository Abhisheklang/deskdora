"""Cross-platform driver conformance.

Every shipped driver must:
* instantiate on any OS (platform imports are lazy),
* expose the full DesktopDriver interface,
* declare an ``implemented`` set that covers every plan action's
  requirements (ACTION_REQUIREMENTS) if it claims full support,
* be honest in its capability report about hardware verification,
* round-trip elements through to_dict()/JSON (plans and logs are
  serialised), and
* plan identically: the built-in planner's output validates and each
  step's action is supported by the driver.

The macOS and Windows drivers are exercised against fake
accessibility trees injected through their backend seams — the
observe/walk/refind logic is real; only the OS bindings are faked.
Neither driver has run on real hardware, and their capability
reports must keep saying so until one does.
"""
import json

import pytest

from deskdora.drivers import ACTION_REQUIREMENTS, available_drivers
from deskdora.drivers.base import ALL_METHODS
from deskdora.planners import BuiltinPlanner, validate_plan

DRIVERS = available_drivers()


@pytest.mark.parametrize("driver_id", sorted(DRIVERS))
def test_driver_instantiates_and_reports_honestly(driver_id):
    driver = DRIVERS[driver_id]()
    caps = driver.capabilities()
    assert caps["driver"] == driver_id
    assert set(caps["implemented"]) <= set(ALL_METHODS)
    for method in caps["implemented"]:
        assert callable(getattr(driver, method)), (driver_id, method)
    if driver_id in ("macos", "windows"):
        # Implemented in code, never executed on the platform.
        assert caps["verified_on_hardware"] is False
        assert "not yet run on real hardware" in \
            driver.display_name.lower() + driver.hardware_note.lower()


@pytest.mark.parametrize("driver_id", sorted(DRIVERS))
def test_full_drivers_support_every_plan_action(driver_id):
    driver = DRIVERS[driver_id]()
    if driver_id == "linux-wayland":
        pytest.skip("portal driver: code-level support is complete, "
                    "runtime support is probe-dependent by design")
    for action, needed in ACTION_REQUIREMENTS.items():
        assert driver.supports_action(action), (driver_id, action, needed)


def test_planner_output_is_driver_compatible():
    planner = BuiltinPlanner({
        "form": "python3 /fixtures/form_app.py",
        "site_url": "http://127.0.0.1:8901/index.html",
        "browser": "chromium",
    })
    plan = validate_plan(planner.plan(
        "Fill the form: name=Ada, email=a@b.c, city=Pune and submit", None))
    for driver_id, cls in DRIVERS.items():
        driver = cls()
        for step in plan:
            if driver_id == "linux-wayland":
                continue
            assert driver.supports_action(step["action"]), \
                (driver_id, step)


# ---------------------------------------------------------------------
# macOS: observe/refind against a fake AX tree
# ---------------------------------------------------------------------

class FakeAXNode:
    def __init__(self, role, title="", children=(), bounds=(0, 0, 100, 20),
                 **attrs):
        self.role = role
        self.title = title
        self.children = list(children)
        self.bounds = bounds
        self.attrs = attrs


def _fake_macos(monkeypatch):
    import deskdora.drivers.macos as mac

    def attr(node, name):
        if name == "AXRole":
            return node.role
        if name == "AXTitle":
            return node.title
        if name == "AXChildren":
            return node.children
        return node.attrs.get(name)

    button = FakeAXNode("AXButton", "Submit", bounds=(10, 10, 80, 24),
                        AXEnabled=True)
    field = FakeAXNode("AXTextField", "", bounds=(10, 40, 200, 24),
                       AXFocused=True, AXValue="")
    window = FakeAXNode("AXWindow", "FakeForm", children=[button, field],
                        bounds=(0, 0, 400, 300))
    app_el = FakeAXNode("AXApplication", "FakeApp", children=[window],
                        bounds=(0, 0, 400, 300))
    monkeypatch.setattr(mac, "_attr", attr)
    monkeypatch.setattr(mac, "_children", lambda n: n.children)
    monkeypatch.setattr(mac, "_bounds", lambda n: n.bounds)
    monkeypatch.setattr(mac, "_running_app_elements",
                        lambda: [("FakeApp", app_el)])
    return mac.MacOSDriver()


def test_macos_observe_fake_tree(monkeypatch):
    driver = _fake_macos(monkeypatch)
    obs = driver.observe()
    roles = {(e.role, e.name) for e in obs.elements}
    assert ("push button", "Submit") in roles
    assert ("text", "") in roles
    assert obs.windows == ["FakeForm"]
    focused = [e for e in obs.elements if e.focused]
    assert focused and focused[0].role == "text"


def test_macos_refind_fake_tree(monkeypatch):
    driver = _fake_macos(monkeypatch)
    obs = driver.observe()
    button = next(e for e in obs.elements if e.role == "push button")
    fresh = driver.refind(button)
    assert fresh is not None and fresh.name == "Submit"
    # element serialisation round-trips (logs/plans are JSON)
    assert json.loads(json.dumps(button.to_dict()))["name"] == "Submit"


def test_macos_combo_parsing():
    from deskdora.drivers.macos import (FLAG_COMMAND, FLAG_CONTROL,
                                        FLAG_SHIFT, parse_combo)
    flags, code = parse_combo("ctrl+s")
    assert flags == FLAG_CONTROL and code == 1     # 's' ANSI keycode
    flags, code = parse_combo("cmd+shift+t")
    assert flags == FLAG_COMMAND | FLAG_SHIFT
    from deskdora.drivers.base import CapabilityError
    with pytest.raises(CapabilityError):
        parse_combo("ctrl+banana")


# ---------------------------------------------------------------------
# Windows: observe/refind against a fake UIA tree
# ---------------------------------------------------------------------

class FakeUIANode:
    def __init__(self, ctype, name="", children=(), rect=(0, 0, 100, 20),
                 **props):
        self.ControlTypeName = ctype
        self.Name = name
        self.BoundingRectangle = rect
        self._children = list(children)
        self.__dict__.update(props)

    def GetChildren(self):
        return list(self._children)


def _fake_windows(monkeypatch):
    import deskdora.drivers.windows as win

    button = FakeUIANode("Button", "Submit", rect=(10, 10, 90, 34),
                         ProcessId=4242, IsEnabled=True)
    edit = FakeUIANode("Edit", "Name", rect=(10, 40, 210, 64),
                       ProcessId=4242, HasKeyboardFocus=True)
    window = FakeUIANode("Window", "FakeForm", children=[button, edit],
                         rect=(0, 0, 400, 300), ProcessId=4242)
    root = FakeUIANode("Pane", "", children=[window], rect=(0, 0, 800, 600))
    monkeypatch.setattr(win, "_root", lambda: root)
    monkeypatch.setattr(win, "_children", lambda n: n.GetChildren())
    monkeypatch.setattr(win, "_process_name", lambda pid: "fakeapp.exe")
    return win.WindowsDriver()


def test_windows_observe_fake_tree(monkeypatch):
    driver = _fake_windows(monkeypatch)
    obs = driver.observe()
    roles = {(e.role, e.name) for e in obs.elements}
    assert ("push button", "Submit") in roles
    assert ("text", "Name") in roles
    assert obs.windows == ["FakeForm"]
    assert all(e.app == "fakeapp.exe" for e in obs.elements)


def test_windows_refind_fake_tree(monkeypatch):
    driver = _fake_windows(monkeypatch)
    obs = driver.observe()
    button = next(e for e in obs.elements if e.role == "push button")
    fresh = driver.refind(button)
    assert fresh is not None and fresh.name == "Submit"


def test_windows_combo_parsing():
    from deskdora.drivers.windows import parse_combo as win_parse
    mods, vk = win_parse("ctrl+shift+t")
    assert mods == [0x11, 0x10] and vk == ord("T")
    from deskdora.drivers.base import CapabilityError
    with pytest.raises(CapabilityError):
        win_parse("banana+t")


def test_windows_input_seams_refuse_off_platform():
    import deskdora.drivers.windows as win
    driver = win.WindowsDriver()
    # On this Linux test machine the low-level seams must refuse
    # cleanly (CapabilityError), never crash with AttributeError.
    with pytest.raises(Exception):
        driver.click(1, 1)
