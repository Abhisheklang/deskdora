"""The agent loop: observe -> plan -> act -> verify.

One step at a time, re-observing after every action.  The loop stops
honestly — on failure, on a denied confirmation, or when the kill switch
trips — and never improvises clicks the planner did not ask for.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time

from .drivers import get_driver
from .drivers.base import DriverError
from .planners import PlanError, extract_pattern_problem, validate_plan
from .autonomy import evaluate as autonomy_evaluate


class StepFailure(Exception):
    pass


# Extract-step safety bounds (EXTRACT-REGEX): the pattern comes
# from the user's chosen planner, so it is never run unbounded.
# Nested quantifiers are refused before anything runs, each text
# is capped, and the search itself runs on a daemon worker thread
# under a hard wall-clock budget - a pattern that outlives the
# budget fails the step instead of hanging the agent thread (the
# abandoned worker is a daemon and its result is discarded; the
# kill switch is only checked between steps, so the budget is
# what keeps a bad pattern from wedging the whole run).
EXTRACT_TIMEOUT_SECONDS = 2.0
EXTRACT_MAX_TEXT_CHARS = 200_000


def _bounded_extract_search(pattern, texts):
    """Search texts for pattern under the extract safety bounds.
    Returns the match object, or None when nothing matches.
    Raises StepFailure for a refused or timed-out pattern."""
    problem = extract_pattern_problem(pattern)
    if problem:
        raise StepFailure(f"extract pattern refused: {problem}")
    outcome = {}

    def run():
        try:
            compiled = re.compile(pattern)
            for text in texts:
                match = compiled.search(text[:EXTRACT_MAX_TEXT_CHARS])
                if match:
                    outcome["match"] = match
                    return
        except Exception as exc:   # never let the worker die silently
            outcome["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(EXTRACT_TIMEOUT_SECONDS)
    if worker.is_alive():
        raise StepFailure(
            f"extract pattern timed out after "
            f"{EXTRACT_TIMEOUT_SECONDS:g}s and was abandoned: "
            f"{pattern}")
    if "error" in outcome:
        raise StepFailure(
            f"extract pattern failed: {outcome['error']}")
    return outcome.get("match")


def describe_step(step):
    """A plan step as one readable line — used by the dry-run
    approval view and the exported run report, so a human can
    judge a plan without reading JSON."""
    action = step.get("action", "?")
    target = step.get("target") or {}
    tdesc = ""
    if target:
        bits = [target.get("name_contains") or target.get("label_contains")
                or target.get("text_contains") or target.get("role") or ""]
        tdesc = " ".join(b for b in bits if b)
    if "element" in step:
        tdesc = f"element {step['element']}"
    if action == "launch":
        return f"Open {step.get('command', '')}"
    if action == "wait":
        return f"Wait {step.get('seconds', 1)}s"
    if action == "key":
        return f"Press {step.get('key', '')}"
    if action in ("click", "double_click"):
        verb = "Double-click" if action == "double_click" else "Click"
        return f"{verb} {tdesc or 'point'}".rstrip()
    if action == "click_at":
        return f"Click at {step.get('x')},{step.get('y')}"
    if action == "type":
        text = str(step.get("text", ""))
        if len(text) > 42:
            text = text[:42] + "…"
        return f"Type '{text}'" + (f" into {tdesc}" if tdesc else "")
    if action == "scroll":
        return f"Scroll {step.get('direction', 'down')}"
    if action == "extract":
        return f"Read text matching {step.get('pattern', '')!r}"
    if action == "read_clipboard":
        return "Read the clipboard"
    if action == "build_sheet":
        return f"Build a spreadsheet and save as {step.get('save_as', '')}"
    if action == "done":
        return "Finish"
    if action == "fail":
        return f"Give up: {step.get('reason', '')}"
    return action


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
                 driver=None, autonomy=None, pause_gate=None,
                 denied_apps=None, recovery_enabled=True,
                 cancel_event=None):
        self.planner = planner
        self.planner_mode = planner_mode
        self.gate = gate
        self.kill = kill_switch
        # Per-run cancellation (v3.4.1): set permanently by the
        # server watchdog when this run is force-cleared.  Unlike
        # the shared kill switch — which the NEXT run resets — this
        # event is never cleared, so a worker that was stuck inside
        # a step when its run was force-cleared can never dispatch
        # another desktop action after it wakes up.
        self.cancel_event = cancel_event
        self.allow_list = allow_list
        self.log = run_log
        self.max_steps = max_steps
        self.on_event = on_event or (lambda kind, **fields: None)
        self.settle = settle
        # Per-category autonomy policy (deskdora.autonomy) or None
        # for the legacy all-confirm behaviour.
        self.autonomy = autonomy
        # Cooperative pause (safety.PauseGate) or None: checked at
        # each step boundary, never mid-action.
        self.pause_gate = pause_gate
        # Per-app permissions (v3.3): lowercase substrings of AT-SPI
        # application names the agent must not touch.  Enforced at
        # the action layer (like the money floor) and by filtering
        # the planner's observation.
        self.denied_apps = [str(a).lower() for a in (denied_apps or [])
                            if str(a).strip()]
        # Failure self-recovery (v3.3): one alternative attempt per
        # failed step, never more.
        self.recovery_enabled = recovery_enabled
        self._recovered = set()
        # File-change journal (v3.3): completed file operations,
        # mirrored into the audit log as file_op events so a run's
        # file changes can be undone from History.
        self.file_journal = []
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

    def _is_cancelled(self):
        """True when this run has been force-cleared by the server
        watchdog (v3.4.1).  Checked before every action dispatch:
        a stuck step that eventually returns must find its run
        cancelled and stop, not carry on with the next action."""
        return self.cancel_event is not None \
            and self.cancel_event.is_set()

    def _check_kill(self):
        if self.kill.is_tripped() or self._is_cancelled():
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

    # DeskDora's own desktop furniture (v3.2): the floating control
    # window and the edge light belong to the agent, not to the
    # user's work.  The overlay maps when a run starts — after a
    # launch step's snapshot — so its elements otherwise look like
    # "the new window the launch raised", and adoption would pin
    # the run to the overlay and then refuse to type into the real
    # app (measured live on the organise task).  Furniture is
    # transparent to adoption and pinning: never pinned, never
    # adopted, never the reason a step fails.
    FURNITURE_WINDOWS = frozenset({"DeskDora Control",
                                   "DeskDora Edge"})

    @classmethod
    def _is_furniture(cls, element):
        return (getattr(element, "window", None)
                in cls.FURNITURE_WINDOWS)

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
               if e.window and e.window not in self._launch_snapshot
               and not self._is_furniture(e)]
        hinted = [e for e in obs.elements
                  if hint and e.app and hint in e.app.lower()
                  and not self._is_furniture(e)]
        focused = self._focused_element(obs)
        if focused is not None and self._is_furniture(focused):
            focused = None
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
        self._check_kill()
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
            if focused is not None and self._is_furniture(focused):
                focused = None
            if focused is not None:
                self._pin_focus(focused.app, focused.window)
            else:
                title = self.d.active_window_title()
                if title and title not in self.FURNITURE_WINDOWS:
                    self._pin_focus(window=title)
            return
        # Verify.  The window manager's active window is where X11
        # keystrokes actually land; an AT-SPI "focused" element can
        # be stale — WebKitGTK keeps reporting its web view as
        # focused long after the window loses activation (measured
        # with DeskDora's own window: the WM active
        # window was Thunar while AT-SPI still claimed DeskDora).
        # So when the active window belongs to the intended app,
        # focus is right, whatever a stale element says.
        try:
            obs = self.d.observe()
            focused = self._focused_element(obs)
        except Exception:
            obs, focused = None, None
        active = self.d.active_window_title()
        if active and obs is not None and self.focus_app:
            if self._window_owner(obs, active) == self.focus_app:
                return
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
        false refusals on a busy desktop).  True only when focus is
        *seen* in the intended app/window afterwards."""
        if not self.focus_window:
            return False
        self._check_kill()
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
    # -- per-app permissions (v3.3) -----------------------------------
    def _check_app_allowed(self, app_name):
        """Refuse to touch a denied application — enforcement at
        the action layer, the same place the money floor lives.
        A planner (built-in or LLM) that targets a denied app gets
        this stated refusal, never a silent workaround."""
        if not app_name:
            return
        low = str(app_name).lower()
        for denied in self.denied_apps:
            if denied in low:
                raise StepFailure(
                    f"per-app permissions: '{app_name}' is on the "
                    "deny list (Settings → App permissions) — "
                    "the agent will not interact with it")

    def _exec_primitive(self, step):
        self._check_kill()
        action = step["action"]
        if action == "launch":
            if not self.allow_list.permits(step["command"]):
                raise StepFailure(f"app not in allow-list: {step['command']}")
            self._check_app_allowed(_launch_app_hint(step["command"]))
            self._check_app_allowed(step["command"].split()[0]
                                    .rsplit("/", 1)[-1])
            try:
                self._launch_snapshot = set(self.d.observe().windows)
            except Exception:
                self._launch_snapshot = None
            self._launch_hint = _launch_app_hint(step["command"])
            self._check_kill()
            self.d.launch(step["command"])
            return f"launched {step['command']}"
        if action in ("click", "double_click"):
            el = self._resolve(step)
            self._check_app_allowed(getattr(el, "app", None))
            if el is not None and not getattr(el, "sensitive", True):
                raise StepFailure(
                    f"element '{el.name}' ({el.role}) is disabled "
                    "(greyed out) and cannot be clicked")
            self.last_target = el.to_dict()
            self._emit("target", element=el.to_dict())
            self._check_kill()
            self.d.activate_window(getattr(el, "window", ""))
            self._pin_focus(getattr(el, "app", None),
                            getattr(el, "window", None))
            self._check_kill()
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
            self._check_kill()
            self.d.click(x, y)
            return f"clicked point {x},{y} (coordinate grounding)"
        if action == "type":
            if step.get("target"):
                el = self._resolve(step)
                self._check_app_allowed(getattr(el, "app", None))
                self.last_target = el.to_dict()
                self._check_kill()
                self.d.activate_window(getattr(el, "window", ""))
                self._pin_focus(getattr(el, "app", None),
                                getattr(el, "window", None))
                self.d.click(*el.center)
                time.sleep(0.25)
            else:
                self._guard_focus()
            self._check_kill()
            self.d.type_text(step["text"])
            return f"typed {step['text'][:40]!r}"
        if action == "key":
            self._guard_focus()
            self._check_kill()
            self.d.key(step["key"])
            return f"key {step['key']}"
        if action == "scroll":
            el = self._resolve(step) if step.get("target") else None
            self._check_app_allowed(getattr(el, "app", None))
            x, y = el.center if el else (640, 400)
            self._check_kill()
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
            m = _bounded_extract_search(step["pattern"],
                                        list(obs.texts.values()))
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
        self._check_kill()
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
        # finishing its first-run startup did exactly this
        # once, and the save keystroke opened the *browser's* save
        # dialog instead of the spreadsheet's).
        self._guard_focus()
        save_started = time.time()
        self._check_kill()
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
            # (observed with Gnumeric).
            stem = save_as
            if stem.lower().endswith(".gnumeric"):
                stem = stem[: -len(".gnumeric")]
            self._check_kill()
            self.d.key("ctrl+l")
            time.sleep(0.3)
            self._check_kill()
            self.d.type_text(stem)
            time.sleep(0.3)
            self.d.key("Return")
            time.sleep(1.0)
            obs = self.d.observe()
            replaces = obs.find(role="push button", name_contains="Replace")
            if replaces:
                self._check_kill()
                self.d.click(*replaces[0].center)
                time.sleep(0.8)
        else:
            replaces = obs.find(role="push button", name_contains="Replace")
            if replaces:
                self._check_kill()
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
    # -- planning ------------------------------------------------------
    def build_plan(self, goal):
        """Observe and plan, without executing.  Used by run() and
        by the dry-run approval flow, which shows this plan to the
        user before a single action is taken.  Per-app permissions
        apply here too: elements of denied apps are removed from
        the observation, so no planner can target what it cannot
        see (the action layer still refuses as a second line)."""
        obs = self.d.observe()
        if self.denied_apps:
            from .perception import Observation
            kept = [e for e in obs.elements
                    if not any(d in (e.app or "").lower()
                               for d in self.denied_apps)]
            obs = Observation(elements=kept, windows=obs.windows,
                              texts=obs.texts)
        self.plan_observation = obs
        self._shot("initial")
        plan = self.planner.plan(goal, obs)
        return validate_plan(
            plan, element_count=len(obs.elements)
            if hasattr(self.planner, "model") else None)

    # -- file-change journal (v3.3) ------------------------------------
    def _record_file_ops(self, file_op, index):
        for entry in file_op.get("files", []):
            record = {"op": file_op.get("op", "move"),
                      "src": entry.get("src"), "dst": entry.get("dst"),
                      "step": index, "sha256": None}
            try:
                dst = record["dst"]
                if dst and os.path.isfile(dst) \
                        and os.path.getsize(dst) <= 64 * 1024 * 1024:
                    digest = hashlib.sha256()
                    with open(dst, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            digest.update(chunk)
                    record["sha256"] = digest.hexdigest()
            except Exception:
                pass  # a hash is a bonus, never a blocker
            self.file_journal.append(record)
            self._emit("file_op", **record)

    # -- failure self-recovery (v3.3) ----------------------------------
    def _attempt_recovery(self, step, exc):
        """ONE alternative attempt for a failed step: re-observe,
        then a strategy matched to the failure — focus retry for
        focus-guard failures, scroll-into-view + fresh element
        match for targeted steps, a plain fresh attempt otherwise.
        Returns (ok, detail, strategy).  Never loops: the caller
        marks the step recovered-or-not exactly once."""
        self._check_kill()
        message = str(exc)
        time.sleep(0.6)  # let the desktop settle first
        if "focus guard" in message:
            strategy = "focus retry"
            self._try_refocus()
        elif step.get("target") or "element" in step:
            strategy = "fresh element match"
            bounds = (self.last_target or {}).get("bounds") or []
            if step["action"] in ("click", "double_click") \
                    and len(bounds) == 4:
                cx = bounds[0] + bounds[2] // 2
                cy = bounds[1] + bounds[3] // 2
                try:
                    self.d.scroll(cx, cy, "down")
                    self.d.scroll(cx, cy, "up")
                    strategy = "scroll into view + fresh match"
                except Exception:
                    pass
        else:
            strategy = "retry after re-observing"
        try:
            self.d.observe()
            detail = self._exec_and_verify(step)
            return True, detail, strategy
        except StepFailure as exc2:
            return False, f"{strategy} also failed: {exc2}", strategy

    def _exec_and_verify(self, step):
        self._check_kill()
        detail = self._exec_primitive(step)
        # A step that was stuck when its run was force-cleared
        # returns here eventually (v3.4.1): refuse to verify it or
        # let the run continue — the run is over, and the next
        # thing this worker would do is another desktop action.
        self._check_kill()
        if step.get("expect_text"):
            obs2 = self.d.observe()
            joined = "\n".join(obs2.texts.values())
            names = " ".join(e.name for e in obs2.elements)
            if step["expect_text"] not in joined \
                    and step["expect_text"] not in names:
                raise StepFailure(
                    f"expected to see {step['expect_text']!r} after step")
        return detail

    def run(self, goal):
        try:
            plan = self.build_plan(goal)
        except PlanError as exc:
            self._emit("plan_failed", error=str(exc))
            self.log.finish("failed", reason=f"planning failed: {exc}")
            return {"status": "failed", "reason": f"planning failed: {exc}",
                    "answers": {}, "steps_executed": 0}
        self._emit("plan", steps=plan, planner_mode=self.planner_mode)
        return self.execute_plan(goal, plan)

    def execute_plan(self, goal, plan, skip=frozenset()):
        """Execute a built plan.  ``skip`` is the dry-run contract:
        indices the user unticked are logged as skipped-by-user
        and never reach the executor — enforcement here, in the
        engine, not in any UI."""
        status, answer, reason = "failed", None, ""
        self._recovered = set()
        try:
            replans = 1 if hasattr(self.planner, "model") else 0
            i = 0
            while i < len(plan):
                step = plan[i]
                self._check_kill()
                if self.pause_gate is not None and self.pause_gate.paused:
                    self._emit("run_paused", index=i)
                    if not self.pause_gate.wait(self.kill):
                        raise StepFailure("kill switch tripped")
                    self._emit("run_resumed", index=i)
                if self.executed >= self.max_steps:
                    raise StepFailure(f"step cap reached ({self.max_steps})")
                if i in skip:
                    self._emit("step_start", index=i, step=step)
                    self._emit("step_result", index=i, ok=True,
                               detail="skipped by user",
                               skipped=True)
                    i += 1
                    continue
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
                    # The gate defers to the autonomy verdict: a
                    # step evaluate() ordered confirmed always
                    # reaches the human policy (vibe S1).
                    approved = self.gate.check(
                        step, desc, requires_confirmation=True)
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
                    detail = self._exec_and_verify(step)
                    self.executed += 1
                    time.sleep(self.settle)
                    shot = self._shot(f"step{i}")
                    self._emit("step_result", index=i, ok=True,
                               detail=detail, screenshot=shot)
                    if step.get("file_op"):
                        self._record_file_ops(step["file_op"], i)
                except StepFailure as exc:
                    # Failure self-recovery: exactly ONE alternative
                    # attempt (re-observe + a different approach),
                    # logged in the open.  A second failure ends
                    # the step exactly as it always has.
                    if self.recovery_enabled and i not in self._recovered \
                            and not self._is_cancelled():
                        self._recovered.add(i)
                        ok, rdetail, strategy = \
                            self._attempt_recovery(step, exc)
                        self._emit("recovery", index=i,
                                   strategy=strategy, ok=ok,
                                   detail=rdetail, error=str(exc))
                        if ok:
                            self.executed += 1
                            time.sleep(self.settle)
                            shot = self._shot(f"step{i}")
                            self._emit(
                                "step_result", index=i, ok=True,
                                detail=f"recovered ({strategy}): "
                                       f"{rdetail}",
                                screenshot=shot, recovered=True)
                            if step.get("file_op"):
                                self._record_file_ops(step["file_op"], i)
                            i += 1
                            continue
                        exc = StepFailure(
                            f"{exc} (recovery attempted: {rdetail})")
                    shot = self._shot(f"step{i}-failed")
                    self._emit("step_result", index=i, ok=False,
                               error=str(exc), screenshot=shot)
                    if replans > 0 and not self._is_cancelled():
                        replans -= 1
                        self._emit("replan", after_step=i, error=str(exc))
                        obs = self.d.observe()
                        if self.denied_apps:
                            from .perception import Observation
                            obs = Observation(
                                elements=[e for e in obs.elements
                                          if not any(
                                              d in (e.app or "").lower()
                                              for d in self.denied_apps)],
                                windows=obs.windows, texts=obs.texts)
                        self.plan_observation = obs
                        self._recovered = set()
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
