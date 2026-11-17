"""Task queue — multiple works, one at a time, in order.

A desktop has exactly one pointer and one keyboard, so two agent
runs can never truly execute at the same time on one screen: they
would fight over focus and corrupt each other's steps.  The queue is
the honest form of "multiple works": tasks are lined up, each one
runs in turn with the full safety layer (confirmations, autonomy,
kill switch), and each lands in the same audit history as a direct
run.  Nothing here pretends to be parallel.

The queue drives a *runner* — in the app, the server's RunManager —
through four callables, so the queue itself is testable without a
desktop:

* ``busy()``        — is a run active right now?
* ``start(goal, planner)`` — begin a run; raises :class:`BusyError`
  if one is already active.
* ``join()``        — block until the active run finishes; returns
  its result dict (or None).
* ``kill_tripped()``— has the kill switch been tripped?

Queue state persists to a JSON file so a restart doesn't lose
pending work; an item that was mid-run at shutdown comes back as
``interrupted``, never silently re-run.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

ITEM_QUEUED = "queued"
ITEM_RUNNING = "running"
FINISHED = ("success", "failed", "blocked", "killed", "interrupted")
MAX_FINISHED_KEPT = 50


class BusyError(RuntimeError):
    """The runner already has a run in progress."""


class TaskQueue:
    def __init__(self, runner, path=None):
        self.runner = runner
        self.path = path
        self.on_drained = None      # optional callback (sound tone)
        self._cond = threading.Condition()
        self._items = []            # list of dicts, execution order
        self._paused = False
        self._halted = False        # set when the kill switch trips
        self._worker = None
        self._stopped = False
        self._load()

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------
    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path) as fh:
                data = json.load(fh)
        except Exception:
            return
        for item in data.get("items", []):
            if item.get("status") == ITEM_RUNNING:
                item["status"] = "interrupted"
                item["finished_at"] = item.get("finished_at") or time.time()
            self._items.append(item)
        self._paused = bool(data.get("paused", False))

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump({"items": self._items,
                           "paused": self._paused}, fh, indent=1)
            os.replace(tmp, self.path)
        except Exception:
            pass  # persistence must never break the queue

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def add(self, goal, planner=None):
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal is empty")
        item = {
            "id": uuid.uuid4().hex[:10],
            "goal": goal,
            "planner": planner or "",
            "status": ITEM_QUEUED,
            "queued_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "result_status": None,
        }
        with self._cond:
            self._items.append(item)
            self._save()
            self._cond.notify_all()
        return dict(item)

    def remove(self, item_id):
        """Remove a still-queued item.  Returns False if the item is
        unknown or already running/finished."""
        with self._cond:
            for i, item in enumerate(self._items):
                if item["id"] == item_id:
                    if item["status"] != ITEM_QUEUED:
                        return False
                    del self._items[i]
                    self._save()
                    self._cond.notify_all()
                    return True
        return False

    def move(self, item_id, direction):
        """Swap a queued item with its queued neighbour ('up' moves
        it earlier).  The running item never moves."""
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")
        with self._cond:
            idx = next((i for i, it in enumerate(self._items)
                        if it["id"] == item_id), None)
            if idx is None:
                return False
            item = self._items[idx]
            if item["status"] != ITEM_QUEUED:
                return False
            step = -1 if direction == "up" else 1
            j = idx + step
            while 0 <= j < len(self._items):
                if self._items[j]["status"] == ITEM_QUEUED:
                    self._items[idx], self._items[j] = \
                        self._items[j], self._items[idx]
                    self._save()
                    self._cond.notify_all()
                    return True
                j += step
        return False

    def clear(self):
        """Drop every queued (not running/finished) item."""
        with self._cond:
            before = len(self._items)
            self._items = [it for it in self._items
                           if it["status"] != ITEM_QUEUED]
            removed = before - len(self._items)
            self._save()
            self._cond.notify_all()
            return removed

    def pause(self):
        with self._cond:
            self._paused = True
            self._save()
            self._cond.notify_all()

    def resume(self):
        with self._cond:
            self._paused = False
            self._halted = False
            self._save()
            self._cond.notify_all()

    def halt(self):
        """Stop starting new items (kill switch).  The running item
        is stopped by the kill switch itself; this only fences the
        queue until the user resumes it."""
        with self._cond:
            self._halted = True
            self._cond.notify_all()

    def snapshot(self):
        with self._cond:
            items = [dict(it) for it in self._items]
        return {
            "items": items,
            "paused": self._paused,
            "halted": self._halted,
            "queued": sum(1 for it in items
                          if it["status"] == ITEM_QUEUED),
            "running_id": next((it["id"] for it in items
                                if it["status"] == ITEM_RUNNING), None),
        }

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------
    def start(self):
        if self._worker is not None:
            return
        self._stopped = False
        self._worker = threading.Thread(target=self._loop, daemon=True,
                                        name="deskdora-queue")
        self._worker.start()

    def stop(self):
        self._stopped = True
        with self._cond:
            self._cond.notify_all()

    def _next_queued(self):
        for item in self._items:
            if item["status"] == ITEM_QUEUED:
                return item
        return None

    def _loop(self):
        while not self._stopped:
            with self._cond:
                item = None
                while not self._stopped:
                    if not self._paused and not self._halted:
                        item = self._next_queued()
                        if item is not None:
                            break
                    self._cond.wait(timeout=1.0)
                if self._stopped or item is None:
                    return
                item["status"] = ITEM_RUNNING
                item["started_at"] = time.time()
                self._save()
            self._execute(item)

    def _execute(self, item):
        # The desktop is shared: a manual, routine or proactive run
        # may already own it.  Wait our turn rather than failing.
        while not self._stopped and self.runner.busy():
            time.sleep(0.4)
        if self._stopped:
            with self._cond:
                item["status"] = ITEM_QUEUED
                item["started_at"] = None
                self._save()
            return
        try:
            self.runner.start(item["goal"], item["planner"] or None)
        except BusyError:
            with self._cond:
                item["status"] = ITEM_QUEUED
                item["started_at"] = None
                self._cond.notify_all()
            time.sleep(0.5)
            return
        result = self.runner.join()
        status = (result or {}).get("status") or "failed"
        if status not in FINISHED:
            status = "failed"
        with self._cond:
            item["status"] = status
            item["result_status"] = status
            item["finished_at"] = time.time()
            if status == "killed" or self.runner.kill_tripped():
                self._halted = True
            # keep the file small: cap finished history
            finished = [it for it in self._items
                        if it["status"] in FINISHED]
            if len(finished) > MAX_FINISHED_KEPT:
                drop = {id(it) for it in
                        finished[:len(finished) - MAX_FINISHED_KEPT]}
                self._items = [it for it in self._items
                               if id(it) not in drop]
            self._save()
            self._cond.notify_all()
            drained = not any(
                it["status"] in (ITEM_QUEUED, ITEM_RUNNING)
                for it in self._items)
        if drained and self.on_drained is not None:
            try:
                self.on_drained()
            except Exception:
                pass  # a sound must never break the queue
