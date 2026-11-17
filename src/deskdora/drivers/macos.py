"""macOS driver — Accessibility (AX) + Quartz event synthesis.

STATUS: **implemented, not yet run on real hardware.**  This module
was written and unit-tested on Linux against a fake AX tree; it has
never executed on a Mac.  The capability report says so
(``verified_on_hardware == False``) and so does the UI.  First
hardware run is expected to shake out platform details (AX attribute
availability per app, event-tap permissions — macOS requires the
Accessibility permission for the host process, and Screen Recording
permission for ``screencapture`` to capture other apps' windows).

Design notes:

* Perception walks the AX tree of every running application and maps
  AX roles onto DeskDora's shared role vocabulary, producing the same
  ``Observation``/``Element`` model the planner already speaks.
  Elements carry a child-index ``path`` (app index first, apps sorted
  by pid for determinism) so ``refind`` can re-locate them.
* Input uses Quartz ``CGEvent`` posted to the HID event tap —
  mouse buttons, scroll wheels, and keyboard events with modifier
  flags.  Typing uses ``CGEventKeyboardSetUnicodeString`` so
  arbitrary text does not depend on a keycode-per-character table.
* Screenshots use the system ``screencapture`` tool; the clipboard
  uses ``pbpaste``; app launching runs the plan's command through
  the shell (macOS known-app mappings use ``open -a`` commands),
  matching the Linux drivers' semantics.
* Every pyobjc import is lazy (inside the backend functions), so
  this module imports cleanly on any platform — that is what lets
  the conformance tests exercise it on Linux.
"""
from __future__ import annotations

import subprocess
import time

from ..perception import Element, Observation
from .base import ALL_METHODS, CapabilityError, DesktopDriver, DriverError

# AX role -> DeskDora role vocabulary (perception.ACTIONABLE_ROLES).
ROLE_MAP = {
    "AXButton": "push button",
    "AXMenuButton": "push button",
    "AXPopUpButton": "combo box",
    "AXComboBox": "combo box",
    "AXTextField": "text",
    "AXTextArea": "text",
    "AXSecureTextField": "password text",
    "AXSearchField": "text",
    "AXCheckBox": "check box",
    "AXRadioButton": "radio button",
    "AXMenu": "menu",
    "AXMenuItem": "menu item",
    "AXLink": "link",
    "AXTab": "page tab",
    "AXRow": "list item",
    "AXCell": "table cell",
    "AXSlider": "slider",
    "AXIncrementor": "spin button",
    "AXStaticText": "label",
    "AXImage": "icon",
    "AXList": "list item",
    "AXOutline": "tree item",
    "AXToolbarButton": "tool bar button",
}

# CGEvent modifier flags (stable ABI constants from CGEventTypes.h).
FLAG_SHIFT = 0x20000
FLAG_CONTROL = 0x40000
FLAG_ALT = 0x80000
FLAG_COMMAND = 0x100000
MODIFIERS = {
    "shift": FLAG_SHIFT, "ctrl": FLAG_CONTROL, "control": FLAG_CONTROL,
    "alt": FLAG_ALT, "option": FLAG_ALT, "cmd": FLAG_COMMAND,
    "command": FLAG_COMMAND, "super": FLAG_COMMAND, "meta": FLAG_COMMAND,
}

# ANSI virtual keycodes for named keys and characters (US layout).
KEYCODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49,
    "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "page_up": 116, "page_down": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
for _ch, _code in zip("1234567890", (18, 19, 20, 21, 23, 22, 26, 28, 25, 29)):
    KEYCODES[_ch] = _code
for _ch, _code in {
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
        "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
        "y": 16, "t": 17, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37,
        "j": 38, "k": 40, "n": 45, "m": 46}.items():
    KEYCODES[_ch] = _code


