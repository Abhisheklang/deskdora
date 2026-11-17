"""Windows driver — UI Automation + SendInput.

STATUS: **implemented, not yet run on real hardware.**  Written and
unit-tested on Linux against a fake UIA tree; it has never executed
on a Windows machine.  The capability report says so
(``verified_on_hardware == False``) and so does the UI.

Design notes:

* Perception walks the UI Automation tree from the desktop root and
  maps UIA control types onto DeskDora's shared role vocabulary,
  producing the same ``Observation``/``Element`` model as every other
  driver.  Elements carry a child-index ``path`` from the UIA root so
  ``refind`` can re-locate them (with a signature-search fallback).
* The UIA bindings go through the ``uiautomation`` package (a thin
  wrapper over the system's UIAutomationCore COM API) — accessed only
  through the small backend seam below (``_children``, ``_prop``,
  ``_root`` …), which is what the Linux-side tests fake.
* Input uses Win32 ``SendInput`` via ctypes: mouse buttons and wheel,
  key combos from virtual-key codes, and Unicode text via
  ``KEYEVENTF_UNICODE`` (no layout-dependent keycode table needed
  for typing).  ``ctypes.windll`` is only touched inside the seam
  functions, so the module imports cleanly anywhere.
* Screenshots use PIL's ``ImageGrab``; the clipboard is read with
  PowerShell's ``Get-Clipboard``; launching runs the plan's command
  through the shell, matching the other drivers' semantics.
"""
from __future__ import annotations

import subprocess
import sys
import time

from ..perception import Element, Observation
from .base import ALL_METHODS, CapabilityError, DesktopDriver, DriverError

# UIA control type -> DeskDora role vocabulary.
ROLE_MAP = {
    "Button": "push button",
    "SplitButton": "split button",
    "CheckBox": "check box",
    "RadioButton": "radio button",
    "Edit": "text",
    "Document": "text",
    "ComboBox": "combo box",
    "Hyperlink": "link",
    "Menu": "menu",
    "MenuItem": "menu item",
    "TabItem": "page tab",
    "DataItem": "table cell",
    "ListItem": "list item",
    "TreeItem": "tree item",
    "Slider": "slider",
    "Spinner": "spin button",
    "ToolBar": "tool bar button",
    "Image": "icon",
    "Text": "label",
    "List": "list item",
}

# Virtual-key codes (Win32).  Modifiers first, then named keys/chars.
VK = {
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "super": 0x5B, "meta": 0x5B, "win": 0x5B,
    "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "escape": 0x1B, "esc": 0x1B,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "page_down": 0x22,
    "insert": 0x2D,
}
for _i in range(12):
    VK[f"f{_i + 1}"] = 0x70 + _i
for _ch in "abcdefghijklmnopqrstuvwxyz0123456789":
    VK[_ch] = ord(_ch.upper())

MODIFIER_VKS = {VK[k] for k in ("shift", "ctrl", "control", "alt",
                                "super", "meta", "win")}


