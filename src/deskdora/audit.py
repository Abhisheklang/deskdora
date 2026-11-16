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

from . import actions


def _slug(text, limit=40):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:limit] or "run").rstrip("-")


class RunLog:
    def __init__(self, root, goal, planner_mode):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = os.path.join(root, f"{stamp}-{_slug(goal)}")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "log.jsonl")
        self.goal = goal
        self.planner_mode = planner_mode
        self._fh = open(self.path, "a", buffering=1)
        self._step = 0
        self.event("run_start", goal=goal, planner_mode=planner_mode)

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
            actions.screenshot(path)
            return name
        except Exception as exc:  # screenshot failure must not kill the run
            self.event("screenshot_failed", error=str(exc))
            return None

    def finish(self, status, **fields):
        self.event("run_end", status=status, **fields)
        self._fh.close()


def read_log(path):
    events = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def list_runs(root):
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root), reverse=True):
        path = os.path.join(root, name, "log.jsonl")
        if os.path.isfile(path):
            try:
                events = read_log(path)
            except Exception:
                continue
            start = next((e for e in events if e["kind"] == "run_start"), {})
            end = next((e for e in reversed(events) if e["kind"] == "run_end"), {})
            out.append({
                "id": name, "dir": os.path.join(root, name),
                "goal": start.get("goal", ""), "planner_mode": start.get("planner_mode", ""),
                "status": end.get("status", "running"),
                "answer": end.get("answer"),
                "events": events,
            })
    return out
