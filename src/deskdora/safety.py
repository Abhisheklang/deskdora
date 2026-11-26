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
import time

# Element names / key actions that mark a step as destructive.
# This list is the single source of truth for name-based danger:
# autonomy.categorise_step builds on classify_step and adds the
# category mapping, it does not maintain a second hint list for
# click names.  The money hints here deliberately cover the full
# autonomy MONEY_HINTS click surface ("buy", "pay", "checkout",
# "donate", "subscribe now", "transfer money", ...), not only the
# narrower "... now" forms: a click on "Buy" or "Pay" is a money
# action and must confirm (vibe S1/V3).
DESTRUCTIVE_NAME_HINTS = (
    "delete", "remove", "trash", "empty trash", "erase", "discard",
    "send", "submit", "install", "purchase", "buy now", "pay now",
    "buy", "pay", "checkout", "check out", "place order", "payment",
    "donate", "subscribe now", "send money", "transfer money",
    "confirm order", "uninstall", "format", "reset",
)
CONFIRM_ROLES = {"push button", "menu item", "check menu item"}


def classify_step(step):
    """Return 'destructive' or 'safe' for a plan step.

    'destructive' means the step must reach a human confirmation
    before it runs.  Launch steps are destructive by this measure:
    launching a program runs a command on the user's machine, so it
    is always significant (BugHunter C3, vibe S1)."""
    if not isinstance(step, dict):
        # Fail closed: an unclassifiable step shape confirms
        # rather than crashing the classifier (DD-A4).
        return "destructive"
    action = step.get("action")
    if action == "launch":
        return "destructive"
    key_value = step.get("key")
    if action == "key" and isinstance(key_value, str) \
            and key_value.lower() in ("delete", "shift+delete"):
        # A bare Delete keypress in a file manager deletes the selection.
        if step.get("context") == "files":
            return "destructive"
        return "safe"
    if action == "click_at":
        # A coordinate click carries no element name at all, so
        # there is nothing to prove it is not a Delete/Send/Pay
        # button.  Treat it as destructive (confirmation required).
        return "destructive"
    if action in ("click", "double_click"):
        target = step.get("target") or {}
        if not isinstance(target, dict):
            target = {}
        # Only string name fields can prove anything about the
        # target; a typed value (123, a list) proves nothing, so
        # the click stays unproven and therefore destructive
        # (DD-A4 - fail closed, exactly as for an unnamed click).
        name = ""
        for field in ("name_contains", "exact_name",
                      "label_contains"):
            value = target.get(field)
            if isinstance(value, str) and value:
                name = value
                break
        name = name.lower()
        if any(hint in name for hint in DESTRUCTIVE_NAME_HINTS):
            return "destructive"
        if not name:
            # No resolvable name: an element-index click (the main
            # shape planners emit) or a role-only target.  The gate
            # check runs before the element is resolved, so an
            # unnamed click cannot be proven safe - it must confirm
            # like a destructive one.
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


class PauseGate:
    """Cooperative pause, checked by the agent between steps.

    Pausing never freezes a half-executed action: the current step
    finishes, then the run waits at the boundary.  A kill that lands
    while paused still aborts the run — wait() returns False and the
    agent treats it exactly like a kill at any other boundary."""

    def __init__(self):
        self._paused = threading.Event()

    def request(self):
        self._paused.set()

    def clear(self):
        self._paused.clear()

    @property
    def paused(self):
        return self._paused.is_set()

    def wait(self, kill_switch, poll=0.15):
        """Block while paused.  False = the kill switch tripped
        during the wait (the run must stop, not resume)."""
        while self._paused.is_set():
            if kill_switch.is_tripped():
                return False
            time.sleep(poll)
        return not kill_switch.is_tripped()


class ConfirmationGate:
    """Wraps a policy callable: policy(step, description) -> bool.

    Every request and decision is recorded in self.history so the audit
    log (and the tests) can prove the gate was exercised.
    """

    def __init__(self, policy):
        self.policy = policy
        self.history = []

    def check(self, step, description="", requires_confirmation=None):
        """Ask the human policy if this step needs confirmation.

        `requires_confirmation` is the autonomy layer's verdict when
        the caller has one: True means autonomy.evaluate() already
        ordered this step confirmed, and the gate MUST honour that
        and consult the policy - it never re-classifies the step and
        overrules the stricter first decision (vibe S1).  When the
        caller has no autonomy verdict (None), the gate falls back
        to the single shared classifier, classify_step."""
        if requires_confirmation is None:
            requires_confirmation = classify_step(step) == "destructive"
        if not requires_confirmation:
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
