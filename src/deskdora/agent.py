"""The agent loop: observe -> plan -> act -> verify.

One step at a time, re-observing after every action.  The loop stops
honestly — on failure, on a denied confirmation, or when the kill switch
trips — and never improvises clicks the planner did not ask for.
"""
from __future__ import annotations

import re
import subprocess
import time

from . import actions, perception
from .planners import PlanError, validate_plan
from .safety import classify_step


class StepFailure(Exception):
    pass


def parse_table(raw):
    """Parse clipboard text into rows of cells.  Structured separators
    (tab, 2+ spaces, pipe) win; a line that yields a single cell falls
    back to plain whitespace splitting (browser PDF copies collapse
    columns to single spaces)."""
    rows = []
    for line in raw.splitlines():
        cells = [c.strip() for c in re.split(r"\t| {2,}|\|", line)]
        cells = [c for c in cells if c != ""]
        if len(cells) < 2:
            cells = line.split()
        if len(cells) >= 2:
            rows.append(cells)
    return rows


class Agent:
    def __init__(self, planner, planner_mode, gate, kill_switch, allow_list,
                 run_log, max_steps=400, on_event=None, settle=0.45):
        self.planner = planner
        self.planner_mode = planner_mode
        self.gate = gate
        self.kill = kill_switch
        self.allow_list = allow_list
        self.log = run_log
        self.max_steps = max_steps
        self.on_event = on_event or (lambda kind, **fields: None)
        self.settle = settle
        self.answers = {}
        self.executed = 0
        self.plan_observation = None
        self.last_target = None       # element dict for live-view highlight
        self.last_screenshot = None

    # -- helpers ---------------------------------------------------------
    def _emit(self, kind, **fields):
        self.log.event(kind, **fields)
        self.on_event(kind, **fields)

    def _check_kill(self):
        if self.kill.is_tripped():
            raise StepFailure("kill switch tripped")

    def _shot(self, tag):
        name = self.log.screenshot(tag)
        if name:
            self.last_screenshot = name
        return name

    def _resolve(self, step):
        """Return a fresh Element for a click/type/scroll step."""
        if "element" in step:
            base = self.plan_observation.elements[step["element"]]
            fresh = perception.refind(base)
            if fresh is None:
                raise StepFailure(f"element {step['element']} no longer present")
            return fresh
        target = step.get("target")
        if target:
            obs = perception.observe()
            query = {k: v for k, v in target.items() if k != "nth"}
            matches = obs.find(**query)
            nth = target.get("nth", 0)
            if len(matches) <= nth:
                raise StepFailure(f"no element matches target {target}")
            return matches[nth]
        return None

    # -- primitive execution ----------------------------------------------
    def _exec_primitive(self, step):
        action = step["action"]
        if action == "launch":
            if not self.allow_list.permits(step["command"]):
                raise StepFailure(f"app not in allow-list: {step['command']}")
            actions.launch(step["command"], wait=0)
            return f"launched {step['command']}"
        if action in ("click", "double_click"):
            el = self._resolve(step)
            self.last_target = el.to_dict()
            self._emit("target", element=el.to_dict())
            actions.activate_window(getattr(el, "window", ""))
            cx, cy = el.center
            if action == "click":
                actions.click(cx, cy)
            else:
                actions.double_click(cx, cy)
            return f"clicked {el.role} '{el.name}' at {cx},{cy}"
        if action == "click_at":
            x, y = int(step["x"]), int(step["y"])
            self.last_target = {"role": "point", "name": f"{x},{y}",
                                "bounds": [x - 12, y - 12, 24, 24]}
            self._emit("target", element=self.last_target)
            actions.click(x, y)
            return f"clicked point {x},{y} (coordinate grounding)"
        if action == "type":
            if step.get("target"):
                el = self._resolve(step)
                self.last_target = el.to_dict()
                actions.activate_window(getattr(el, "window", ""))
                actions.click(*el.center)
                time.sleep(0.25)
            actions.type_text(step["text"])
            return f"typed {step['text'][:40]!r}"
        if action == "key":
            actions.key(step["key"])
            return f"key {step['key']}"
        if action == "scroll":
            el = self._resolve(step) if step.get("target") else None
            x, y = el.center if el else (640, 400)
            actions.scroll(x, y, step.get("direction", "down"))
            return f"scrolled {step.get('direction', 'down')}"
        if action == "wait":
            end = time.time() + float(step.get("seconds", 1))
            while time.time() < end:
                self._check_kill()
                time.sleep(min(0.2, max(0.0, end - time.time())))
            return f"waited {step.get('seconds', 1)}s"
        if action == "extract":
            obs = perception.observe()
            haystacks = list(obs.texts.values())
            for text in haystacks:
                m = re.search(step["pattern"], text)
                if m:
                    value = m.group(1) if m.groups() else m.group(0)
                    self.answers[step.get("name", "answer")] = value.strip()
                    return f"extracted {step.get('name')} = {value.strip()!r}"
            raise StepFailure(f"extract pattern not found: {step['pattern']}")
        if action == "read_clipboard":
            res = subprocess.run(["xclip", "-o", "-selection", "clipboard"],
                                 capture_output=True, text=True, timeout=15)
            text = res.stdout
            if not text.strip():
                raise StepFailure("clipboard is empty")
            self.answers[step.get("name", "clipboard")] = text
            return f"read {len(text)} chars from clipboard"
        if action == "build_sheet":
            return self._build_sheet(step)
        raise StepFailure(f"unhandled action {action}")

    def _build_sheet(self, step):
        """Macro: enter the clipboard table into a spreadsheet, cell by
        cell, with primitive logged sub-steps, then save via the GUI."""
        raw = self.answers.get("table") or self.answers.get("clipboard") or ""
        rows = parse_table(raw)
        if not rows:
            raise StepFailure("no table rows found in clipboard text")
        width = max(len(r) for r in rows)
        sub = []
        actions.key("ctrl+Home")
        time.sleep(0.3)
        for r, row in enumerate(rows):
            for c in range(width):
                value = row[c] if c < len(row) else ""
                if value:
                    actions.type_text(value)
                actions.key("Tab")
                sub.append(f"{chr(65 + c)}{r + 1}={value}")
                self.executed += 1
                self._check_kill()
            # End of row: Enter returns to the first tabbed column, next row
            # (standard spreadsheet behaviour after Tab-entry).
            actions.key("Return")
            time.sleep(0.15)
        self._emit("macro_substeps", macro="build_sheet", cells=sub)
        # Save through the GUI save dialog.
        save_as = step["save_as"]
        actions.key("ctrl+s")
        time.sleep(1.2)
        obs = perception.observe()
        save_buttons = obs.find(role="push button", exact_name="Save")
        if save_buttons:
            # A save dialog is open: focus its location/name entry,
            # type the filename, confirm.  GTK save dialogs append the
            # format extension themselves, so type the bare stem —
            # typing "name.gnumeric" produces "name.gnumeric.gnumeric"
            # (observed with Gnumeric in the rig).
            stem = save_as
            if stem.lower().endswith(".gnumeric"):
                stem = stem[: -len(".gnumeric")]
            actions.key("ctrl+l")
            time.sleep(0.3)
            actions.type_text(stem)
            time.sleep(0.3)
            actions.key("Return")
            time.sleep(1.0)
            obs = perception.observe()
            replaces = obs.find(role="push button", name_contains="Replace")
            if replaces:
                actions.click(*replaces[0].center)
                time.sleep(0.8)
            return f"entered {len(rows)}x{width} table and saved as {save_as}"
        replaces = obs.find(role="push button", name_contains="Replace")
        if replaces:
            actions.click(*replaces[0].center)
            time.sleep(0.8)
            return f"entered {len(rows)}x{width} table and re-saved ({save_as})"
        return (f"entered {len(rows)}x{width} table; no save dialog "
                f"appeared (direct save assumed)")

    # -- main loop -----------------------------------------------------------
    def run(self, goal):
        status, answer, reason = "failed", None, ""
        try:
            obs = perception.observe()
            self.plan_observation = obs
            self._shot("initial")
            try:
                plan = self.planner.plan(goal, obs)
                plan = validate_plan(plan, element_count=len(obs.elements)
                                     if hasattr(self.planner, "model") else None)
            except PlanError as exc:
                self._emit("plan_failed", error=str(exc))
                self.log.finish("failed", reason=f"planning failed: {exc}")
                return {"status": "failed", "reason": f"planning failed: {exc}",
                        "answers": {}}
            self._emit("plan", steps=plan, planner_mode=self.planner_mode)
            replans = 1 if hasattr(self.planner, "model") else 0
            i = 0
            while i < len(plan):
                step = plan[i]
                self._check_kill()
                if self.executed >= self.max_steps:
                    raise StepFailure(f"step cap reached ({self.max_steps})")
                action = step["action"]
                if action == "done":
                    answer = step.get("answer") or answer
                    status = "success"
                    break
                if action == "fail":
                    raise StepFailure(step.get("reason", "planner gave up"))
                # Safety gate before anything destructive.
                if classify_step(step) == "destructive":
                    desc = f"{action} {step.get('target') or step.get('key') or ''}"
                    approved = self.gate.check(step, desc)
                    self._emit("confirmation", step=step, approved=approved)
                    if not approved:
                        status = "blocked"
                        reason = f"confirmation denied for: {desc}"
                        break
                self._emit("step_start", index=i, step=step)
                try:
                    detail = self._exec_primitive(step)
                    self.executed += 1
                    time.sleep(self.settle)
                    shot = self._shot(f"step{i}")
                    self._emit("step_result", index=i, ok=True,
                               detail=detail, screenshot=shot)
                    if step.get("expect_text"):
                        obs2 = perception.observe()
                        joined = "\n".join(obs2.texts.values())
                        names = " ".join(e.name for e in obs2.elements)
                        if step["expect_text"] not in joined and step["expect_text"] not in names:
                            raise StepFailure(
                                f"expected to see {step['expect_text']!r} after step")
                except StepFailure as exc:
                    shot = self._shot(f"step{i}-failed")
                    self._emit("step_result", index=i, ok=False,
                               error=str(exc), screenshot=shot)
                    if replans > 0:
                        replans -= 1
                        self._emit("replan", after_step=i, error=str(exc))
                        obs = perception.observe()
                        self.plan_observation = obs
                        plan = validate_plan(self.planner.plan(
                            goal, obs, history=f"step {i} failed: {exc}"))
                        self._emit("plan", steps=plan,
                                   planner_mode=self.planner_mode, replan=True)
                        i = 0
                        continue
                    raise
                i += 1
            else:
                status = "success"  # plan ran to completion without done
        except StepFailure as exc:
            status, reason = "failed", str(exc)
            if "kill switch" in reason:
                status = "killed"
        except Exception as exc:  # never crash without a log line
            status, reason = "failed", f"unexpected: {type(exc).__name__}: {exc}"
        if self.answers:
            answer = answer or self.answers.get("answer") or \
                next(iter(self.answers.values()), None)
            if not isinstance(answer, str):
                answer = str(answer)
        self._shot("final")
        self.log.finish(status, answer=answer, reason=reason,
                        answers=self.answers, steps_executed=self.executed)
        return {"status": status, "answer": answer, "reason": reason,
                "answers": self.answers, "steps_executed": self.executed}