def parse_combo(combo):
    """Parse an xdotool-style combo ('ctrl+shift+t') into
    (modifier_flags, keycode).  Pure function — unit-tested on Linux.
    Raises CapabilityError for anything unmappable."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise CapabilityError("empty key combo")
    flags = 0
    for part in parts[:-1]:
        if part not in MODIFIERS:
            raise CapabilityError(f"unknown modifier {part!r} in {combo!r}")
        flags |= MODIFIERS[part]
    key = parts[-1]
    if key in MODIFIERS and len(parts) == 1:
        raise CapabilityError(f"combo {combo!r} has no key")
    if key not in KEYCODES:
        raise CapabilityError(f"no macOS keycode for key {key!r}")
    return flags, KEYCODES[key]


def map_role(ax_role):
    return ROLE_MAP.get(ax_role, "")


# --------------------------------------------------------------------------
# AX backend — the only functions that touch pyobjc.  Tests replace
# these with a fake tree; on a real Mac they call ApplicationServices.
# --------------------------------------------------------------------------

def _ax():
    try:
        import ApplicationServices as ax
        return ax
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise CapabilityError(
            "pyobjc ApplicationServices is not available — the macOS "
            "driver only runs on macOS") from exc


def _quartz():
    try:
        import Quartz
        return Quartz
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise CapabilityError(
            "pyobjc Quartz is not available — the macOS driver only "
            "runs on macOS") from exc


def _running_app_elements():
    """[(app_name, AXUIElement for the app)], sorted by pid."""
    ax = _ax()
    import AppKit
    apps = []
    workspace = AppKit.NSWorkspace.sharedWorkspace()
    for app in sorted(workspace.runningApplications(),
                      key=lambda a: a.processIdentifier()):
        if app.activationPolicy() != AppKit.NSApplicationActivationPolicyRegular:
            continue
        el = ax.AXUIElementCreateApplication(app.processIdentifier())
        apps.append((app.localizedName() or f"pid{app.processIdentifier()}",
                     el))
    return apps


def _attr(node, name):
    ax = _ax()
    err, value = ax.AXUIElementCopyAttributeValue(node, name, None)
    return value if err == 0 else None


def _children(node):
    return _attr(node, "AXChildren") or []


def _bounds(node):
    ax = _ax()
    pos = _attr(node, "AXPosition")
    size = _attr(node, "AXSize")
    if pos is None or size is None:
        return 0, 0, 0, 0
    _ok, point = ax.AXValueGetValue(pos, ax.kAXValueCGPointType, None)
    _ok2, sz = ax.AXValueGetValue(size, ax.kAXValueCGSizeType, None)
    if not _ok or not _ok2:
        return 0, 0, 0, 0
    return int(point.x), int(point.y), int(sz.width), int(sz.height)


class MacOSDriver(DesktopDriver):
    id = "macos"
    display_name = "macOS (Accessibility + Quartz) — not yet run on real hardware"
    implemented = ALL_METHODS
    verified_on_hardware = False
    hardware_note = ("Implemented and unit-tested against a fake AX "
                     "tree on Linux; NOT yet run on a real Mac. "
                     "Requires the Accessibility permission (and "
                     "Screen Recording for screenshots) on first use.")

    MAX_ELEMENTS = 400
    MAX_DEPTH = 14

    # -- perception ------------------------------------------------------
    def observe(self):
        elements = []
        windows = []
        texts = {}

        def walk(node, app_name, window_title, path, depth):
            if depth > self.MAX_DEPTH or len(elements) >= self.MAX_ELEMENTS:
                return
            ax_role = _attr(node, "AXRole") or ""
            role = map_role(ax_role)
            title = _attr(node, "AXTitle") or ""
            if ax_role == "AXWindow":
                window_title = title or window_title
                if window_title and window_title not in windows:
                    windows.append(window_title)
            if role:
                x, y, w, h = _bounds(node)
                if w > 0 and h > 0:
                    value = _attr(node, "AXValue")
                    label = ""
                    label_el = _attr(node, "AXTitleUIElement")
                    if label_el is not None:
                        label = (_attr(label_el, "AXTitle")
                                 or _attr(label_el, "AXValue") or "")
                    enabled = _attr(node, "AXEnabled")
                    elements.append(Element(
                        idx=len(elements), app=app_name,
                        window=window_title, role=role,
                        name=title or (_attr(node, "AXDescription") or ""),
                        label=str(label),
                        x=x, y=y, w=w, h=h,
                        editable=role in ("text", "password text"),
                        focused=bool(_attr(node, "AXFocused")),
                        sensitive=True if enabled is None else bool(enabled),
                        text=str(value)[:300] if role in ("text", "password text")
                        and value is not None else "",
                        path=tuple(path),
                    ))
                    if role == "label" and title:
                        key = window_title or app_name
                        texts[key] = (texts.get(key, "") + "\n" + title)
            for i, child in enumerate(_children(node)):
                walk(child, app_name, window_title, path + [i], depth + 1)

        for app_idx, (app_name, app_el) in enumerate(_running_app_elements()):
            walk(app_el, app_name, "", [app_idx], 0)
        return Observation(elements=elements, windows=windows, texts=texts,
                           taken_at=time.time())

    def refind(self, element):
        # Path first (apps sorted by pid, so indices are stable while
        # the app set is unchanged); signature search as fallback.
        try:
            apps = _running_app_elements()
            node = apps[element.path[0]][1]
            for seg in element.path[1:]:
                node = _children(node)[seg]
            if map_role(_attr(node, "AXRole") or "") == element.role:
                x, y, w, h = _bounds(node)
                if w > 0 and h > 0:
                    return Element(idx=element.idx, app=element.app,
                                   window=element.window, role=element.role,
                                   name=element.name, label=element.label,
                                   x=x, y=y, w=w, h=h, path=element.path)
        except Exception:
            pass
        obs = self.observe()
        for cand in obs.find(role=element.role, exact_name=element.name,
                             app_contains=element.app):
            if cand.label == element.label:
                return cand
        matches = obs.find(role=element.role, exact_name=element.name)
        return matches[0] if matches else None

    # -- input -------------------------------------------------------------
    def _post_mouse(self, x, y, event_type, button, click_state=1):
        q = _quartz()
        ev = q.CGEventCreateMouseEvent(None, event_type, (float(x), float(y)),
                                       button)
        if click_state != 1:
            q.CGEventSetIntegerValueField(ev, q.kCGMouseEventClickState,
                                          click_state)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def click(self, x, y):
        q = _quartz()
        self._post_mouse(x, y, q.kCGEventMouseMoved, q.kCGMouseButtonLeft)
        self._post_mouse(x, y, q.kCGEventLeftMouseDown, q.kCGMouseButtonLeft)
        self._post_mouse(x, y, q.kCGEventLeftMouseUp, q.kCGMouseButtonLeft)

    def double_click(self, x, y):
        q = _quartz()
        self._post_mouse(x, y, q.kCGEventLeftMouseDown, q.kCGMouseButtonLeft,
                         click_state=2)
        self._post_mouse(x, y, q.kCGEventLeftMouseUp, q.kCGMouseButtonLeft,
                         click_state=2)

    def _post_key(self, keycode, down, flags=0, text=None):
        q = _quartz()
        ev = q.CGEventCreateKeyboardEvent(None, keycode, down)
        if flags:
            q.CGEventSetFlags(ev, flags)
        if text is not None:
            q.CGEventKeyboardSetUnicodeString(ev, len(text), text)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    def type_text(self, text):
        for ch in text:
            self._post_key(0, True, text=ch)
            self._post_key(0, False, text=ch)

    def key(self, combo):
        flags, keycode = parse_combo(combo)
        self._post_key(keycode, True, flags)
        self._post_key(keycode, False, flags)

    def scroll(self, x, y, direction="down"):
        q = _quartz()
        self._post_mouse(x, y, q.kCGEventMouseMoved, q.kCGMouseButtonLeft)
        ev = q.CGEventCreateScrollWheelEvent(
            None, q.kCGScrollEventUnitLine, 1,
            -3 if direction == "down" else 3)
        q.CGEventPost(q.kCGHIDEventTap, ev)

    # -- system --------------------------------------------------------------
    def launch(self, command):
        subprocess.Popen(command, shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return command

    def screenshot(self, path):
        res = subprocess.run(["screencapture", "-x", path],
                             capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise DriverError(f"screencapture failed: {res.stderr.strip()}")
        return path

    def read_clipboard(self):
        res = subprocess.run(["pbpaste"], capture_output=True, text=True,
                             timeout=15)
        if res.returncode != 0:
            raise DriverError(f"pbpaste failed: {res.stderr.strip()}")
        return res.stdout

    def active_window_title(self):
        try:
            import AppKit
            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return ""
            ax = _ax()
            app_el = ax.AXUIElementCreateApplication(app.processIdentifier())
            focused = _attr(app_el, "AXFocusedWindow")
            if focused is None:
                return ""
            return _attr(focused, "AXTitle") or ""
        except Exception:
            return ""

    def activate_window(self, title):
        if not title:
            return False
        try:
            import AppKit
            for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
                el = _ax().AXUIElementCreateApplication(
                    app.processIdentifier())
                for win in _attr(el, "AXWindows") or []:
                    if _attr(win, "AXTitle") == title:
                        app.activateWithOptions_(
                            AppKit.NSApplicationActivateIgnoringOtherApps)
                        return True
        except Exception:
            pass
        return False
