"""Safety layer: kill switch, confirmation gate, action classification,
and the application allow-list.

Design rule: the agent may look at anything, but actions that delete,
send, submit, or install must pass the confirmation gate first.  Who
approves is a *policy* (a human at the UI, a human at the terminal, or an
explicitly-configured auto-approver for unattended benchmark runs) — the
gate itself is always exercised and always logged.
"""
from __future__ import annotations

import os
import threading

# Element names / key actions that mark a step as destructive.
DESTRUCTIVE_NAME_HINTS = (
    "delete", "remove", "trash", "empty trash", "erase", "discard",
    "send", "submit", "install", "purchase", "buy now", "pay now",
    "confirm order", "uninstall", "format", "reset",
)
CONFIRM_ROLES = {"push button", "menu item", "check menu item"}


def classify_step(step):
    """Return 'destructive' or 'safe' for a plan step."""
    action = step.get("action")
    if action == "delete_files":
        return "destructive"
    if action == "key" and step.get("key", "").lower() in ("delete", "shift+delete"):
        # A bare Delete keypress in a file manager deletes the selection.
        if step.get("context") == "files":
            return "destructive"
        return "safe"
    if action == "click":
        target = step.get("target") or {}
        name = (target.get("name_contains") or target.get("exact_name") or "").lower()
        if any(hint in name for hint in DESTRUCTIVE_NAME_HINTS):
            return "destructive"
    return "safe"


class KillSwitch:
    """Checked before every step.  Trips when the stop file appears or
    when trip() is called (the web UI's kill button does both)."""

    def __init__(self, stop_file=None):
        self.stop_file = stop_file or os.environ.get(
            "DESKDORA_STOP_FILE",
            os.path.expanduser("~/.local/share/deskdora/STOP"))
        self._tripped = threading.Event()

    def trip(self):
        self._tripped.set()
        try:
            os.makedirs(os.path.dirname(self.stop_file), exist_ok=True)
            with open(self.stop_file, "w") as fh:
                fh.write("stop\n")
        except OSError:
            pass

    def reset(self):
        self._tripped.clear()
        try:
            os.unlink(self.stop_file)
        except OSError:
            pass

    def is_tripped(self):
        return self._tripped.is_set() or os.path.exists(self.stop_file)


class ConfirmationGate:
    """Wraps a policy callable: policy(step, description) -> bool.

    Every request and decision is recorded in self.history so the audit
    log (and the tests) can prove the gate was exercised.
    """

    def __init__(self, policy):
        self.policy = policy
        self.history = []

    def check(self, step, description=""):
        verdict = classify_step(step)
        if verdict != "destructive":
            return True
        approved = bool(self.policy(step, description))
        self.history.append({
            "step": step, "description": description, "approved": approved,
        })
        return approved


def deny_all(step, description=""):
    return False


def approve_all(step, description=""):
    """Explicit auto-approve policy (benchmark runs).  Use is always
    disclosed in the run log and benchmark report."""
    return True


def terminal_policy(input_fn=input, out=print):
    def policy(step, description=""):
        out(f"\n[DeskDora] Confirmation needed: {description or step}")
        try:
            answer = input_fn("Allow this action? [y/N] ")
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")
    return policy


class AllowList:
    """Applications the agent is permitted to launch.  Empty = allow all
    (the default for a personal, local tool); a non-empty list is enforced
    on every launch step."""

    def __init__(self, allowed=None):
        self.allowed = [a.lower() for a in (allowed or [])]

    def permits(self, command):
        if not self.allowed:
            return True
        base = command.strip().split()[0].split("/")[-1].lower()
        return base in self.allowed
