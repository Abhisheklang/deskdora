"""Desktop notifications (v3.4): libnotify via notify-send.

One notification per event, fired from exactly one call site per
event (run finish, confirmation creation, queue drain, connection
failure) — no duplicates by construction.  Everything is
fire-and-forget: a missing notify-send or notification server
fails silently and never delays the agent.

Settings: the master switch is the Features-panel toggle
(config["feature_notifications"]); per-event toggles live under
config["notifications"]["events"] in Settings → Notifications.
The confirmation-waiting notification additionally offers a
"Show DeskDora" action where the notification server supports
actions: picking it tries to raise the app window with wmctrl.
That part is best-effort by nature — it depends on the desktop's
notification server — and is reported as such.
"""
from __future__ import annotations

import shutil
import subprocess
import threading

DEFAULT_NOTIFICATIONS = {
    "events": {
        "run_completed": True,
        "run_failed": True,
        "confirmation_waiting": True,
        "queue_finished": True,
        "connection_failed": True,
    },
}

EVENT_TITLES = {
    "run_completed": "DeskDora — run completed",
    "run_failed": "DeskDora — run failed",
    "confirmation_waiting": "DeskDora — confirmation waiting",
    "queue_finished": "DeskDora — queue finished",
    "connection_failed": "DeskDora — connection problem",
}


def notifications_config(config):
    merged = {"enabled": bool((config or {}).get(
                  "feature_notifications", True)),
              "events": dict(DEFAULT_NOTIFICATIONS["events"])}
    raw = (config or {}).get("notifications") or {}
    if isinstance(raw.get("events"), dict):
        merged["events"].update(raw["events"])
    return merged


def event_enabled(config, event):
    cfg = notifications_config(config)
    return bool(cfg["enabled"] and cfg["events"].get(event, False))


def _notify_send(title, body, urgency="normal", focus_action=False,
                 runner=None):
    """One notify-send invocation.  ``runner`` is injectable for
    tests: runner(argv) -> None."""
    binary = shutil.which("notify-send")
    if binary is None:
        if runner is None:
            return False
        binary = "notify-send"  # injected runner (tests)
    argv = [binary, "-a", "DeskDora", "-u", urgency, title, body]
    if focus_action:
        argv = [binary, "-a", "DeskDora", "-u", urgency,
                "-A", "focus=Show DeskDora", title, body]
    run = runner or (lambda a: subprocess.run(
        a, capture_output=True, timeout=30))
    if focus_action and runner is None:
        def _run_and_maybe_focus():
            try:
                out = subprocess.run(argv, capture_output=True,
                                     text=True, timeout=120)
                if (out.stdout or "").strip() == "focus":
                    wmctrl = shutil.which("wmctrl")
                    if wmctrl:
                        subprocess.run([wmctrl, "-a", "DeskDora"],
                                       capture_output=True, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_run_and_maybe_focus,
                         daemon=True).start()
        return True
    try:
        run(argv)
        return True
    except Exception:
        return False


def notify(event, config, body="", runner=None):
    """Send the notification for ``event`` if settings allow it.
    Returns True when a notification was dispatched.  A disabled
    master switch or event produces NO call."""
    if event not in EVENT_TITLES:
        return False
    if not event_enabled(config, event):
        return False
    urgency = "critical" if event in ("confirmation_waiting",
                                      "run_failed") else "normal"
    return _notify_send(EVENT_TITLES[event], body, urgency=urgency,
                        focus_action=(event == "confirmation_waiting"),
                        runner=runner)