def parse_combo(combo):
    """Parse an xdotool-style combo ('ctrl+shift+t') into
    ([modifier VKs], key VK).  Pure — unit-tested on Linux."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise CapabilityError("empty key combo")
    mods = []
    for part in parts[:-1]:
        if part not in VK or VK[part] not in MODIFIER_VKS:
            raise CapabilityError(f"unknown modifier {part!r} in {combo!r}")
        mods.append(VK[part])
    key = parts[-1]
    if key not in VK:
        raise CapabilityError(f"no virtual-key code for {key!r}")
    return mods, VK[key]


def map_role(control_type):
    return ROLE_MAP.get(control_type, "")


# --------------------------------------------------------------------------
# Backend seams — the only functions that touch Windows APIs.  Tests
# replace them with a fake UIA tree.
# --------------------------------------------------------------------------

def _require_windows():
    if not sys.platform.startswith("win"):
        raise CapabilityError(
            "the Windows driver only runs on Windows "
            f"(this platform is {sys.platform})")


def _uia():
    _require_windows()
    try:
        import uiautomation
        return uiautomation
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise CapabilityError(
            "the 'uiautomation' package is required on Windows") from exc


def _root():
    return _uia().GetRootControl()


def _children(node):
    return node.GetChildren()


def _prop(node, name):
    return getattr(node, name, None)


def _control_type(node):
    return _prop(node, "ControlTypeName") or ""


def _process_name(pid):
    """Best-effort process image name via kernel32 (no psutil dep)."""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return f"pid{pid}"
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf,
                                                 ctypes.byref(size))
        kernel32.CloseHandle(handle)
        if ok:
            return buf.value.rsplit("\\", 1)[-1]
    except Exception:
        pass
    return f"pid{pid}"


def _send_input(events):
    """events: list of dicts describing INPUT records; built by the
    caller so this seam stays a thin ctypes translation."""
    _require_windows()
    import ctypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_ulong),
                    ("dwFlags", ctypes.c_ulong),
                    ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("u", _UNION)]

    records = []
    for ev in events:
        rec = INPUT()
        if ev["kind"] == "mouse":
            rec.type = 0
            rec.u.mi = MOUSEINPUT(ev.get("dx", 0), ev.get("dy", 0),
                                  ev.get("data", 0), ev["flags"], 0, None)
        else:
            rec.type = 1
            rec.u.ki = KEYBDINPUT(ev.get("vk", 0), ev.get("scan", 0),
                                  ev["flags"], 0, None)
        records.append(rec)
    arr = (INPUT * len(records))(*records)
    sent = ctypes.windll.user32.SendInput(len(records), arr,
                                          ctypes.sizeof(INPUT))
    if sent != len(records):
        raise DriverError(f"SendInput sent {sent}/{len(records)} events")


def _set_cursor(x, y):
    _require_windows()
    import ctypes
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def _foreground_title():
    _require_windows()
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _raise_window(title):
    _require_windows()
    import ctypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _enum(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        if buf.value == title:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_enum, 0)
    if found:
        user32.SetForegroundWindow(found[0])
        return True
    return False


def _grab_image(path):
    _require_windows()
    try:
        from PIL import ImageGrab
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise CapabilityError(
            "Pillow is required for Windows screenshots") from exc
    ImageGrab.grab(all_screens=True).save(path)
    return path


class WindowsDriver(DesktopDriver):
    id = "windows"
    display_name = "Windows (UI Automation + SendInput) — not yet run on real hardware"
    implemented = ALL_METHODS
    verified_on_hardware = False
    hardware_note = ("Implemented and unit-tested against a fake UIA "
                     "tree on Linux; NOT yet run on a real Windows "
                     "machine.")

    MAX_ELEMENTS = 400
    MAX_DEPTH = 14

    # -- perception ------------------------------------------------------
    def observe(self):
        elements = []
        windows = []
        texts = {}

        def walk(node, window_title, path, depth):
            if depth > self.MAX_DEPTH or len(elements) >= self.MAX_ELEMENTS:
                return
            role = map_role(_control_type(node))
            name = _prop(node, "Name") or ""
            rect = _prop(node, "BoundingRectangle")
            if rect is not None:
                left, top, right, bottom = rect
            else:
                left = top = right = bottom = 0
            ctype = _control_type(node)
            if ctype == "Window":
                window_title = name or window_title
                if window_title and window_title not in windows:
                    windows.append(window_title)
            if role and right > left and bottom > top:
                pid = _prop(node, "ProcessId") or 0
                elements.append(Element(
                    idx=len(elements),
                    app=_process_name(pid) if pid else "",
                    window=window_title, role=role, name=name,
                    x=int(left), y=int(top),
                    w=int(right - left), h=int(bottom - top),
                    editable=role in ("text", "password text"),
                    focused=bool(_prop(node, "HasKeyboardFocus")),
                    sensitive=bool(_prop(node, "IsEnabled")
                                   if _prop(node, "IsEnabled") is not None
                                   else True),
                    path=tuple(path),
                ))
                if role == "label" and name:
                    key = window_title or "windows"
                    texts[key] = (texts.get(key, "") + "\n" + name)
            for i, child in enumerate(_children(node)):
                walk(child, window_title, path + [i], depth + 1)

        walk(_root(), "", [], 0)
        return Observation(elements=elements, windows=windows, texts=texts,
                           taken_at=time.time())

    def refind(self, element):
        try:
            node = _root()
            for seg in element.path:
                node = _children(node)[seg]
            if map_role(_control_type(node)) == element.role:
                rect = _prop(node, "BoundingRectangle")
                if rect is not None:
                    left, top, right, bottom = rect
                    return Element(idx=element.idx, app=element.app,
                                   window=element.window, role=element.role,
                                   name=element.name, label=element.label,
                                   x=int(left), y=int(top),
                                   w=int(right - left),
                                   h=int(bottom - top),
                                   path=element.path)
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
    # SendInput mouse flags.
    _MOVE = 0x0001
    _LDOWN = 0x0002
    _LUP = 0x0004
    _WHEEL = 0x0800
    _KEYUP = 0x0002
    _UNICODE = 0x0004

    def click(self, x, y):
        _set_cursor(x, y)
        _send_input([{"kind": "mouse", "flags": self._LDOWN},
                     {"kind": "mouse", "flags": self._LUP}])

    def double_click(self, x, y):
        self.click(x, y)
        self.click(x, y)

    def scroll(self, x, y, direction="down"):
        _set_cursor(x, y)
        delta = -120 * 3 if direction == "down" else 120 * 3
        _send_input([{"kind": "mouse", "flags": self._WHEEL,
                      "data": delta & 0xFFFFFFFF}])

    def key(self, combo):
        mods, vk = parse_combo(combo)
        events = [{"kind": "key", "vk": m, "flags": 0} for m in mods]
        events.append({"kind": "key", "vk": vk, "flags": 0})
        events.append({"kind": "key", "vk": vk, "flags": self._KEYUP})
        events += [{"kind": "key", "vk": m, "flags": self._KEYUP}
                   for m in reversed(mods)]
        _send_input(events)

    def type_text(self, text):
        events = []
        for ch in text:
            events.append({"kind": "key", "vk": 0, "scan": ord(ch),
                           "flags": self._UNICODE})
            events.append({"kind": "key", "vk": 0, "scan": ord(ch),
                           "flags": self._UNICODE | self._KEYUP})
        _send_input(events)

    # -- system --------------------------------------------------------------
    def launch(self, command):
        subprocess.Popen(command, shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return command

    def screenshot(self, path):
        return _grab_image(path)

    def read_clipboard(self):
        _require_windows()
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            raise DriverError(f"Get-Clipboard failed: {res.stderr.strip()}")
        return res.stdout

    def active_window_title(self):
        try:
            return _foreground_title()
        except Exception:
            return ""

    def activate_window(self, title):
        if not title:
            return False
        try:
            return _raise_window(title)
        except Exception:
            return False
