"""Floating control overlay: the pure state mapping.

The GTK window in deskdora.window renders what this module
decides.  Keeping the mapping free of GTK imports makes the
behaviour testable: given a /api/status payload, what should the
overlay show, and which controls should it offer?

Control semantics are the service's own (the overlay's buttons call
the same /api/kill, /api/pause, /api/resume and /api/reset endpoints
as the companion):

* Freeze = pause at a step boundary (never mid-action).
* Resume continues a frozen run, or — after a kill — the queue.
  A kill-stopped run itself is never resumed; it offers Reset.
* Reset clears a finished/stopped run back to idle, which hides
  the overlay.
"""
from __future__ import annotations

HIDDEN = {"visible": False, "mode": "hidden", "goal": "",
          "percent": None, "steps_text": "", "status_text": "",
          "planner_display": "",
          "can_stop": False, "can_freeze": False,
          "can_resume": False, "can_reset": False}


def overlay_state(status):
    """Map a /api/status payload to the overlay's view state."""
    status = status or {}
    state = status.get("state") or {}
    progress = status.get("progress") or {}
    result = state.get("result")
    goal = progress.get("goal") or state.get("goal") or ""
    disp = progress.get("planner_display") or ""

    if state.get("running"):
        paused = bool(progress.get("paused"))
        total = progress.get("step_total") or 0
        done = progress.get("steps_done") or 0
        return {
            "visible": True,
            "mode": "paused" if paused else "running",
            "goal": goal,
            "percent": progress.get("percent") or 0,
            "steps_text": (f"STEP {done}/{total}" if total
                           else "PLANNING"),
            "status_text": ("PAUSED — AT A STEP BOUNDARY" if paused
                            else (progress.get("current_action")
                                  or "WORKING")),
            "planner_display": disp,
            "can_stop": True,
            "can_freeze": not paused,
            "can_resume": paused,
            "can_reset": False,
        }

    if result:
        queue_waiting = bool(progress.get("queue_total")) and bool(
            progress.get("queue_halted")
            or progress.get("queue_paused"))
        return {
            "visible": True,
            "mode": "finished",
            "goal": goal,
            "percent": progress.get("percent") or 0,
            "steps_text": "",
            "status_text": (result.get("status") or "done").upper(),
            "planner_display": disp,
            "can_stop": False,
            "can_freeze": False,
            # Only meaningful when a halted/paused queue is waiting:
            # resume then continues the queue, never this run.
            "can_resume": queue_waiting,
            "can_reset": True,
        }

    if progress.get("queue_total"):
        halted = bool(progress.get("queue_halted"))
        qpaused = bool(progress.get("queue_paused"))
        return {
            "visible": True,
            "mode": "queued",
            "goal": "Task queue",
            "percent": None,
            "steps_text": f"{progress.get('queue_total', 0)} WAITING",
            "status_text": ("HALTED — KILL SWITCH" if halted
                            else "PAUSED" if qpaused else "QUEUED"),
            "planner_display": disp,
            "can_stop": True,
            "can_freeze": not (halted or qpaused),
            "can_resume": halted or qpaused,
            "can_reset": False,
        }

    return dict(HIDDEN)


# ---------------------------------------------------------------------
# Edge light: a click-through neon border around the screen edges.
# Same state signal as the floating window:
#   running               -> lime (flowing / gently pulsing)
#   awaiting confirmation -> amber, pulsing faster
#   paused / frozen       -> amber, steady
#   finished / idle       -> off
# The user's style setting (off|single|double|flow) gates everything:
# "off" disables the overlay completely.
# ---------------------------------------------------------------------
EDGE_STYLES = ("off", "single", "double", "flow")


def edge_state(status, setting="single"):
    """Map a /api/status payload + the edge-light setting to the
    edge overlay's view state."""
    hidden = {"visible": False, "mode": "off", "style": setting,
              "animated": False}
    if setting not in EDGE_STYLES or setting == "off":
        return hidden
    status = status or {}
    state = status.get("state") or {}
    progress = status.get("progress") or {}

    if status.get("pending_confirmation"):
        return {"visible": True, "mode": "amber-fast",
                "style": setting, "animated": True}
    if state.get("running"):
        if progress.get("paused"):
            return {"visible": True, "mode": "amber-steady",
                    "style": setting, "animated": False}
        return {"visible": True, "mode": "lime",
                "style": setting, "animated": True}
    if progress.get("queue_paused") and progress.get("queue_total"):
        return {"visible": True, "mode": "amber-steady",
                "style": setting, "animated": False}
    return hidden
