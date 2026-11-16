"""Perception: read the desktop through the AT-SPI accessibility tree.

The tree is flattened into a numbered list of *elements* — the same trick
browser-automation tools use with the DOM.  The planner picks an element
(by number, or by a role/name query resolved at execution time) instead of
guessing pixels, which is what makes the agent reliable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

import pyatspi

# Roles worth handing to a planner: things you can click, type into, read,
# or select.  Pure layout containers are walked but not listed.
ACTIONABLE_ROLES = {
    "push button", "toggle button", "check box", "radio button",
    "menu", "menu item", "check menu item", "radio menu item",
    "text", "password text", "combo box", "list item", "page tab",
    "table cell", "link", "icon", "slider", "spin button", "tree item",
    "tab", "tool bar button", "split button", "label",
}

MAX_ELEMENTS = 400
MAX_NODES = 30000
MAX_TEXT_CHARS = 20000
_INVALID = -2147483648


@dataclass
class Element:
    idx: int
    app: str
    window: str
    role: str
    name: str = ""
    label: str = ""            # text of a LABELLED_BY relation (form labels)
    description: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    actions: list = field(default_factory=list)
    editable: bool = False
    focused: bool = False
    text: str = ""             # own text content (truncated)
    path: tuple = ()           # child-index path from the application node

    @property
    def center(self):
        return (self.x + self.w // 2, self.y + self.h // 2)

    def signature(self):
        return (self.app, self.role, self.name, self.label)

    def to_dict(self):
        d = asdict(self)
        d["path"] = list(self.path)
        return d


@dataclass
class Observation:
    elements: list               # list[Element]
    windows: list                # list[str] window titles, active first-ish
    texts: dict                  # window title -> visible text (for extraction)
    taken_at: float = 0.0

    def find(self, role=None, name_contains=None, label_contains=None,
             app_contains=None, exact_name=None):
        """Return elements matching all given criteria (case-insensitive)."""
        out = []
        for el in self.elements:
            if role and el.role != role:
                continue
            if exact_name is not None and el.name != exact_name:
                continue
            if name_contains and name_contains.lower() not in el.name.lower():
                continue
            if label_contains and label_contains.lower() not in el.label.lower():
                continue
            if app_contains and app_contains.lower() not in el.app.lower():
                continue
            out.append(el)
        return out

    def summary_for_planner(self, limit=120):
        """Compact element list for an LLM prompt."""
        lines = []
        for el in self.elements[:limit]:
            bits = f"[{el.idx}] {el.role}"
            if el.name:
                bits += f" '{el.name}'"
            if el.label:
                bits += f" (label: {el.label})"
            if el.editable:
                bits += " <editable>"
            if el.text and el.role in ("text", "password text"):
                bits += f" = '{el.text[:40]}'"
            lines.append(bits)
        return "\n".join(lines)


def _extents(node):
    try:
        ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        return ext.x, ext.y, ext.width, ext.height
    except Exception:
        return _INVALID, _INVALID, 0, 0


def _valid(x, y, w, h):
    return x != _INVALID and y != _INVALID and w > 0 and h > 0 and x > -10000 and y > -10000


def _label_of(node):
    try:
        for rel in node.getRelationSet():
            if rel.getRelationType() == pyatspi.RELATION_LABELLED_BY:
                tgt = rel.getTarget(0)
                if tgt is not None and tgt.name:
                    return tgt.name
    except Exception:
        pass
    return ""


def _own_text(node, cap=300):
    try:
        ti = node.queryText()
        n = ti.characterCount
        if n <= 0:
            return ""
        return ti.getText(0, min(n, cap))
    except Exception:
        return ""


def observe(max_depth=14):
    """Walk the whole desktop and return an Observation."""
    desktop = pyatspi.Registry.getDesktop(0)
    elements = []
    windows = []
    texts = {}
    budget = [MAX_NODES]

    def walk(node, app_name, window_title, path, depth):
        if budget[0] <= 0 or depth > max_depth or node is None:
            return
        budget[0] -= 1
        try:
            role = node.getRoleName()
        except Exception:
            return
        name = ""
        try:
            name = node.name or ""
        except Exception:
            pass
        if role in ("frame", "dialog", "alert", "window") and name:
            window_title = name
            if name not in windows:
                windows.append(name)
        x, y, w, h = _extents(node)
        # Collect readable text for extraction (documents, pages, labels).
        if role in ("text", "paragraph", "document", "document frame",
                    "heading", "static", "label", "terminal"):
            t = _own_text(node, cap=4000) if role != "label" else name
            if t:
                key = window_title or app_name
                cur = texts.get(key, "")
                if len(cur) < MAX_TEXT_CHARS:
                    texts[key] = (cur + "\n" + t)[:MAX_TEXT_CHARS]
        if role in ACTIONABLE_ROLES and _valid(x, y, w, h) and len(elements) < MAX_ELEMENTS:
            try:
                states = node.getState()
                editable = states.contains(pyatspi.STATE_EDITABLE)
                focused = states.contains(pyatspi.STATE_FOCUSED)
            except Exception:
                editable = focused = False
            acts = []
            try:
                qa = node.queryAction()
                acts = [qa.getName(i) for i in range(qa.nActions)]
            except Exception:
                pass
            elements.append(Element(
                idx=len(elements), app=app_name, window=window_title,
                role=role, name=name, label=_label_of(node),
                x=x, y=y, w=w, h=h, actions=acts, editable=editable,
                focused=focused,
                text=_own_text(node) if role in ("text", "password text") else "",
                path=tuple(path),
            ))
        try:
            for i in range(node.childCount):
                walk(node.getChildAtIndex(i), app_name, window_title,
                     path + [i], depth + 1)
        except Exception:
            pass
        # Some GtkTreeViews (settings lists, spreadsheets) expose their
        # rows ONLY through the Table interface, not as tree children.
        # Enumerate cells so the planner can see and click them.
        if role in ("table", "tree table"):
            try:
                qt = node.queryTable()
                rows = min(qt.nRows, 200)
                cols = min(qt.nColumns, 20)
                seen_ids = set()
                for r in range(rows):
                    for c in range(cols):
                        try:
                            cell = qt.getAccessibleAtRowColumn(r, c)
                        except Exception:
                            cell = None
                        if cell is None or id(cell) in seen_ids:
                            continue
                        seen_ids.add(id(cell))
                        walk(cell, app_name, window_title,
                             path + [("cell", r, c)], depth + 1)
            except Exception:
                pass

    for i in range(desktop.childCount):
        app = desktop.getChildAtIndex(i)
        if app is None:
            continue
        walk(app, app.name or f"app{i}", "", [i], 0)
    return Observation(elements=elements, windows=windows, texts=texts,
                       taken_at=time.time())


def refind(element):
    """Re-locate an element from a previous observation.

    Follows the stored child-index path first and verifies the signature;
    falls back to a fresh observation search by signature.  Returns a fresh
    Element (current bounds) or None.
    """
    desktop = pyatspi.Registry.getDesktop(0)
    try:
        node = desktop
        ok = True
        for seg in element.path:
            if isinstance(seg, tuple) and len(seg) == 3 and seg[0] == "cell":
                node = node.queryTable().getAccessibleAtRowColumn(seg[1], seg[2])
            else:
                node = node.getChildAtIndex(seg)
            if node is None:
                ok = False
                break
        if ok and node is not None:
            role = node.getRoleName()
            name = node.name or ""
            if role == element.role and name == element.name:
                x, y, w, h = _extents(node)
                if _valid(x, y, w, h):
                    fresh = Element(idx=element.idx, app=element.app,
                                    window=element.window, role=role, name=name,
                                    label=element.label, x=x, y=y, w=w, h=h,
                                    path=element.path)
                    return fresh
    except Exception:
        pass
    obs = observe()
    for cand in obs.find(role=element.role, exact_name=element.name,
                         app_contains=element.app):
        if cand.label == element.label:
            return cand
    matches = obs.find(role=element.role, exact_name=element.name)
    return matches[0] if matches else None
