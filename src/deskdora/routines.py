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
             source_run=None, trigger=None, goal_template=None,
             params=None):
        path, safe = self._path(name)
        data = {
            "name": safe, "goal": goal, "planner_mode": planner_mode,
            "schedule": schedule, "source_run": source_run,
            "trigger": trigger,
            "goal_template": goal_template,
            "params": params or [],
            "created_at": time.time(), "last_run_at": None,
            "run_count": 0,
        }
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)
        return data

    def record_from_run(self, name, run_dir, schedule=None, trigger=None):
        events = read_log(os.path.join(run_dir, "log.jsonl"))
        start = next((e for e in events if e["kind"] == "run_start"), None)
        end = next((e for e in reversed(events) if e["kind"] == "run_end"),
                   None)
        if not start or not end or end.get("status") != "success":
            raise ValueError("only a successful run can become a routine")
        return self.save(name, start["goal"],
                         planner_mode=start.get("planner_mode", ""),
                         schedule=schedule, trigger=trigger,
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


# --------------------------------------------------------------------------
# Teach a routine from history (v3.3): parameterise a finished run's
# goal — path literals become routine parameters whose defaults are
# the original values, so the routine works for the next folder,
# not just the one in the recording.
# --------------------------------------------------------------------------

_PATH_RE = re.compile(r"/[\w.\-]+(?:/[\w.\-]+)+")
_SAVE_AS_RE = re.compile(r"\bsaved as (\S+\.(?:gnumeric|ods|xlsx|csv))")


def parameterize_goal(goal):
    """Return (template, params): the goal with its variable
    literals replaced by {placeholders}, and the parameter list
    [{name, default}] in order of appearance."""
    params = []
    template = goal

    def add_param(name, value):
        for existing in params:
            if existing["name"] == name:
                if existing["default"] == value:
                    return "{" + name + "}"
                return add_param(f"{name}_{len(params) + 1}", value)
        params.append({"name": name, "default": value})
        return "{" + name + "}"

    def path_sub(match):
        path = match.group(0)
        name = "folder" if template.lower().startswith(
            ("organise", "organize", "tidy")) and not params \
            else "path"
        return add_param(name, path)

    template = _PATH_RE.sub(path_sub, template)
    template = _SAVE_AS_RE.sub(
        lambda m: "saved as " + add_param("filename", m.group(1)),
        template)
    return template, params


def render_goal(routine, values=None):
    """Instantiate a routine's goal from its template + parameter
    values (defaults fill whatever the user didn't change)."""
    template = routine.get("goal_template")
    if not template:
        return routine.get("goal", "")
    merged = {p["name"]: p["default"]
              for p in routine.get("params") or []}
    for key, value in (values or {}).items():
        if key in merged and str(value).strip():
            merged[key] = str(value)
    try:
        return template.format(**merged)
    except (KeyError, IndexError):
        return routine.get("goal", "")


def record_parameterized(store, name, run_dir, schedule=None,
                         trigger=None):
    """Save a successful run as a routine with a parameterised
    goal (the v3.2 save-as-routine, upgraded)."""
    events = read_log(os.path.join(run_dir, "log.jsonl"))
    start = next((e for e in events if e["kind"] == "run_start"), None)
    end = next((e for e in reversed(events) if e["kind"] == "run_end"),
               None)
    if not start or not end or end.get("status") != "success":
        raise ValueError("only a successful run can become a routine")
    template, params = parameterize_goal(start["goal"])
    return store.save(name, start["goal"],
                      planner_mode=start.get("planner_mode", ""),
                      schedule=schedule, trigger=trigger,
                      source_run=os.path.basename(run_dir),
                      goal_template=template, params=params)


# --------------------------------------------------------------------------
# Routines in plain words (v3.3): a sentence -> a routine draft.
# The built-in parser covers the common shapes; anything it cannot
# read refuses honestly instead of inventing a routine.
# --------------------------------------------------------------------------

_WEEKDAYS = {"monday": 1, "tuesday": 2, "wednesday": 3,
             "thursday": 4, "friday": 5, "saturday": 6, "sunday": 0}
_HOME_DIRS = {"downloads": "~/Downloads", "desktop": "~/Desktop",
              "documents": "~/Documents", "pictures": "~/Pictures",
              "home": "~"}


def _parse_schedule(text):
    """Return (cron, remaining_text) or (None, text)."""
    low = text.lower()
    m = re.search(r"every (morning|evening|night)\b", low)
    if m:
        hour = {"morning": 9, "evening": 19, "night": 22}[m.group(1)]
        return f"0 {hour} * * *", text[:m.start()] + text[m.end():]
    m = re.search(r"every day at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low)
    if m:
        hour = int(m.group(1))
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        return f"{int(m.group(2) or 0)} {hour} * * *", \
            text[:m.start()] + text[m.end():]
    m = re.search(r"\bdaily at (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", low)
    if m:
        hour = int(m.group(1))
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        return f"{int(m.group(2) or 0)} {hour} * * *", \
            text[:m.start()] + text[m.end():]
    m = re.search(r"every (monday|tuesday|wednesday|thursday|friday|"
                  r"saturday|sunday)\b", low)
    if m:
        return f"0 9 * * {_WEEKDAYS[m.group(1)]}", \
            text[:m.start()] + text[m.end():]
    if re.search(r"\bevery hour\b", low):
        m = re.search(r"\bevery hour\b", low)
        return "0 * * * *", text[:m.start()] + text[m.end():]
    if re.search(r"\bevery week\b", low):
        m = re.search(r"\bevery week\b", low)
        return "0 9 * * 1", text[:m.start()] + text[m.end():]
    if re.search(r"\bevery day\b|\bdaily\b", low):
        m = re.search(r"\bevery day\b|\bdaily\b", low)
        return "0 9 * * *", text[:m.start()] + text[m.end():]
    return None, text


def describe_routine(text):
    """Parse a plain-words routine description into a draft
    {goal, schedule, trigger}.  Raises ValueError with an honest
    reason when the sentence can't be read — the same refusal
    contract as the planner's."""
    text = " ".join((text or "").split()).strip()
    if not text:
        raise ValueError("describe the routine first")
    schedule, rest = _parse_schedule(text)
    trigger = None
    m = re.search(r"when(?:ever)? files (?:land|arrive|appear) in "
                  r"(\S+)", rest, re.IGNORECASE)
    if m:
        folder = os.path.expanduser(m.group(1).rstrip(".,"))
        trigger = {"folder": folder}
        rest = (rest[:m.start()] + rest[m.end():]).strip(" ,")
    goal = " ".join(rest.split()).strip(" ,")
    # "my Downloads" / "the Downloads folder" -> a real path, and an
    # organise verb -> the canonical organise goal.
    home_match = re.search(
        r"\b(?:my|the) (downloads|desktop|documents|pictures)"
        r"(?: folder)?\b", goal, re.IGNORECASE)
    if home_match:
        path = os.path.expanduser(
            _HOME_DIRS[home_match.group(1).lower()])
        if re.match(r"(?i)(organise|organize|tidy|sort|clean up)\b",
                    goal):
            goal = f"Organise folder {path} by type"
        else:
            goal = (goal[:home_match.start()] + path
                    + goal[home_match.end():])
    if not goal:
        raise ValueError(
            "I couldn't find the task in that description — say "
            "what should happen, e.g. 'every morning organise my "
            "Downloads'")
    if schedule is None and trigger is None:
        raise ValueError(
            "I couldn't find when it should run — add 'every "
            "morning', 'daily at 9', 'every Monday', or 'when files "
            "land in …'")
    goal = goal[0].upper() + goal[1:]
    return {"goal": goal, "schedule": schedule, "trigger": trigger}
