#!/usr/bin/env python3
"""Probe DeskDora's Wayland support in a live Wayland session.

Run INSIDE a Wayland session (e.g. headless sway) with the session's
environment (WAYLAND_DISPLAY, DBUS_SESSION_BUS_ADDRESS,
XDG_RUNTIME_DIR).  Prints the linux-wayland driver's probe() result
as JSON — verbatim, including failures.  The probe:

* checks the portal bus name is owned,
* counts AT-SPI elements (perception under Wayland),
* attempts a real screenshot through the Screenshot portal,
* attempts a RemoteDesktop session (CreateSession/SelectDevices/
  Start) and reports the granted device bitmask.

Exit code is 0 if the probe *ran*; the JSON is the verdict.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deskdora.drivers.linux_wayland import LinuxWaylandDriver  # noqa: E402


def main():
    driver = LinuxWaylandDriver()
    result = driver.probe()
    result["driver"] = driver.id
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
