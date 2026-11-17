"""Proactive work, within limits.

DeskDora may *notice* useful work on its own: a watched folder
filling up with loose files is the canonical case.  Noticing creates
a **proposal** — a persistent, inspectable record.  What happens
next is governed by the autonomy policy, exactly like user-asked
work:

* category `files` is **free-run** → the proposal executes at once
  (through the normal run path: audit log, kill switch, everything),
  and the proposal records that it auto-executed.
* otherwise → the proposal waits.  A human approves or dismisses it
  in the UI (or from the phone companion).  Approval starts the same
  run a manual request would.

Every proposal and every transition is kept in a local JSON file and
mirrored into the scheduler-style event stream the UI shows.  A
proposal for a folder is only created when the folder crosses the
loose-file threshold and no open proposal for it exists; once a
proposal is executed or dismissed, the folder must change materially
(5+ new loose files) before it is proposed again — the agent does
not nag.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from .planners import FILE_TYPE_GROUPS

ORGANISABLE_EXTS = {ext for exts in FILE_TYPE_GROUPS.values()
                    for ext in exts}
DEFAULT_THRESHOLD = 5


def loose_files(path):
    """Loose, organisable files at a folder's top level."""
    try:
        out = []
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isfile(full) and "." in name:
                if name.rsplit(".", 1)[1].lower() in ORGANISABLE_EXTS:
                    out.append(name)
        return out
    except OSError:
        return []


class ProposalStore:
    def __init__(self, path):
        self.path = path
        self.items = []
        self.load()

    def load(self):
        try:
            with open(self.path) as fh:
                self.items = json.load(fh)
        except Exception:
            self.items = []

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.items, fh, indent=1)
        os.replace(tmp, self.path)

    def list(self, status=None):
        items = sorted(self.items, key=lambda p: p["created_at"],
                       reverse=True)
        if status:
            items = [p for p in items if p["status"] == status]
        return items

    def get(self, proposal_id):
        return next((p for p in self.items if p["id"] == proposal_id),
                    None)

    def create(self, kind, path, detail, loose_count):
        proposal = {
            "id": uuid.uuid4().hex[:8], "kind": kind, "path": path,
            "detail": detail, "loose_count": loose_count,
            "status": "proposed", "created_at": time.time(),
            "decided_at": None, "auto": False,
        }
        self.items.append(proposal)
        self.save()
        return proposal

    def set_status(self, proposal_id, status, auto=False):
        proposal = self.get(proposal_id)
        if proposal is None:
            return None
        proposal["status"] = status
        proposal["decided_at"] = time.time()
        proposal["auto"] = auto
        self.save()
        return proposal


class ProactiveWatcher:
    """Scans watched folders and raises/executes proposals.

    runner: callable(goal) -> (started, detail) — the same run-entry
    the scheduler uses.  policy: an AutonomyPolicy (or None).
    """

    def __init__(self, store, runner, policy=None,
                 threshold=DEFAULT_THRESHOLD):
        self.store = store
        self.runner = runner
        self.policy = policy
        self.threshold = threshold
        # path -> loose-file count at which a proposal was last
        # settled (executed or dismissed).  A folder is proposed
        # again only when it is materially messier than that, or
        # after it has been tidy in between.
        self._handled = {}

    def scan(self, folders):
        """One pass over the watched folders.  Returns the proposals
        created in this pass."""
        made = []
        for folder in folders or []:
            count = len(loose_files(folder))
            if count < self.threshold:
                self._handled.pop(folder, None)   # tidy: fully re-arm
                continue
            if any(p["path"] == folder
                   for p in self.store.list("proposed")):
                continue                          # already on the table
            handled_at = self._handled.get(folder)
            if handled_at is not None \
                    and count < handled_at + self.threshold:
                continue                          # not materially worse
            proposal = self.store.create(
                "organise", folder,
                f"{count} loose files that could be organised by type",
                count)
            made.append(proposal)
            level = self.policy.level_for("files") if self.policy \
                else "confirm"
            if level == "free":
                started, detail = self.runner(
                    f"Organise folder {folder} by type")
                if started:
                    self.store.set_status(proposal["id"], "executed",
                                          auto=True)
                    proposal["detail"] += " (auto-executed)"
                    self.store.save()
                    self._handled[folder] = count
        return made

    def approve(self, proposal_id):
        proposal = self.store.get(proposal_id)
        if proposal is None or proposal["status"] != "proposed":
            return None
        started, detail = self.runner(
            f"Organise folder {proposal['path']} by type")
        if started:
            self.store.set_status(proposal_id, "executed")
            self._handled[proposal["path"]] = proposal["loose_count"]
        return {"started": started, "detail": detail,
                "proposal": self.store.get(proposal_id)}

    def dismiss(self, proposal_id):
        proposal = self.store.set_status(proposal_id, "dismissed")
        if proposal is not None:
            self._handled[proposal["path"]] = proposal["loose_count"]
        return proposal
