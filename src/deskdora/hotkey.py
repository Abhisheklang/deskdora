"""Global hotkey (v3.7.3): summon DeskDora from anywhere.

The binding lives in the window process (it owns the window it
raises) and is driven by the ``hotkey`` config section:
``{"enabled": bool, "combo": "Ctrl+Alt+D"}``.

Platform truth, stated plainly: on **X11** a global grab works
through libkeybinder (the ``Keybinder`` GObject typelib) while
the app runs.  Under **Wayland**, global hotkeys are restricted
by the compositor — a client cannot grab keys like this, and
the binding will simply not register there.  Nothing in this
module pretends otherwise: when the backend is unavailable,
``available`` is False and no registration is faked.

This module is deliberately split so the logic is testable
without a display: combo parsing/validation is pure, and the
binding talks to a backend object (bind/unbind); the real
backend wraps Keybinder and is imported lazily, only when used.
"""
from __future__ import annotations

DEFAULT_COMBO = "Ctrl+Alt+D"
DEFAULT_HOTKEY = {"enabled": True, "combo": DEFAULT_COMBO}

_MODIFIERS = {
    "ctrl": "Control", "control": "Control",
    "alt": "Alt", "mod1": "Alt",
    "shift": "Shift",
    "meta": "Meta", "super": "Meta", "win": "Meta", "mod4": "Meta",
}

# Friendly key name -> GDK key name (what the accelerator wants).
_KEY_NAMES = {
    "space": "space", "tab": "Tab", "enter": "Return",
    "return": "Return", "esc": "Escape", "escape": "Escape",
    "delete": "Delete", "del": "Delete", "backspace": "BackSpace",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Page_Up",
    "pagedown": "Page_Down", "insert": "Insert",
}
_KEY_NAMES.update({f"f{i}": f"F{i}" for i in range(1, 25)})


def parse_combo(text):
    """Validate a combo like "Ctrl+Alt+D" and return its GTK
    accelerator string ("<Control><Alt>d").

    Raises ValueError with a plain-words reason: a global hotkey
    needs at least one modifier (a bare key would hijack normal
    typing everywhere), exactly one key, and known names only.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("The shortcut is empty.")
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("The shortcut needs a modifier and a key "
                         "(e.g. Ctrl+Alt+D).")
    mods, key = parts[:-1], parts[-1]
    accel_mods = []
    for mod in mods:
        canonical = _MODIFIERS.get(mod.lower())
        if canonical is None:
            raise ValueError(f"Unknown modifier: {mod}. Use Ctrl, "
                             "Alt, Shift or Super.")
        tag = f"<{canonical}>"
        if tag not in accel_mods:
            accel_mods.append(tag)
    low = key.lower()
    if low in _MODIFIERS:
        raise ValueError("The shortcut must end with a key, not "
                         "another modifier.")
    if len(key) == 1 and key.isalnum():
        key_name = low
    elif low in _KEY_NAMES:
        key_name = _KEY_NAMES[low]
    else:
        raise ValueError(f"Unknown key: {key}. Use a letter, a "
                         "digit, or a named key like F5 or Space.")
    return "".join(accel_mods) + key_name


def combo_is_valid(text):
    try:
        parse_combo(text)
        return True
    except ValueError:
        return False


class KeybinderBackend:
    """The real backend: libkeybinder on X11.  Imported lazily so
    this module (and its tests) never needs a display or the
    typelib.  ``available`` is False wherever Keybinder can't
    init — Wayland included."""

    def __init__(self):
        self._kb = None
        try:
            import gi
            gi.require_version("Keybinder", "3.0")
            from gi.repository import Keybinder
            Keybinder.init()
            self._kb = Keybinder
        except Exception:
            self._kb = None

    @property
    def available(self):
        return self._kb is not None

    def bind(self, accel, callback):
        if self._kb is None:
            return False
        try:
            return bool(self._kb.bind(accel, callback, None))
        except Exception:
            return False

    def unbind(self, accel):
        if self._kb is None:
            return
        try:
            self._kb.unbind(accel)
        except Exception:
            pass


class HotkeyBinding:
    """Owns one global binding: bind a combo to a callback,
    re-bind on change, release on demand.  With the toggle off
    (or an unavailable backend) nothing is registered — the
    release path is the same code the Settings toggle drives."""

    def __init__(self, backend=None):
        self._backend = backend if backend is not None \
            else KeybinderBackend()
        self._accel = None
        self._callback = None

    @property
    def available(self):
        return bool(getattr(self._backend, "available", False))

    @property
    def active_accel(self):
        return self._accel

    def bind(self, combo_text, callback):
        """(Re)bind.  Returns True when a grab is registered.
        Raises ValueError for a malformed combo — validation is
        the caller's first line, this is the second."""
        accel = parse_combo(combo_text)
        if accel == self._accel and self._callback is callback:
            return True
        self.release()
        if not self.available:
            return False
        if not self._backend.bind(accel, callback):
            return False
        self._accel = accel
        self._callback = callback
        return True

    def release(self):
        if self._accel is not None:
            self._backend.unbind(self._accel)
            self._accel = None
            self._callback = None

    def sync(self, config, callback):
        """Make the binding match a hotkey config section —
        what the window's watcher calls as Settings change.
        Returns the registered accelerator, or None."""
        cfg = config or {}
        if not cfg.get("enabled", True):
            self.release()
            return None
        try:
            accel = parse_combo(cfg.get("combo") or DEFAULT_COMBO)
        except ValueError:
            self.release()
            return None
        if accel == self._accel:
            return self._accel
        return accel if self.bind(cfg.get("combo")
                                  or DEFAULT_COMBO, callback) \
            else None
