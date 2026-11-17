"""Scheduler: fires saved routines on cron schedules and triggers.

A routine may carry:

* ``schedule`` — a five-field cron expression (the dialect in
  routines.cron_matches), checked once per minute, and/or
* ``trigger`` — ``{"type": "folder_change", "path": ...}``: fires
  when the watched folder's top-level contents change (name, size or
  mtime).  The first sighting of a folder is a baseline, never a fire.

Firing means asking the run manager to start the routine's goal —
the *same* path as pressing RUN NOW, so confirmation prompts,
autonomy policy, the kill switch, and the audit log all apply
unchanged.  If a run is already active the firing is skipped and the
skip is logged; the scheduler never queues a second run on top of
one in flight.

Every firing (and skip) is appended to a JSONL event log in the data
directory and kept in a small in-memory ring for the API/UI.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

from .routines import cron_matches


def folder_signature(path):
    """A comparable snapshot of a folder's top level, or None when
    the folder cannot be read."""
    try:
        entries = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
                entries.append((name, st.st_size, int(st.st_mtime)))
            except OSError:
                continue
        return tuple(entries)
    except OSError:
        return None


class Scheduler:
    def __init__(self, store, runner, events_path, poll_seconds=20):
        """store: RoutineStore.  runner: callable(routine) ->
        (started: bool, detail: str)."""
        self.store = store
        self.runner = runner
        self.events_path = events_path
        self.poll_seconds = poll_seconds
        self._last_fired = {}        # routine name -> "YYYY-MM-DD HH:MM"
        self._signatures = {}        # routine name -> folder signature
        self._trigger_armed = {}     # routine name -> bool
        self._events = []            # ring, newest last
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    # -- events ---------------------------------------------------------
    def _record(self, kind, routine_name, detail=""):
        event = {"ts": time.time(), "kind": kind,
                 "routine": routine_name, "detail": detail}
        with self._lock:
            self._events.append(event)
            self._events = self._events[-100:]
            try:
                os.makedirs(os.path.dirname(self.events_path),
                            exist_ok=True)
                with open(self.events_path, "a") as fh:
                    fh.write(json.dumps(event) + "\n")
            except OSError:
                pass
        return event

    def events(self, limit=50):
        with self._lock:
            return list(self._events[-limit:])

    # -- firing ------------------------------------------------------------
    def _fire(self, routine, reason):
        started, detail = self.runner(routine)
        self._record("fired" if started else "skipped",
                     routine["name"],
                     f"{reason}: {detail}" if detail else reason)
        return started

    def tick(self, now=None):
        """One scheduling pass.  Returns the list of routine names
        fired this pass.  Deterministic in `now` for tests."""
        now = now or datetime.now()
        fired = []
        for routine in self.store.list():
            name = routine["name"]
            schedule = routine.get("schedule")
            if schedule:
                minute_key = now.strftime("%Y-%m-%d %H:%M")
                if self._last_fired.get(name) != minute_key:
                    try:
                        due = cron_matches(schedule, now)
                    except ValueError:
                        due = False
                    if due:
                        self._last_fired[name] = minute_key
                        if self._fire(routine, f"cron {schedule}"):
                            fired.append(name)
            trigger = routine.get("trigger") or {}
            if trigger.get("type") == "folder_change" \
                    and trigger.get("path"):
                sig = folder_signature(trigger["path"])
                if sig is None:
                    continue
                previous = self._signatures.get(name)
                self._signatures[name] = sig
                if previous is None or previous == sig:
                    # Baseline sighting, or no change: (re-)arm.
                    self._trigger_armed[name] = True
                    continue
                # The folder changed.  Fire only when armed: after a
                # firing, the next change is absorbed as the new
                # baseline — it is usually the fired run's own work
                # (e.g. the organise it performed), and absorbing it
                # keeps a self-watching routine from refiring on
                # itself forever.
                if self._trigger_armed.get(name, True):
                    self._trigger_armed[name] = False
                    if self._fire(routine,
                                  f"folder changed: {trigger['path']}"):
                        fired.append(name)
        return fired

    # -- service loop ---------------------------------------------------------
    def start(self):
        if self._thread is not None:
            return
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    # A bad routine must never kill the scheduler.
                    pass
                self._stop.wait(self.poll_seconds)

        self._thread = threading.Thread(target=loop, daemon=True,
                                        name="deskdora-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread = None
