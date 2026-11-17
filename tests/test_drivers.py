"""Driver registry, X11 driver contract, and the Wayland portal
driver exercised against a fake D-Bus transport (the request
construction and response handling are real code; only the bus is
scripted)."""
import os

import pytest

from deskdora.drivers import (ACTION_REQUIREMENTS, CapabilityError,
                              DriverError, available_drivers,
                              detect_driver_id, get_driver)
from deskdora.drivers.linux_wayland import LinuxWaylandDriver, PortalBus


def test_registry_detects_linux_x11_by_default(monkeypatch):
    monkeypatch.delenv("DESKDORA_DRIVER", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert detect_driver_id() == "linux-x11"
    driver = get_driver()
    assert driver.id == "linux-x11"


def test_registry_rejects_unknown_driver():
    with pytest.raises(DriverError):
        get_driver("amiga")


def test_x11_driver_supports_every_action():
    driver = get_driver("linux-x11")
    caps = driver.capabilities()
    assert caps["verified_on_hardware"] is True
    for action in ACTION_REQUIREMENTS:
        assert caps["actions_supported"][action] is True, action


class FakeBus(PortalBus):
    """Scripted portal transport.  Records every call; Start and
    Screenshot answers are configurable."""

    def __init__(self, start_code=0, devices=3, streams=((42, {}),),
                 shot_uri=None):
        self.calls = []       # (kind, interface, method, signature, params)
        self.start_code = start_code
        self.devices = devices
        self.streams = list(streams)
        self.shot_uri = shot_uri

    def name_has_owner(self, name):
        return True

    def call(self, interface, method, signature, params):
        self.calls.append(("call", interface, method, signature, params))
        return ()

    def create_session(self, interface, timeout=30):
        self.calls.append(("call", interface, "CreateSession",
                           "(a{sv})", ()))
        return "/org/freedesktop/portal/desktop/session/fake"

    def request(self, interface, method, signature, params, timeout=60):
        self.calls.append(("request", interface, method, signature, params))
        if method == "Screenshot":
            return 0, {"uri": self.shot_uri}
        if method == "Start":
            if self.start_code != 0:
                return self.start_code, {}
            return 0, {"devices": self.devices, "streams": self.streams}
        raise AssertionError(f"unexpected request {method}")

    def methods(self):
        return [c[2] for c in self.calls]


@pytest.fixture
def wayland(tmp_path):
    shot = tmp_path / "portal-shot.png"
    shot.write_bytes(b"\x89PNG-fake")
    bus = FakeBus(shot_uri="file://" + str(shot))
    return LinuxWaylandDriver(bus=bus), bus


def test_wayland_screenshot_via_portal(wayland, tmp_path):
    driver, bus = wayland
    out = tmp_path / "out.png"
    driver.screenshot(str(out))
    assert out.read_bytes() == b"\x89PNG-fake"
    assert "Screenshot" in bus.methods()


def test_wayland_click_uses_remote_desktop(wayland):
    driver, bus = wayland
    driver.click(100, 200)
    methods = bus.methods()
    assert methods[:3] == ["CreateSession", "SelectDevices", "Start"]
    motion = [c for c in bus.calls
              if c[2] == "NotifyPointerMotionAbsolute"]
    assert motion and motion[0][4][2] == 42       # the granted stream
    assert motion[0][4][3:] == (100.0, 200.0)
    buttons = [c for c in bus.calls if c[2] == "NotifyPointerButton"]
    assert [b[4][3] for b in buttons] == [1, 0]    # press, release


def test_wayland_key_combo_keycodes(wayland):
    driver, bus = wayland
    driver.key("ctrl+s")
    events = [c[4][2] for c in bus.calls
              if c[2] == "NotifyKeyboardKeycode"]
    states = [c[4][3] for c in bus.calls
              if c[2] == "NotifyKeyboardKeycode"]
    assert events == [29, 31, 31, 29]              # ctrl, s, s, ctrl
    assert states == [1, 1, 0, 0]


def test_wayland_type_text_with_shift(wayland):
    driver, bus = wayland
    driver.type_text("Ab")
    codes = [c[4][2] for c in bus.calls
             if c[2] == "NotifyKeyboardKeycode"]
    assert codes[0] == 42                          # shift down for 'A'
    assert 30 in codes                             # 'a' keycode


def test_wayland_type_text_rejects_untypeable(wayland):
    driver, _bus = wayland
    with pytest.raises(CapabilityError):
        driver.type_text("€")


def test_wayland_unknown_key_rejected(wayland):
    driver, _bus = wayland
    with pytest.raises(CapabilityError):
        driver.key("banana")


def test_wayland_start_refused_is_capability_error(tmp_path):
    bus = FakeBus(start_code=1)
    driver = LinuxWaylandDriver(bus=bus)
    with pytest.raises(CapabilityError):
        driver.click(1, 1)


def test_wayland_no_stream_means_no_absolute_motion(tmp_path):
    bus = FakeBus(streams=())
    driver = LinuxWaylandDriver(bus=bus)
    with pytest.raises(CapabilityError):
        driver.click(1, 1)


def test_wayland_clipboard_needs_wl_paste(wayland, monkeypatch):
    driver, _bus = wayland
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(CapabilityError):
        driver.read_clipboard()


def test_wayland_focus_apis_honestly_absent(wayland):
    driver, _bus = wayland
    assert driver.active_window_title() == ""
    assert driver.activate_window("anything") is False
    assert "activate_window" not in driver.implemented
