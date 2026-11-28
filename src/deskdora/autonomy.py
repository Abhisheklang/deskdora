"""Per-category autonomy policy.

Every consequential action DeskDora takes belongs to a category:

  files     deleting / moving / renaming files and folders
  apps      installing or removing software, launching apps
  web       acting inside a browser (navigating, clicking, typing)
  messages  sending things to people or services (send, submit,
            post, publish, reply)
  money     anything money-adjacent (pay, buy, checkout, orders)

Each category is set to one of:

  free     the agent may do it without asking
  confirm  the agent must ask first (the v1 behaviour for
           destructive actions)
  never    the agent must refuse, however nicely it is asked

**Hard floor:** the money category can never be set to `free`.
Money-adjacent actions always require a human confirmation.  The
floor is enforced in code (here), in the config API (a `free` money
setting is rejected), and in the Settings UI (the option is not
offered).  Defence in depth, because this is the category where an
autonomous mistake costs real money.

Non-consequential steps (waits, reads, navigation inside an app)
are not gated by the policy except that `never` still blocks them
when they belong to that category — `web: never` means the agent
does not browse at all, not merely that it asks before paying.
"""
from __future__ import annotations

from .safety import classify_step

CATEGORIES = ("files", "apps", "web", "messages", "money")
LEVELS = ("free", "confirm", "never")
DEFAULT_POLICY = {category: "confirm" for category in CATEGORIES}

MONEY_HINTS = ("pay", "purchase", "buy", "checkout", "check out",
               "confirm order", "place order", "donate", "payment",
               "subscribe now", "send money", "transfer money")
MESSAGE_HINTS = ("send", "submit", "post", "publish", "reply",
                 "tweet", "share via")
APP_HINTS = ("install", "uninstall")
BROWSER_APPS = ("chrom", "firefox", "browser", "brave", "edge", "opera")
FILE_APPS = ("thunar", "nautilus", "files", "dolphin", "nemo", "pcmanfm")


class PolicyError(ValueError):
    pass


def validate_policy(raw):
    """Validate a raw policy dict; returns a complete policy.
    Raises PolicyError on unknown categories/levels and on any
    attempt to set money below confirm (the hard floor)."""
    policy = dict(DEFAULT_POLICY)
    for category, level in (raw or {}).items():
        if category not in CATEGORIES:
            raise PolicyError(f"unknown autonomy category: {category!r}")
        if level not in LEVELS:
            raise PolicyError(
                f"unknown autonomy level {level!r} for {category!r}")
        policy[category] = level
    if policy["money"] == "free":
        raise PolicyError(
            "the money category can never be set to free-run: "
            "money-adjacent actions always need a human confirmation")
    return policy


class AutonomyPolicy:
    def __init__(self, raw=None):
        self.levels = validate_policy(raw or {})

    def level_for(self, category):
        return self.levels.get(category, "confirm")

    def as_dict(self):
        return dict(self.levels)


def _step_text(step):
    parts = []
    target = step.get("target") or {}
    if not isinstance(target, dict):
        target = {}
    for key in ("name_contains", "exact_name", "label_contains",
                "app_contains", "role"):
        if target.get(key):
            parts.append(str(target[key]))
    for key in ("text", "command", "key"):
        if step.get(key):
            parts.append(str(step[key]))
    return " ".join(parts).lower()


def categorise_step(step, context="", element_app=""):
    """Return (category, significant).

    `significant` marks steps the confirmation layer cares about
    (the destructive set from safety.classify_step, plus money
    actions, which are significant even when a name pattern alone
    would not flag them).  `element_app` is the app of the resolved
    target element when the agent knows it.
    """
    text = _step_text(step)
    action = step.get("action", "")
    app = (element_app or "").lower()

    if any(hint in text for hint in MONEY_HINTS):
        return "money", True
    if action == "launch":
        # Launching a program is always significant: it runs a
        # command on the user's machine, so the default all-confirm
        # policy must ask first (BugHunter C3).  Handled before the
        # destructive check so the category still reflects what is
        # being launched; significance comes from the one shared
        # classifier in safety.classify_step (vibe S1/V3).
        raw_command = step.get("command")
        command = raw_command.lower() \
            if isinstance(raw_command, str) else ""
        if any(b in command for b in BROWSER_APPS):
            return "web", True
        if any(f in command for f in FILE_APPS):
            return "files", True
        return "apps", True
    step_context = context or step.get("context") or ""
    if step_context == "terminal" \
            and action in ("type", "key", "build_sheet"):
        # Input into a terminal is command execution (the C3
        # sub-gap): significant in the apps category, so the
        # default policy confirms it and a "never" apps setting
        # refuses it.  Categorised here, not left to the generic
        # destructive fallback below, so it can never be routed
        # into a laxer category by accident.
        return "apps", True
    probe = step
    if step_context and "context" not in step:
        probe = {**step, "context": step_context}
    destructive = classify_step(probe) == "destructive"
    if destructive:
        if any(hint in text for hint in MESSAGE_HINTS):
            return "messages", True
        if any(hint in text for hint in APP_HINTS):
            return "apps", True
        return "files", True
    if any(b in app for b in BROWSER_APPS):
        return "web", False
    if any(f in app for f in FILE_APPS):
        return "files", False
    return "apps", False


def evaluate(policy, step, context="", element_app=""):
    """The enforcement decision for one step, as
    (decision, category, significant) with decision in
    "allow" | "confirm" | "deny".

    * never   -> deny, significant or not (the category is closed).
    * confirm -> confirm when significant, allow otherwise (this is
      exactly the pre-v3.0 behaviour when every category is confirm,
      and when `policy` is None).
    * free    -> allow (the caller logs the auto-approval).
    """
    category, significant = categorise_step(
        step, context=context, element_app=element_app)
    level = policy.level_for(category) if policy else "confirm"
    if level == "never":
        return "deny", category, significant
    if level == "confirm" and significant:
        return "confirm", category, significant
    return "allow", category, significant


def decide(policy, step, context="", element_app=""):
    decision, category, _significant = evaluate(
        policy, step, context=context, element_app=element_app)
    return decision, category
