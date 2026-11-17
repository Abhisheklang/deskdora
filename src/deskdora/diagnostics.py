"""Diagnostics (v3.4): a truthful, measured picture of the current
machine — session type, compositor, accessibility-tree health per
app, portal availability, provider connectivity, and overlay
capability.  Every value is measured at call time; nothing is
hard-coded, and each probe reports *how* it knows (or that it
couldn't tell)."""
from __future__ import annotations

import os
import shutil
import subprocess

from . import connfail
from .planners import PROVIDER_REGISTRY

_COMPOSITORS = ("mutter", "kwin_x11", "kwin_wayland", "picom",
                "compton", "xfwm4", "sway", "Hyprland", "wayfire",
                "labwc", "marco", "metacity")
_PORTAL_IFACES = "/usr/share/dbus-1/interfaces"


def _processes():
    names = set()
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as fh:
                    names.add(fh.read().strip())
            except Exception:
                pass
    except Exception:
        pass
    return names


def session_info(env=None):
    env = env or os.environ
    wayland = bool(env.get("WAYLAND_DISPLAY"))
    x11 = bool(env.get("DISPLAY"))
    declared = env.get("XDG_SESSION_TYPE", "")
    if wayland:
        kind = "Wayland"
    elif x11:
        kind = "Xorg"
    else:
        kind = "unknown"
    return {"type": kind, "declared": declared,
            "display": env.get("DISPLAY", ""),
            "wayland_display": env.get("WAYLAND_DISPLAY", ""),
            "desktop": env.get("XDG_CURRENT_DESKTOP", "")}


def compositor_info(env=None):
    """A compositor is required for transparent overlays (the edge
    light).  On Wayland the compositor is the server itself; on
    Xorg we look for a running compositing window manager or a
    standalone compositor, and cross-check the X root window's
    _NET_SUPPORTING_WM_CHECK via xprop when available."""
    sess = session_info(env)
    procs = _processes()
    found = sorted(p for p in _COMPOSITORS if p in procs)
    if sess["type"] == "Wayland":
        return {"present": True,
                "name": found[0] if found else "wayland compositor",
                "how": "Wayland compositors composite by design"}
    wm = ""
    if shutil.which("xprop"):
        try:
            out = subprocess.run(
                ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"],
                capture_output=True, text=True, timeout=4).stdout
            if "window id" in out:
                wid = out.rsplit("#", 1)[-1].strip()
                name_out = subprocess.run(
                    ["xprop", "-id", wid, "_NET_WM_NAME"],
                    capture_output=True, text=True, timeout=4).stdout
                if "=" in name_out:
                    wm = name_out.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
    present = bool(found or wm)
    return {"present": present,
            "name": wm or (found[0] if found else ""),
            "how": ("window manager " + (wm or found[0]) +
                    " is compositing-capable and running")
            if present else
            "no compositing window manager or compositor found "
            "in the process list"}


def atspi_info(driver):
    """Per-app accessibility-tree health from one observation."""
    try:
        obs = driver.observe()
    except Exception as exc:
        return {"reachable": False, "error": str(exc), "apps": [],
                "total_elements": 0, "windows": []}
    counts = {}
    for el in obs.elements:
        counts[el.app or "(unknown)"] = \
            counts.get(el.app or "(unknown)", 0) + 1
    apps = []
    for app, count in sorted(counts.items(),
                             key=lambda kv: -kv[1]):
        health = "rich" if count >= 50 else \
            "sparse" if count > 0 else "empty"
        apps.append({"app": app, "elements": count,
                     "health": health})
    return {"reachable": True, "apps": apps,
            "total_elements": len(obs.elements),
            "windows": list(obs.windows)}


def portal_info():
    """xdg-desktop-portal: is the service answering on the session
    bus, and which of the interfaces DeskDora cares about exist?"""
    ifaces = {}
    for name in ("Screenshot", "RemoteDesktop"):
        ifaces[name.lower()] = os.path.exists(os.path.join(
            _PORTAL_IFACES,
            f"org.freedesktop.portal.{name}.xml"))
    responding = False
    how = "portal interface files " + (
        "present" if any(ifaces.values()) else "absent")
    if shutil.which("gdbus"):
        try:
            out = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.freedesktop.portal.Desktop",
                 "--object-path", "/org/freedesktop/portal/desktop",
                 "--method", "org.freedesktop.DBus.Peer.Ping"],
                capture_output=True, text=True, timeout=5)
            responding = out.returncode == 0
            how = ("portal service answered a D-Bus ping"
                   if responding else
                   "portal service did not answer a D-Bus ping")
        except Exception:
            pass
    return {"responding": responding, "interfaces": ifaces,
            "how": how}


