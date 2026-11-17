"""Resident memory: what DeskDora remembers about this machine.

DeskDora keeps a small, local, human-inspectable memory:

* **places** — named folders ("invoices" -> "/home/you/Documents/
  invoices").  Set explicitly by the user, or learned when a folder
  is organised successfully (the folder's basename becomes a place).
* **apps** — which applications get launched, how often, when last.
* **habits** — goals that have succeeded before (normalised text and
  a count) and routine run counts.

The planner *consults* this memory: "Organise folder my messy by
type" resolves "messy" through places when it is not a literal path.
That is the whole point of keeping it — it is not a log, it changes
what the agent can understand.

Everything lives in one JSON file in the data directory.  It is
never sent anywhere, every entry can be inspected and deleted from
the Memory view, and clearing it is one click.  A corrupt file is
not fatal: memory starts empty and the bad file is kept aside as
``memory.json.corrupt`` for the user to look at.
"""
from __future__ import annotations

import json
import os
import re
import time


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class MemoryStore:
    def __init__(self, path):
        self.path = path
        self.data = {"places": {}, "apps": {}, "habits": {"goals": {},
                                                          "routines": {}}}
        self.load()

    # -- persistence ------------------------------------------------------
    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as fh:
                raw = json.load(fh)
            for key in ("places", "apps"):
                if isinstance(raw.get(key), dict):
                    self.data[key] = raw[key]
            habits = raw.get("habits") or {}
            for key in ("goals", "routines"):
                if isinstance(habits.get(key), dict):
                    self.data["habits"][key] = habits[key]
        except (OSError, ValueError):
            try:
                os.replace(self.path, self.path + ".corrupt")
            except OSError:
                pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # -- places -------------------------------------------------------------
    def set_place(self, alias, path, source="user"):
        alias = _norm(alias)
        if not alias or not path:
            raise ValueError("alias and path are required")
        self.data["places"][alias] = {
            "path": path, "source": source, "updated": time.time(),
        }
        self.save()
        return self.data["places"][alias]

    def get_place(self, alias):
        entry = self.data["places"].get(_norm(alias))
        return entry["path"] if entry else None

    def resolve_place(self, token):
        """Resolve a goal token to a path: an exact alias first, then
        a place whose stored path has this basename."""
        token_n = _norm(token)
        hit = self.get_place(token_n)
        if hit:
            return hit
        for entry in self.data["places"].values():
            if _norm(os.path.basename(entry["path"])) == token_n:
                return entry["path"]
        return None

    def delete_place(self, alias):
        if self.data["places"].pop(_norm(alias), None) is not None:
            self.save()
            return True
        return False

    # -- learning -------------------------------------------------------------
    def note_app(self, command, when=None):
        entry = self.data["apps"].setdefault(
            command, {"count": 0, "last": 0})
        entry["count"] += 1
        entry["last"] = when or time.time()

    def note_goal(self, goal):
        key = _norm(goal)
        if key:
            goals = self.data["habits"]["goals"]
            goals[key] = goals.get(key, 0) + 1

    def note_routine(self, name):
        entry = self.data["habits"]["routines"].setdefault(
            name, {"count": 0, "last": 0})
        entry["count"] += 1
        entry["last"] = time.time()

    def learn_from_run(self, goal, steps, status, organised_paths=()):
        """Fold a finished run into memory.  Only successful runs
        teach: a failed attempt is not a habit worth keeping."""
        if status != "success":
            return
        self.note_goal(goal)
        for step in steps or []:
            if step.get("action") == "launch" and step.get("command"):
                self.note_app(step["command"])
        for path in organised_paths:
            base = os.path.basename(os.path.normpath(path))
            if base:
                self.set_place(base, path, source="learned")
        self.save()

    # -- inspection / forgetting ---------------------------------------------
    def snapshot(self):
        return {
            "places": dict(sorted(self.data["places"].items())),
            "apps": dict(sorted(self.data["apps"].items())),
            "habits": {
                "goals": dict(sorted(self.data["habits"]["goals"].items())),
                "routines": dict(sorted(
                    self.data["habits"]["routines"].items())),
            },
        }

    def forget(self, kind, key):
        if kind == "places":
            return self.delete_place(key)
        table = {"apps": self.data["apps"],
                 "goals": self.data["habits"]["goals"],
                 "routines": self.data["habits"]["routines"]}.get(kind)
        if table is not None and table.pop(key, None) is not None:
            self.save()
            return True
        return False

    def clear(self):
        self.data = {"places": {}, "apps": {},
                     "habits": {"goals": {}, "routines": {}}}
        self.save()


def learn_from_log(memory, log_dir, status):
    """Learn from a finished run's audit log (used by the CLI and
    the server alike).  Extracts the executed plan and any folder the
    run organised, then defers to MemoryStore.learn_from_run.
    Accepts the run directory or the log.jsonl path itself."""
    from .audit import read_log
    path = log_dir
    if os.path.isdir(path):
        path = os.path.join(path, "log.jsonl")
    events = read_log(path)
    steps = []
    organised = []
    goal = ""
    for ev in events:
        if ev.get("kind") == "run_start":
            goal = ev.get("goal", goal)
        elif ev.get("kind") == "plan":
            steps = ev.get("steps", steps)
    if status == "success":
        # The executed plan is the ground truth for what was
        # organised (the goal text may have used a memory alias).
        for step in steps:
            cmd = step.get("command", "")
            if step.get("action") == "launch" and cmd.startswith("thunar "):
                organised.append(cmd[len("thunar "):].strip())
    memory.learn_from_run(goal, steps, status, organised_paths=organised)
