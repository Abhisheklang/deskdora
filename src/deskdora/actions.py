"""Actions: perform input on the X display with xdotool, capture
screenshots with scrot, and launch applications.

Everything here is deliberately thin and synchronous so the agent loop can
re-observe after every single action and verify what actually happened.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time


def _xdotool(*args, check=True):
    res = subprocess.run(["xdotool", *args], capture_output=True, text=True,
                         timeout=30)
    if check and res.returncode != 0:
        raise RuntimeError(f"xdotool {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def click(x, y, button=1):
    _xdotool("mousemove", str(int(x)), str(int(y)), "click", str(button))


def double_click(x, y):
    _xdotool("mousemove", str(int(x)), str(int(y)), "click", "--repeat", "2", "1")


def move(x, y):
    _xdotool("mousemove", str(int(x)), str(int(y)))


def type_text(text, delay_ms=12):
    # --clearmodifiers avoids stuck Ctrl/Alt from earlier key actions.
    _xdotool("type", "--clearmodifiers", "--delay", str(delay_ms), "--", text)


def key(combo):
    """combo is xdotool syntax: 'Return', 'ctrl+a', 'alt+F4', 'Tab' ..."""
    _xdotool("key", "--clearmodifiers", combo)


def scroll(x, y, direction="down", clicks=3):
    button = "5" if direction == "down" else "4"
    _xdotool("mousemove", str(int(x)), str(int(y)),
             "click", "--repeat", str(clicks), button)


def launch(command, wait=1.5):
    """Launch a GUI application detached from our process group.

    The command is parsed with shlex and executed as an argv list
    with no shell, so shell metacharacters in a planner-supplied
    command are inert arguments, never shell syntax (BugHunter C3).
    """
    argv = shlex.split(command)
    if not argv:
        raise ValueError("launch needs a non-empty command")
    env = dict(os.environ)
    subprocess.Popen(argv, shell=False, env=env,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    if wait:
        time.sleep(wait)
    return command


def active_window_title():
    try:
        return _xdotool("getactivewindow", "getwindowname", check=False)
    except Exception:
        return ""


def activate_window(title):
    """Best-effort raise of the window with this title, so a click lands
    on the app it was aimed at even if another window (e.g. DeskDora's
    own confirmation UI) was raised in between.  Never raises."""
    if not title:
        return False
    try:
        res = subprocess.run(
            ["xdotool", "search", "--name", f"^{title}$", "windowactivate"],
            capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            time.sleep(0.35)
            return True
    except Exception:
        pass
    return False


def screenshot(path):
    """Capture the full display to a PNG file. Returns the path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    res = subprocess.run(["scrot", "-o", path], capture_output=True, text=True,
                         timeout=30)
    if res.returncode != 0 or not os.path.exists(path):
        raise RuntimeError(f"scrot failed: {res.stderr.strip()}")
    return path


def sleep(seconds):
    time.sleep(seconds)
