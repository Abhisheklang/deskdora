"""Linux / X11 driver: AT-SPI perception + xdotool input.

This is DeskDora's reference driver — the one the gate, the benchmark,
and every shipped screenshot were produced with.  It delegates to the
long-standing ``perception`` and ``actions`` modules, which remain the
X11 backend implementation.
"""
from __future__ import annotations

import subprocess

from .. import actions, perception
from .base import ALL_METHODS, DesktopDriver, DriverError


class LinuxX11Driver(DesktopDriver):
    id = "linux-x11"
    display_name = "Linux (X11 / AT-SPI + xdotool)"
    implemented = ALL_METHODS
    verified_on_hardware = True
    hardware_note = ("Verified end-to-end in the reference rig "
                     "(XFCE/Xvfb) and on Ubuntu Xorg sessions.")

    def observe(self):
        return perception.observe()

    def refind(self, element):
        return perception.refind(element)

    def click(self, x, y):
        actions.click(x, y)

    def double_click(self, x, y):
        actions.double_click(x, y)

    def type_text(self, text):
        actions.type_text(text)

    def key(self, combo):
        actions.key(combo)

    def scroll(self, x, y, direction="down"):
        actions.scroll(x, y, direction)

    def launch(self, command):
        actions.launch(command, wait=0)
        return command

    def screenshot(self, path):
        return actions.screenshot(path)

    def read_clipboard(self):
        res = subprocess.run(["xclip", "-o", "-selection", "clipboard"],
                             capture_output=True, text=True, timeout=15)
        if res.returncode != 0:
            raise DriverError(f"xclip failed: {res.stderr.strip()}")
        return res.stdout

    def active_window_title(self):
        return actions.active_window_title()

    def activate_window(self, title):
        return actions.activate_window(title)
