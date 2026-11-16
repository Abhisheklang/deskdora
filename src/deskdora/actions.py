"""Actions: perform input on the X display with xdotool, capture
screenshots with scrot, and launch applications.

Everything here is deliberately thin and synchronous so the agent loop can
re-observe after every single action and verify what actually happened.
"""
from __future__ import annotations

import os
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
    """Launch a GUI application detached from our process group."""
    env = dict(os.environ)
    subprocess.Popen(command, shell=True, env=env,
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
