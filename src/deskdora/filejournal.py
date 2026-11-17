"""Undo for file actions (v3.3).

The agent journals every file operation it completes into the
run's audit log as ``file_op`` events (source, destination, and a
content hash where feasible).  Undo replays that journal in
reverse on the real filesystem.

Honesty rules:
- A destination that no longer exists is reported as *missing* —
  nothing is invented to replace it.
- A destination whose content changed since the run (hash
  mismatch) is a *conflict* — it is never silently overwritten.
- An original location that has been reoccupied is a *conflict* —
  the newer file is never silently overwritten either.
- Only operations this module can reverse safely are attempted;
  everything else is reported, not forced.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time

from .audit import read_log


def journal_for_run(run_dir):
    """The run's recorded file operations, in execution order."""
    events = read_log(os.path.join(run_dir, "log.jsonl"))
    return [e for e in events if e.get("kind") == "file_op"]


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def undo_run(run_dir):
    """Reverse a run's file operations.  Returns a report dict:
    {undone: [...], conflicts: [{... , reason}], missing: [...]}.
    Appends an ``undo`` event to the run's log so History shows
    that the undo happened (and what it could not do)."""
    records = journal_for_run(run_dir)
    undone, conflicts, missing = [], [], []
    for record in reversed(records):
        src, dst = record.get("src"), record.get("dst")
        entry = {"src": src, "dst": dst, "op": record.get("op")}
        if record.get("op") != "move" or not src or not dst:
            conflicts.append({**entry, "reason":
                              "only plain moves can be undone"})
            continue
        if not os.path.exists(dst):
            missing.append({**entry, "reason":
                            "the moved file is no longer there"})
            continue
        if os.path.exists(src):
            conflicts.append({**entry, "reason":
                              "a file exists at the original "
                              "location again — not overwriting it"})
            continue
        recorded = record.get("sha256")
        if recorded:
            try:
                if _hash(dst) != recorded:
                    conflicts.append({**entry, "reason":
                                      "the file changed since the "
                                      "run — not overwriting it"})
                    continue
            except Exception:
                conflicts.append({**entry, "reason":
                                  "the file could not be verified"})
                continue
        try:
            parent = os.path.dirname(src)
            if parent:
                os.makedirs(parent, exist_ok=True)
            shutil.move(dst, src)
            undone.append(entry)
        except Exception as exc:
            conflicts.append({**entry, "reason": f"undo failed: {exc}"})
    report = {"undone": undone, "conflicts": conflicts,
              "missing": missing, "total": len(records)}
    try:
        with open(os.path.join(run_dir, "log.jsonl"), "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "kind": "undo",
                                 **report}) + "\n")
    except Exception:
        pass
    return report
