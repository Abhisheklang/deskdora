"""The agent loop: observe -> plan -> act -> verify.

One step at a time, re-observing after every action.  The loop stops
honestly — on failure, on a denied confirmation, or when the kill switch
trips — and never improvises clicks the planner did not ask for.
"""
from __future__ import annotations

import re
import time

from .drivers import get_driver
from .drivers.base import DriverError
from .planners import PlanError, validate_plan
from .autonomy import evaluate as autonomy_evaluate
from .safety import classify_step


class StepFailure(Exception):
    pass


def _launch_app_hint(command):
    """The app a launch command means to raise, for focus adoption:
    the program's basename — or, for interpreter launches
    (``python3 form_app.py``), the script's basename."""
    tokens = (command or "").split()
    if not tokens:
        return None
    first = tokens[0].rsplit("/", 1)[-1]
    if first in ("python3", "python", "sh", "bash", "node", "env") \
            and len(tokens) > 1:
        return tokens[1].rsplit("/", 1)[-1]
    return first


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
                 run_log, max_steps=400, on_event=None, settle=0.45,
                 driver=None, autonomy=None):
        self.planner = planner
        self.planner_mode = planner_mode
        self.gate = gate
        self.kill = kill_switch
        self.allow_list = allow_list
        self.log = run_log
        self.max_steps = max_steps
        self.on_event = on_event or (lambda kind, **fields: None)
        self.settle = settle
        # Per-category autonomy policy (deskdora.autonomy) or None
        # for the legacy all-confirm behaviour.
        self.autonomy = autonomy
        self.d = driver or get_driver()
        self.answers = {}
        self.executed = 0
        self.plan_observation = None
        self.last_target = None       # element dict for live-view highlight
        self.last_screenshot = None
        # Focus guard state (see _guard_focus): the app/window that
        # untargeted typing and keystrokes are *intended* for.
        self.focus_app = None
        self.focus_window = None
        self._launch_snapshot = None  # window titles before a launch
        self._launch_hint = None      # app the last launch meant to raise

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
            fresh = self.d.refind(base)
            if fresh is None:
                raise StepFailure(f"element {step['element']} no longer present")
            return fresh
        target = step.get("target")
        if target:
            obs = self.d.observe()
            query = {k: v for k, v in target.items() if k != "nth"}
            matches = obs.find(**query)
            nth = target.get("nth", 0)
            if len(matches) <= nth:
                raise StepFailure(f"no element matches target {target}")
            return matches[nth]
        return None

    # -- focus guard -----------------------------------------------------
    # Untargeted `type`/`key` steps deliver input to *whatever window
    # currently holds focus*.  v1.0 shipped with the known failure that
    # typing followed focus into the wrong window (e.g. a duplicate
    # form window) and the run still reported success.  The guard pins
    # an intended app/window — from the last targeted interaction, or
    # from the window a launch step brought up — and before every
    # untargeted input step verifies that focus really is there.  If
    # it is not, it tries to raise the intended window; if focus still
    # cannot be verified there, the step fails honestly instead of
    # typing into the void.
    #
    # Matching is by *application* when an AT-SPI focused element is
    # visible (window titles legitimately change as an app navigates —
    # Thunar retitles itself per folder), and by window title via the
    # driver's active-window query otherwise.  On platforms with no
    # focus query at all (Wayland), the guard degrades to the AT-SPI
    # focused-element check and logs when it cannot verify.
    def _pin_focus(self, app=None, window=None):
        if app:
            self.focus_app = app
        if window:
            self.focus_window = window

    def _focused_element(self, obs):
        for el in obs.elements:
            if getattr(el, "focused", False):
                return el
        return None

    def _adopt_launched_window(self):
        """Try once to pin the window a pending launch meant to
        raise.  Signals, in order: the focused element if it is new
        or belongs to the launched app; any element in a new window;
        any element of the launched app (its window may predate the
        launch — a leftover instance)."""
        try:
            obs = self.d.observe()
        except Exception:
            return False
        hint = (self._launch_hint or "").lower()
        new = [e for e in obs.elements
               if e.window and e.window not in self._launch_snapshot]
        hinted = [e for e in obs.elements
                  if hint and e.app and hint in e.app.lower()]
        focused = self._focused_element(obs)
        adopted = None
        if focused is not None and (
                focused.window not in self._launch_snapshot
                or (hint and hint in (focused.app or "").lower())):
            adopted = focused
        elif new:
            adopted = new[0]
        elif hinted:
            adopted = hinted[0]
        if adopted is None:
            return False
        self._pin_focus(adopted.app, adopted.window)
        self._launch_snapshot = None
        self._launch_hint = None
        return True

    def _guard_focus(self):
        # A launch since the last check may have brought up the window
        # subsequent input is meant for: adopt it before verifying.
        if self._launch_snapshot is not None:
            adopted = self._adopt_launched_window()
            if not adopted and self._launch_hint:
                # The launched app may still be starting; give it a
                # moment, then refuse rather than verify against the
                # previous app's pin — that stale-pin pass is how a
                # spreadsheet's cells once got typed into a browser.
                for _ in range(4):
                    time.sleep(0.5)
                    if self._adopt_launched_window():
                        adopted = True
                        break
                if not adopted:
                    raise StepFailure(
                        f"focus guard: the launched app "
                        f"({self._launch_hint}) did not present a "
                        "window; refusing to type into the previous "
                        "application")
        if self.focus_app is None and self.focus_window is None:
            if self._launch_snapshot is not None:
                # A launched app has not shown a window yet.  Do not
                # pin whatever happens to hold focus now (it may be
                # DeskDora's own window); proceed unpinned this once —
                # the next untargeted step retries the adoption.
                self._emit("focus_guard", result="awaiting-launch-window")
                return
            # Nothing pinned yet: trust the current focus (this is the
            # state right after a launch + wait) and pin it.
            try:
                obs = self.d.observe()
                focused = self._focused_element(obs)
            except Exception:
                focused = None
            if focused is not None:
                self._pin_focus(focused.app, focused.window)
            else:
                title = self.d.active_window_title()
                if title:
                    self._pin_focus(window=title)
            return
        # Verify.
        try:
            obs = self.d.observe()
            focused = self._focused_element(obs)
        except Exception:
            obs, focused = None, None
        if focused is not None and self.focus_app:
            if focused.app == self.focus_app:
                self._pin_focus(window=focused.window)
                return
            # Focus is in another app: try to raise the intended one.
            if self._try_refocus():
                self._emit("focus_guard", result="refocused",
                           app=self.focus_app, window=self.focus_window)
                return
            raise StepFailure(
                f"focus guard: keystrokes would go to "
                f"'{focused.app}' ({focused.window!r}), but this run is "
                f"working in '{self.focus_app}' ({self.focus_window!r})")
        # No focused element visible (transient dialogs report focus
        # poorly).  Map the active window to its owning application
        # through the observation and judge at app level — a dialog
        # of the right app must not trip the guard just because its
        # title differs from the main window's.
        active = self.d.active_window_title()
        if active and obs is not None and self.focus_app:
            owner = self._window_owner(obs, active)
            if owner == self.focus_app:
                return
            if owner is not None:
                if self._try_refocus():
                    self._emit("focus_guard", result="refocused",
                               app=self.focus_app, window=self.focus_window)
                    return
                raise StepFailure(
                    f"focus guard: active window {active!r} belongs to "
                    f"'{owner}', but this run is working in "
                    f"'{self.focus_app}' ({self.focus_window!r})")
        if active and self.focus_window:
            if self._titles_match(active, self.focus_window):
                return
            if self._try_refocus():
                self._emit("focus_guard", result="refocused",
                           window=self.focus_window)
                return
            raise StepFailure(
                f"focus guard: active window is {active!r}, expected "
                f"{self.focus_window!r}; refusing to type blind")
        if focused is None and not active:
            # The platform cannot tell us where focus is (e.g. Wayland
            # without a focused AT-SPI element).  Proceed, but leave a
            # trace that this step's focus was not verified.
            self._emit("focus_guard", result="unverified",
                       app=self.focus_app, window=self.focus_window)

    def _try_refocus(self):
        """Raise the intended window and give the desktop a moment
        to actually move focus (AT-SPI focus state lags the window
        manager under load — a single instant recheck produced
        false refusals in a busy rig).  True only when focus is
        *seen* in the intended app/window afterwards."""
        if not self.focus_window:
            return False
        if not self.d.activate_window(self.focus_window):
            return False
        deadline = time.time() + 1.5
        while time.time() < deadline:
            time.sleep(0.25)
            try:
                obs = self.d.observe()
                focused = self._focused_element(obs)
            except Exception:
                focused = None
            if focused is not None and self.focus_app:
                if focused.app == self.focus_app:
                    self._pin_focus(window=focused.window)
                    return True
            elif focused is None:
                active = self.d.active_window_title()
                if active and self._titles_match(active,
                                                 self.focus_window):
                    return True
        return False

    @staticmethod
    def _titles_match(a, b):
        return (a == b or a.lower() in b.lower() or b.lower() in a.lower())

    def _window_owner(self, obs, active_title):
        """Which app owns the window whose AT-SPI title matches the
        WM's active-window title (exact match first, then the
        contains rule — the WM appends app names, AT-SPI does not)."""
        for el in obs.elements:
            if el.window and el.window == active_title:
                return el.app
        for el in obs.elements:
            if el.window and self._titles_match(el.window, active_title):
                return el.app
        return None

    # -- primitive execution ----------------------------------------------
    def _exec_primitive(self, step):
        action = step["action"]
        if action == "launch":
            if not self.allow_list.permits(step["command"]):
                raise StepFailure(f"app not in allow-list: {step['command']}")
            try:
                self._launch_snapshot = set(self.d.observe().windows)
            except Exception:
                self._launch_snapshot = None
            self._launch_hint = _launch_app_hint(step["command"])
            self.d.launch(step["command"])
            return f"launched {step['command']}"
        if action in ("click", "double_click"):
            el = self._resolve(step)
            if el is not None and not getattr(el, "sensitive", True):
                raise StepFailure(
                    f"element '{el.name}' ({el.role}) is disabled "
                    "(greyed out) and cannot be clicked")
            self.last_target = el.to_dict()
            self._emit("target", element=el.to_dict())
            self.d.activate_window(getattr(el, "window", ""))
            self._pin_focus(getattr(el, "app", None),
                            getattr(el, "window", None))
            cx, cy = el.center
            if action == "click":
                self.d.click(cx, cy)
            else:
                self.d.double_click(cx, cy)
            return f"clicked {el.role} '{el.name}' at {cx},{cy}"
        if action == "click_at":
            x, y = int(step["x"]), int(step["y"])
            self.last_target = {"role": "point", "name": f"{x},{y}",
                                "bounds": [x - 12, y - 12, 24, 24]}
            self._emit("target", element=self.last_target)
            self.d.click(x, y)
            return f"clicked point {x},{y} (coordinate grounding)"
        if action == "type":
            if step.get("target"):
                el = self._resolve(step)
                self.last_target = el.to_dict()
                self.d.activate_window(getattr(el, "window", ""))
                self._pin_focus(getattr(el, "app", None),
                                getattr(el, "window", None))
                self.d.click(*el.center)
                time.sleep(0.25)
            else:
                self._guard_focus()
            self.d.type_text(step["text"])
            return f"typed {step['text'][:40]!r}"
        if action == "key":
            self._guard_focus()
            self.d.key(step["key"])
            return f"key {step['key']}"
        if action == "scroll":
            el = self._resolve(step) if step.get("target") else None
            x, y = el.center if el else (640, 400)
            self.d.scroll(x, y, step.get("direction", "down"))
            return f"scrolled {step.get('direction', 'down')}"
        if action == "wait":
            end = time.time() + float(step.get("seconds", 1))
            while time.time() < end:
                self._check_kill()
                time.sleep(min(0.2, max(0.0, end - time.time())))
            return f"waited {step.get('seconds', 1)}s"
        if action == "extract":
            obs = self.d.observe()
            haystacks = list(obs.texts.values())
            for text in haystacks:
                m = re.search(step["pattern"], text)
                if m:
                    value = m.group(1) if m.groups() else m.group(0)
                    self.answers[step.get("name", "answer")] = value.strip()
                    return f"extracted {step.get('name')} = {value.strip()!r}"
            raise StepFailure(f"extract pattern not found: {step['pattern']}")
        if action == "read_clipboard":
            try:
                text = self.d.read_clipboard()
            except DriverError as exc:
                raise StepFailure(f"clipboard read failed: {exc}")
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
        # The macro types and sends keys untargeted: verify focus is
        # in the spreadsheet window first (focus guard).
        self._guard_focus()
        width = max(len(r) for r in rows)
        sub = []
        self.d.key("ctrl+Home")
        time.sleep(0.3)
        for r, row in enumerate(rows):
            for c in range(width):
                value = row[c] if c < len(row) else ""
                if value:
                    self.d.type_text(value)
                self.d.key("Tab")
                sub.append(f"{chr(65 + c)}{r + 1}={value}")
                self.executed += 1
                self._check_kill()
            # End of row: Enter returns to the first tabbed column, next row
            # (standard spreadsheet behaviour after Tab-entry).
            self.d.key("Return")
            time.sleep(0.15)
        self._emit("macro_substeps", macro="build_sheet", cells=sub)
        # Save through the GUI save dialog.
        save_as = step["save_as"]
        # Re-verify focus immediately before saving: another app can
        # raise itself during the long cell-entry phase (a browser
        # finishing its first-run startup did exactly this in the
        # rig, and the save keystroke opened the *browser's* save
        # dialog instead of the spreadsheet's).
        self._guard_focus()
        save_started = time.time()
        self.d.key("ctrl+s")
        # The save dialog can be slow to present under load; poll
        # briefly instead of assuming after a single look.
        save_buttons = []
        for _ in range(6):
            time.sleep(0.6)
            obs = self.d.observe()
            save_buttons = obs.find(role="push button", exact_name="Save")
            if save_buttons:
                break
        if save_buttons:
            # A save dialog is open: focus its location/name entry,
            # type the filename, confirm.  GTK save dialogs append the
            # format extension themselves, so type the bare stem —
            # typing "name.gnumeric" produces "name.gnumeric.gnumeric"
            # (observed with Gnumeric in the rig).
            stem = save_as
            if stem.lower().endswith(".gnumeric"):
                stem = stem[: -len(".gnumeric")]
            self.d.key("ctrl+l")
            time.sleep(0.3)
            self.d.type_text(stem)
            time.sleep(0.3)
            self.d.key("Return")
            time.sleep(1.0)
            obs = self.d.observe()
            replaces = obs.find(role="push button", name_contains="Replace")
            if replaces:
                self.d.click(*replaces[0].center)
                time.sleep(0.8)
        else:
            replaces = obs.find(role="push button", name_contains="Replace")
            if replaces:
                self.d.click(*replaces[0].center)
                time.sleep(0.8)
        # Verify the save actually produced the file — never assume.
        # The v3.0 final benchmark caught the previous behaviour
        # ("no save dialog appeared, direct save assumed") reporting
        # success for spreadsheets that were never written.  An
        # unverifiable save is a failed step, said out loud.
        import os
        found = None
        deadline = time.time() + 6
        while time.time() < deadline and not found:
            for base in (os.getcwd(), os.path.expanduser("~"),
                         os.path.expanduser("~/Downloads")):
                candidate = os.path.join(base, save_as)
                if os.path.isfile(candidate) \
                        and os.path.getmtime(candidate) >= save_started - 2:
                    found = candidate
                    break
            if not found:
                time.sleep(0.5)
        if not found:
            raise StepFailure(
                f"spreadsheet save could not be verified: {save_as} "
                "not found in cwd, home, or Downloads after the save "
                "attempt")
        return (f"entered {len(rows)}x{width} table and saved as "
                f"{save_as} (verified)")

    # -- main loop -----------------------------------------------------------
    def run(self, goal):
        status, answer, reason = "failed", None, ""
        try:
            obs = self.d.observe()
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
                        "answers": {}, "steps_executed": 0}
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
                # Safety gate: the per-category autonomy policy
                # decides — deny (category set to never), confirm
                # (ask the human through the gate), or allow (safe
                # step, or a significant step in a free-run category,
                # which is logged as an auto-approval).  With no
                # policy configured this is exactly the pre-v3.0
                # behaviour: destructive steps confirm, safe steps run.
                decision, category, significant = autonomy_evaluate(
                    self.autonomy, step,
                    context=getattr(self, "context", ""))
                if decision == "deny":
                    self._emit("autonomy", step=step, category=category,
                               decision="deny")
                    status = "blocked"
                    reason = (f"autonomy policy: '{category}' actions "
                              "are set to never — step refused")
                    break
                if decision == "confirm":
                    desc = f"{action} {step.get('target') or step.get('key') or ''}"
                    approved = self.gate.check(step, desc)
                    self._emit("confirmation", step=step, approved=approved)
                    if not approved:
                        status = "blocked"
                        reason = f"confirmation denied for: {desc}"
                        break
                elif significant:
                    self._emit("autonomy", step=step, category=category,
                               decision="auto-approved")
                self._emit("step_start", index=i, step=step)
                try:
                    detail = self._exec_primitive(step)
                    self.executed += 1
                    time.sleep(self.settle)
                    shot = self._shot(f"step{i}")
                    self._emit("step_result", index=i, ok=True,
                               detail=detail, screenshot=shot)
                    if step.get("expect_text"):
                        obs2 = self.d.observe()
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
                        obs = self.d.observe()
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
