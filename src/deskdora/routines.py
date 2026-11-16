"""Saved routines: record a successful run as a named routine and
re-run it in one click.  Routines are stored locally as JSON files —
nothing is shared, uploaded, or synced anywhere.

A routine stores the *goal* (plus the planner mode that succeeded), not
a frozen click script: re-running re-plans against the current screen,
which is what makes routines survive window moves and theme changes.
An optional schedule uses a small five-field cron dialect; a local
checker reports which routines are due.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

from .audit import read_log


def default_data_dir():
    return os.environ.get(
        "DESKDORA_DATA_DIR",
        os.path.expanduser("~/.local/share/deskdora"))


class RoutineStore:
    def __init__(self, root=None):
        self.root = root or os.path.join(default_data_dir(), "routines")
        os.makedirs(self.root, exist_ok=True)

    def _path(self, name):
        safe = re.sub(r"[^a-z0-9\-_]+", "-", name.lower()).strip("-")
        return os.path.join(self.root, f"{safe}.json"), safe

    def save(self, name, goal, planner_mode="", schedule=None,
             source_run=None):
        path, safe = self._path(name)
        data = {
            "name": safe, "goal": goal, "planner_mode": planner_mode,
            "schedule": schedule, "source_run": source_run,
            "created_at": time.time(), "last_run_at": None,
            "run_count": 0,
        }
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
        return data

    def record_from_run(self, name, run_dir, schedule=None):
        events = read_log(os.path.join(run_dir, "log.jsonl"))
        start = next((e for e in events if e["kind"] == "run_start"), None)
        end = next((e for e in reversed(events) if e["kind"] == "run_end"),
                   None)
        if not start or not end or end.get("status") != "success":
            raise ValueError("only a successful run can become a routine")
        return self.save(name, start["goal"],
                         planner_mode=start.get("planner_mode", ""),
                         schedule=schedule,
                         source_run=os.path.basename(run_dir))

    def get(self, name):
        path, _ = self._path(name)
        if not os.path.isfile(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    def list(self):
        out = []
        for fname in sorted(os.listdir(self.root)):
            if fname.endswith(".json"):
                with open(os.path.join(self.root, fname)) as fh:
                    out.append(json.load(fh))
        return out

    def delete(self, name):
        path, _ = self._path(name)
        if os.path.isfile(path):
            os.unlink(path)
            return True
        return False

    def mark_run(self, name):
        data = self.get(name)
        if data:
            data["last_run_at"] = time.time()
            data["run_count"] = data.get("run_count", 0) + 1
            path, _ = self._path(name)
            with open(path, "w") as fh:
                json.dump(data, fh, indent=2)
        return data


# --------------------------------------------------------------------------
# Minimal cron: five fields (min hour day-of-month month day-of-week),
# supporting '*', numbers, comma lists, ranges, and */n steps.
# --------------------------------------------------------------------------

def _field_matches(field, value, lo, hi):
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            return True
        if part.startswith("*/"):
            step = int(part[2:])
            if (value - lo) % step == 0:
                return True
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if int(a) <= value <= int(b):
                return True
            continue
        if int(part) == value:
            return True
    return False


def cron_matches(expression, when=None):
    when = when or datetime.now()
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron expression needs five fields")
    dow = (when.weekday() + 1) % 7  # cron: Sunday=0
    return (
        _field_matches(fields[0], when.minute, 0, 59)
        and _field_matches(fields[1], when.hour, 0, 23)
        and _field_matches(fields[2], when.day, 1, 31)
        and _field_matches(fields[3], when.month, 1, 12)
        and _field_matches(fields[4], dow, 0, 6)
    )


def due_routines(store, when=None):
    return [r for r in store.list()
            if r.get("schedule") and cron_matches(r["schedule"], when)]