def providers_info(config):
    """Connectivity of the planners the user actually has set up:
    the built-in (always fine), Ollama (server + model), and every
    cloud/custom provider with a stored key — each bounded."""
    out = [{"id": "builtin", "display": "Built-in", "ok": True,
            "detail": "no network involved"}]
    ollama_model = (config.get("providers", {}).get("ollama", {})
                    .get("model") or config.get("model")
                    or "qwen2.5:1.5b")
    failure = connfail.preflight("ollama", config, timeout=3.0)
    out.append({
        "id": "ollama", "display": f"Ollama · {ollama_model}",
        "ok": failure is None,
        "detail": "reachable" if failure is None
        else failure["detail"]})
    providers = config.get("providers") or {}
    for pid, preset in PROVIDER_REGISTRY.items():
        if preset["protocol"] == "ollama":
            continue
        settings = providers.get(pid) or {}
        if not settings.get("api_key"):
            continue
        fail = connfail.preflight(pid, config, timeout=5.0)
        out.append({"id": pid,
                    "display": connfail.provider_display(pid, config),
                    "ok": fail is None,
                    "detail": "reachable" if fail is None
                    else fail["detail"]})
    for custom in config.get("custom_providers") or []:
        if not custom.get("api_key"):
            continue
        fail = connfail.preflight(custom["id"], config, timeout=5.0)
        out.append({"id": custom["id"],
                    "display": connfail.provider_display(
                        custom["id"], config),
                    "ok": fail is None,
                    "detail": "reachable" if fail is None
                    else fail["detail"]})
    return out


def collect(driver, config):
    sess = session_info()
    comp = compositor_info()
    return {
        "session": sess,
        "compositor": comp,
        "atspi": atspi_info(driver),
        "portals": portal_info(),
        "providers": providers_info(config),
        "overlay": {
            "edge_light_setting": config.get("edge_light", "single"),
            "capable": comp["present"],
            "note": "transparent overlays need a compositor"
            if not comp["present"] else
            "compositor present — transparent overlays can render",
        },
    }


def summary_text(diag):
    """Plain-text support summary for the copy button."""
    s = diag["session"]
    lines = [
        "DeskDora diagnostics",
        f"Session: {s['type']} (declared {s['declared'] or '—'}), "
        f"desktop {s['desktop'] or '—'}, DISPLAY {s['display'] or '—'}",
        f"Compositor: {diag['compositor']['name'] or 'none found'} "
        f"({'present' if diag['compositor']['present'] else 'absent'}"
        f" — {diag['compositor']['how']})",
    ]
    at = diag["atspi"]
    if at["reachable"]:
        lines.append(f"AT-SPI: reachable, {at['total_elements']} "
                     f"elements across {len(at['apps'])} apps, "
                     f"{len(at['windows'])} windows")
        for app in at["apps"]:
            lines.append(f"  - {app['app']}: {app['elements']} "
                         f"elements ({app['health']})")
    else:
        lines.append(f"AT-SPI: NOT reachable ({at.get('error', '')})")
    p = diag["portals"]
    lines.append(
        f"Portals: {'responding' if p['responding'] else 'not responding'}"
        f"; screenshot iface "
        f"{'yes' if p['interfaces']['screenshot'] else 'no'}, "
        f"remotedesktop iface "
        f"{'yes' if p['interfaces']['remotedesktop'] else 'no'} "
        f"({p['how']})")
    for prov in diag["providers"]:
        lines.append(f"Provider {prov['display']}: "
                     f"{'OK' if prov['ok'] else 'FAIL'} — "
                     f"{prov['detail']}")
    o = diag["overlay"]
    lines.append(f"Edge light: setting {o['edge_light_setting']}, "
                 f"capable {'yes' if o['capable'] else 'no'} "
                 f"({o['note']})")
    return "\n".join(lines)
