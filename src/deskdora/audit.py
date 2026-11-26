"""Audit log: every run produces a directory with a JSONL event log and
one screenshot per step, so any run can be replayed and checked after the
fact.  This is the product's accountability story: nothing the agent does
is invisible.
"""
from __future__ import annotations

import json
import os
import re
import time


def _slug(text, limit=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit] or "run").rstrip("-")


class RunLog:
    def __init__(self, root, goal, planner_mode, source="ui"):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = os.path.join(root, f"{stamp}-{_slug(goal)}")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "log.jsonl")
        self.goal = goal
        self.planner_mode = planner_mode
        self._fh = open(self.path, "a", buffering=1)
        self._step = 0
        self.event("run_start", goal=goal, planner_mode=planner_mode,
                   source=source)

    def event(self, kind, **fields):
        record = {"ts": time.time(), "kind": kind}
        record.update(fields)
        self._fh.write(json.dumps(record) + "\n")
        return record

    def screenshot(self, tag="step"):
        self._step += 1
        name = f"{self._step:03d}-{tag}.png"
        path = os.path.join(self.dir, name)
        try:
            from .drivers import get_driver
            get_driver().screenshot(path)
            return name
        except Exception as exc:  # screenshot failure must not kill the run
            self.event("screenshot_failed", error=str(exc))
            return None

    def finish(self, status, **fields):
        # Cleanup-path hardening (v3.4.1): finishing the log is
        # what run teardown calls last, so a broken audit sink
        # (full disk, EPERM write block) must never raise here and
        # strand the run's state — the server's finally owns the
        # state, and this method must not fight it.
        try:
            self.event("run_end", status=status, **fields)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass


def read_log(path):
    """Parse a run log.  A line that fails to parse (a crash or
    power loss mid-write leaves a truncated last line) is skipped,
    not fatal: one malformed line must not make the whole run
    vanish from history or turn its detail view into a 500
    (vibe CR2)."""
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


_summary_cache = {}


def _summarise_log(path):
    """(start, end, count) for one log file, cached on mtime+size
    so polling callers do not re-parse every log on disk on every
    request (vibe V1)."""
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return {}, {}, 0
    cached = _summary_cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    events = read_log(path)
    start = next((e for e in events if e["kind"] == "run_start"), {})
    end = next((e for e in reversed(events) if e["kind"] == "run_end"), {})
    result = (start, end, len(events))
    _summary_cache[path] = (key, result)
    return result


def list_runs(root, include_events=True, limit=None):
    """List runs, newest first.

    With include_events=False (the polling list view) each entry is
    a summary only - id, goal, status, planner, event count - and
    no full event list is shipped or held (vibe V1).  Callers that
    genuinely need the events (estimates) keep the default."""
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), reverse=True):
        path = os.path.join(root, name, "log.jsonl")
        if os.path.isfile(path):
            try:
                if include_events:
                    events = read_log(path)
                    start = next((e for e in events
                                  if e["kind"] == "run_start"), {})
                    end = next((e for e in reversed(events)
                                if e["kind"] == "run_end"), {})
                    count = len(events)
                else:
                    start, end, count = _summarise_log(path)
                    events = None
            except Exception:
                continue
            entry = {
                "id": name, "dir": os.path.join(root, name),
                "goal": start.get("goal", ""), "planner_mode": start.get("planner_mode", ""),
                "source": start.get("source", "ui"),
                "status": end.get("status", "running"),
                "answer": end.get("answer"),
                "event_count": count,
            }
            if include_events:
                entry["events"] = events
            out.append(entry)
            if limit and len(out) >= limit:
                break
    return out
