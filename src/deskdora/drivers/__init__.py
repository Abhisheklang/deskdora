"""Driver registry — Linux sessions only.

DeskDora is a Linux desktop agent; these are the only drivers that
exist:

* Linux with a Wayland session (``WAYLAND_DISPLAY`` set and
  ``XDG_SESSION_TYPE=wayland``) → ``linux-wayland``
* Linux otherwise (Xorg/X11) → ``linux-x11``

``DESKDORA_DRIVER`` (or the server's ``driver`` config) overrides the
choice by id.  Driver modules are imported lazily so importing the
registry never pulls a session backend in early.
"""
from __future__ import annotations

import os

from .base import (ACTION_REQUIREMENTS, CapabilityError, DesktopDriver,
                   DriverError)

__all__ = [
    "ACTION_REQUIREMENTS", "CapabilityError", "DesktopDriver",
    "DriverError", "available_drivers", "detect_driver_id", "get_driver",
]

_LOADERS = {
    "linux-x11": ("linux_x11", "LinuxX11Driver"),
    "linux-wayland": ("linux_wayland", "LinuxWaylandDriver"),
}


def available_drivers():
    """id -> driver class, for every driver shipped in this build."""
    import importlib
    out = {}
    for driver_id, (module, cls_name) in _LOADERS.items():
        mod = importlib.import_module(f".{module}", __name__)
        out[driver_id] = getattr(mod, cls_name)
    return out


def detect_driver_id():
    override = os.environ.get("DESKDORA_DRIVER", "").strip()
    if override:
        return override
    if (os.environ.get("XDG_SESSION_TYPE") == "wayland"
            or (os.environ.get("WAYLAND_DISPLAY")
                and not os.environ.get("DISPLAY"))):
        return "linux-wayland"
    return "linux-x11"


def get_driver(driver_id=None):
    """Instantiate a driver by id (default: auto-detect)."""
    import importlib
    driver_id = driver_id or detect_driver_id()
    if driver_id not in _LOADERS:
        raise DriverError(
            f"unknown driver {driver_id!r}; available: "
            f"{sorted(_LOADERS)}")
    module, cls_name = _LOADERS[driver_id]
    mod = importlib.import_module(f".{module}", __name__)
    return getattr(mod, cls_name)()
