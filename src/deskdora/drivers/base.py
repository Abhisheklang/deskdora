"""Desktop driver abstraction.

DeskDora's agent loop never touches a platform API directly: it talks
to a *driver*.  A driver knows how to observe the desktop (an
accessibility-tree snapshot in the shared ``perception`` data model)
and how to perform input (click, type, key, scroll), launch apps,
capture screenshots, and read the clipboard on one platform.

Drivers shipped:

* ``linux-x11``     — AT-SPI + xdotool on X11.  Verified in the rig.
* ``linux-wayland`` — AT-SPI + xdg-desktop-portal (Screenshot and
  RemoteDesktop).  Implemented; the level actually achieved in the
  test rig is probed and reported, never assumed.
* ``macos``         — Accessibility (AX) + Quartz events.
  Implemented, **not yet run on real hardware**.
* ``windows``       — UI Automation + SendInput.
  Implemented, **not yet run on real hardware**.

Honesty rule: ``implemented`` lists the methods a driver *contains
code for*; ``verified_on_hardware`` says whether that code has ever
run against the real platform.  The two are never conflated — the UI
and the reports show both.
"""
from __future__ import annotations

import abc

# Every plan action, and the driver methods it needs.  The conformance
# tests check each driver against this table: a driver that claims a
# method in ``implemented`` must actually provide it, and the planner's
# step vocabulary must never outgrow the table.
ACTION_REQUIREMENTS = {
    "launch": ("launch",),
    "click": ("observe", "click"),
    "double_click": ("observe", "double_click"),
    "click_at": ("click",),
    "type": ("type_text",),
    "key": ("key",),
    "scroll": ("scroll",),
    "wait": (),
    "extract": ("observe",),
    "read_clipboard": ("read_clipboard",),
    "build_sheet": ("observe", "key", "type_text"),
    "done": (),
    "fail": (),
}

ALL_METHODS = frozenset({
    "observe", "refind", "click", "double_click", "type_text", "key",
    "scroll", "launch", "screenshot", "read_clipboard",
    "active_window_title", "activate_window",
})


class DriverError(RuntimeError):
    """A driver operation failed."""


class CapabilityError(DriverError):
    """The driver/platform cannot perform this operation (as opposed
    to it failing transiently).  The agent turns this into an honest
    step failure, never a silent workaround."""


class DesktopDriver(abc.ABC):
    """The interface every platform driver implements."""

    id = "abstract"
    display_name = "Abstract driver"
    #: Methods this driver contains real code for.
    implemented: frozenset = frozenset()
    #: Has this driver's code ever run on the real platform?
    verified_on_hardware = False
    #: Short human-readable status, shown in the UI and reports.
    hardware_note = ""

    # -- observation ---------------------------------------------------
    def observe(self):
        raise CapabilityError(f"{self.id}: observe not implemented")

    def refind(self, element):
        raise CapabilityError(f"{self.id}: refind not implemented")

    # -- input -----------------------------------------------------------
    def click(self, x, y):
        raise CapabilityError(f"{self.id}: click not implemented")

    def double_click(self, x, y):
        raise CapabilityError(f"{self.id}: double_click not implemented")

    def type_text(self, text):
        raise CapabilityError(f"{self.id}: type_text not implemented")

    def key(self, combo):
        raise CapabilityError(f"{self.id}: key not implemented")

    def scroll(self, x, y, direction="down"):
        raise CapabilityError(f"{self.id}: scroll not implemented")

    # -- system ----------------------------------------------------------
    def launch(self, command):
        raise CapabilityError(f"{self.id}: launch not implemented")

    def screenshot(self, path):
        raise CapabilityError(f"{self.id}: screenshot not implemented")

    def read_clipboard(self):
        raise CapabilityError(f"{self.id}: read_clipboard not implemented")

    def active_window_title(self):
        """Title of the currently focused window, or "" when the
        platform offers no way to know (e.g. Wayland)."""
        return ""

    def activate_window(self, title):
        """Best-effort raise of a window by title.  Returns success."""
        return False

    # -- introspection ---------------------------------------------------
    def supports_action(self, action):
        needed = ACTION_REQUIREMENTS.get(action, ())
        return all(m in self.implemented for m in needed)

    def capabilities(self):
        """Static capability report (code-level).  Drivers with a
        runtime consent layer (Wayland portals) override this to add
        live probe results under ``runtime``."""
        return {
            "driver": self.id,
            "display_name": self.display_name,
            "implemented": sorted(self.implemented),
            "verified_on_hardware": self.verified_on_hardware,
            "hardware_note": self.hardware_note,
            "actions_supported": {
                a: self.supports_action(a) for a in ACTION_REQUIREMENTS
            },
        }
