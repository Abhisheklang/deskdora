"""Template gallery (v3.4): ready-made tasks as *goals*.

A template is not a macro and not a safety bypass — one click
loads its goal into the Run view, editable, and execution goes
through the normal planner + confirmation + autonomy path like
any typed goal.  Built-ins ship with DeskDora; personal templates
are the user's own (saved from history runs), stored as JSON in
the data dir."""
from __future__ import annotations

import json
import os
import re
import time

BUILTIN_TEMPLATES = [
    {"id": "builtin-organise-downloads", "name": "Organise Downloads by type",
     "goal": "Organise folder {home}/Downloads by type",
     "note": "Sorts the Downloads folder into per-type subfolders."},
    {"id": "builtin-rename-photos", "name": "Rename photos by date",
     "goal": "Rename the photos in {home}/Pictures by their date",
     "note": "Needs a planner that can plan file renames; the "
             "built-in planner will refuse honestly if it can't."},
    {"id": "builtin-pdf-to-sheet", "name": "PDF table → spreadsheet",
     "goal": "Create a spreadsheet from {home}/Documents/table.pdf "
             "saved as table.gnumeric",
     "note": "Edit the PDF path before running."},
    {"id": "builtin-tidy-desktop", "name": "Tidy the Desktop",
     "goal": "Organise folder {home}/Desktop by type",
     "note": "Sorts the Desktop into per-type subfolders."},
    {"id": "builtin-empty-trash", "name": "Empty the trash",
     "goal": "Empty the trash",
     "note": "Destructive — the confirmation and autonomy layers "
             "apply exactly as for a typed goal."},
]


def builtin_templates(home=None):
    home = home or os.path.expanduser("~")
    out = []
    for tpl in BUILTIN_TEMPLATES:
        item = dict(tpl)
        item["goal"] = tpl["goal"].replace("{home}", home)
        item["builtin"] = True
        out.append(item)
    return out


class TemplateStore:
    """Personal templates: one JSON file in the data dir."""

    def __init__(self, path):
        self.path = path

    def _load(self):
        try:
            with open(self.path) as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self, items):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(items, fh, indent=2)

    def list(self):
        return list(self._load())

    def add(self, name, goal):
        name = " ".join((name or "").split()).strip()
        goal = (goal or "").strip()
        if not name or not goal:
            raise ValueError("a template needs a name and a goal")
        items = [it for it in self._load() if it["name"] != name]
        slug = re.sub(r"[^a-z0-9]+", "-",
                      name.lower()).strip("-") or "template"
        item = {"id": f"personal-{slug}", "name": name,
                "goal": goal, "note": "Your template.",
                "builtin": False, "created_at": time.time()}
        items.append(item)
        self._save(items)
        return item

    def delete(self, template_id):
        items = self._load()
        kept = [it for it in items if it["id"] != template_id]
        if len(kept) == len(items):
            return False
        self._save(kept)
        return True

    def replace_all(self, items):
        """Replace the whole personal-template set (backup
        import, v3.7.3).  Only well-formed entries survive —
        a name and a goal are the minimum a template is."""
        kept = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            name = " ".join(str(it.get("name") or "").split()).strip()
            goal = str(it.get("goal") or "").strip()
            if not name or not goal:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-",
                          name.lower()).strip("-") or "template"
            kept.append({
                "id": it.get("id") or f"personal-{slug}",
                "name": name, "goal": goal,
                "note": it.get("note") or "Your template.",
                "builtin": False,
                "created_at": it.get("created_at") or time.time()})
        self._save(kept)
        return kept
