"""Planners: turn a goal + observation into a validated list of steps.

Two implementations share one step schema and one validator:

* ``OllamaPlanner`` — a local LLM (via Ollama) writes the plan as JSON.
  Output is never trusted: every plan passes the same allow-list
  validation before a single action runs.
* ``BuiltinPlanner`` — a deterministic, rule-based planner with a small
  documented command grammar plus task skills.  It needs no model at
  all, which keeps DeskDora fully testable offline and on modest
  hardware.  The active planner is recorded in every run log and shown
  in the UI — no silent substitution.

Step schema (all steps are dicts):
  {"action": "launch", "command": "thunar /home/user/Downloads"}
  {"action": "click", "element": 12}
  {"action": "click", "target": {"role": "push button",
                                 "name_contains": "Save", "nth": 0}}
  {"action": "type", "text": "hello", "target": {...optional...}}
  {"action": "key", "key": "ctrl+s", "context": "files"?}
  {"action": "scroll", "direction": "down", "target": {...optional...}}
  {"action": "wait", "seconds": 1.0}
  {"action": "extract", "pattern": "capital is (\\w+)", "name": "capital"}
  {"action": "read_clipboard", "name": "clipboard"}
  {"action": "build_sheet", "source": "clipboard", "save_as": "report"}
  {"action": "done", "answer": "..."} / {"action": "fail", "reason": "..."}

``target`` queries are resolved against a *fresh* observation at execution
time, so plans stay valid while windows move and dialogs appear.
``build_sheet`` is a documented macro: it expands, at execution time,
into primitive click/type/key sub-steps (each one logged) that enter a
clipboard table into a spreadsheet grid — the table contents cannot be
known at plan time, so the expansion has to happen in the executor.
"""
from __future__ import annotations

import json
import re
import urllib.request
import urllib.error

ALLOWED_ACTIONS = {
    "launch", "click", "double_click", "click_at", "type", "key", "scroll",
    "wait", "extract", "read_clipboard", "build_sheet", "done", "fail",
}
TARGET_KEYS = {"role", "name_contains", "exact_name", "label_contains",
               "app_contains", "nth"}


class PlanError(ValueError):
    pass


def _check_target(target, where):
    if not isinstance(target, dict):
        raise PlanError(f"{where}: target must be an object")
    unknown = set(target) - TARGET_KEYS
    if unknown:
        raise PlanError(f"{where}: unknown target keys {sorted(unknown)}")
    if "nth" in target and not isinstance(target["nth"], int):
        raise PlanError(f"{where}: target.nth must be an int")


def validate_plan(plan, element_count=None):
    """Raise PlanError unless the plan is a list of well-formed steps
    using only allow-listed actions.  Returns the plan unchanged."""
    if not isinstance(plan, list) or not plan:
        raise PlanError("plan must be a non-empty list of steps")
    if len(plan) > 1000:
        raise PlanError("plan too long (>1000 steps)")
    for i, step in enumerate(plan):
        where = f"step {i}"
        if not isinstance(step, dict):
            raise PlanError(f"{where}: step must be an object")
        action = step.get("action")
        if action not in ALLOWED_ACTIONS:
            raise PlanError(f"{where}: action {action!r} is not allow-listed")
        if action == "launch":
            if not isinstance(step.get("command"), str) or not step["command"].strip():
                raise PlanError(f"{where}: launch needs a non-empty command")
        elif action in ("click", "double_click"):
            if "element" in step:
                idx = step["element"]
                if not isinstance(idx, int) or idx < 0:
                    raise PlanError(f"{where}: element must be a non-negative int")
                if element_count is not None and idx >= element_count:
                    raise PlanError(f"{where}: element {idx} out of range")
            elif "target" in step:
                _check_target(step["target"], where)
            else:
                raise PlanError(f"{where}: click needs element or target")
        elif action == "click_at":
            for axis in ("x", "y"):
                v = step.get(axis)
                if not isinstance(v, int) or not (0 <= v <= 10000):
                    raise PlanError(f"{where}: click_at needs int {axis} in 0..10000")
        elif action == "type":
            if not isinstance(step.get("text"), str):
                raise PlanError(f"{where}: type needs text (string)")
            if "target" in step:
                _check_target(step["target"], where)
        elif action == "key":
            if not isinstance(step.get("key"), str) or not step["key"]:
                raise PlanError(f"{where}: key needs a key combo string")
        elif action == "scroll":
            if step.get("direction", "down") not in ("up", "down"):
                raise PlanError(f"{where}: scroll direction must be up|down")
            if "target" in step:
                _check_target(step["target"], where)
        elif action == "wait":
            secs = step.get("seconds", 1)
            if not isinstance(secs, (int, float)) or not (0 <= secs <= 30):
                raise PlanError(f"{where}: wait seconds must be 0..30")
        elif action == "extract":
            pattern = step.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise PlanError(f"{where}: extract needs a pattern")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise PlanError(f"{where}: bad regex: {exc}")
        elif action == "build_sheet":
            if step.get("source") != "clipboard":
                raise PlanError(f"{where}: build_sheet source must be 'clipboard'")
            if not isinstance(step.get("save_as"), str) or not step["save_as"]:
                raise PlanError(f"{where}: build_sheet needs save_as")
        elif action == "fail":
            if not isinstance(step.get("reason"), str):
                raise PlanError(f"{where}: fail needs a reason")
    return plan


# --------------------------------------------------------------------------
# Ollama planner
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the planner of DeskDora, a desktop agent.
Given a goal and a numbered list of on-screen elements, reply with ONLY a
JSON array of steps. Allowed actions: launch{command}, click{element},
click_at{x, y} (raw screen coordinates — use only when no element fits),
type{text}, key{key}, scroll{direction}, wait{seconds}, extract{pattern,
name}, done{answer}, fail{reason}. Click elements by their [number].
Keep plans short. End with done or fail."""


class OllamaPlanner:
    name = "ollama"

    def __init__(self, model="qwen2.5:1.5b", host="http://127.0.0.1:11434",
                 timeout=300):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def available(self):
        try:
            req = urllib.request.Request(self.host + "/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.load(resp)
            return any(m.get("name", "").split(":")[0] == self.model.split(":")[0]
                       for m in data.get("models", []))
        except Exception:
            return False

    def plan(self, goal, observation, history=None):
        user = f"Goal: {goal}\n\nOn-screen elements:\n{observation.summary_for_planner()}"
        if history:
            user += f"\n\nPrevious attempt notes: {history}"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        req = urllib.request.Request(
            self.host + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PlanError(f"Ollama unreachable or timed out: {exc}")
        content = data.get("message", {}).get("content", "")
        plan = _extract_json_plan(content)
        return validate_plan(plan, element_count=len(observation.elements))


def _extract_json_plan(content):
    content = content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            raise PlanError("planner did not return a JSON plan")
        try:
            parsed = json.loads(content[start:end + 1])
        except json.JSONDecodeError as exc:
            raise PlanError(f"planner returned invalid JSON: {exc}")
    if isinstance(parsed, dict) and "steps" in parsed:
        parsed = parsed["steps"]
    if not isinstance(parsed, list):
        raise PlanError("planner output is not a step list")
    return parsed


# --------------------------------------------------------------------------
# Built-in deterministic planner
# --------------------------------------------------------------------------

FILE_TYPE_GROUPS = {
    "Images": ["png", "jpg", "jpeg", "gif", "bmp", "svg", "webp"],
    "Documents": ["pdf", "txt", "md", "doc", "docx", "odt", "rtf"],
    "Audio": ["mp3", "wav", "ogg", "flac", "m4a"],
    "Video": ["mp4", "mkv", "webm", "mov", "avi"],
    "Archives": ["zip", "tar", "gz", "bz2", "xz", "7z", "rar"],
}


class BuiltinPlanner:
    """Rule-based planner with a small, documented grammar.

    Supported goal shapes (case-insensitive):
      * "Organise folder <path> by type"
      * "Set the appearance theme to <ThemeName>" / "Change theme to <X>"
      * "Enable/Disable event sounds in Appearance settings"
      * "Fill the form: name=..., email=..., city=... and submit"
      * "Open the article about <topic> and report its <thing>"
      * "Create a spreadsheet from <file.pdf> saved as <name>"
      * "Open <app>" / "Click <name>" / chains joined with " then "

    Parsing is *strict* (v1.1): a rule must consume the entire goal.
    A goal that is only partly understood raises PlanError ("I can't
    parse this goal…") and nothing is executed — the agent never
    partially carries out a goal and then reports success.
    """

    name = "builtin"

    def __init__(self, known_apps=None):
        self.apps = {
            "files": "thunar",
            "file manager": "thunar",
            "terminal": "xfce4-terminal",
            "editor": "mousepad",
            "text editor": "mousepad",
            "settings": "xfce4-settings-manager",
            "appearance": "xfce4-appearance-settings",
            "browser": "chromium",
            "spreadsheet": "gnumeric",
        }
        if known_apps:
            self.apps.update(known_apps)

    # -- skills ---------------------------------------------------------
    def _organise(self, path):
        import os
        if not os.path.isdir(path):
            raise PlanError(f"folder not found: {path}")
        present = {}
        for entry in os.listdir(path):
            full = os.path.join(path, entry)
            if os.path.isfile(full) and "." in entry:
                ext = entry.rsplit(".", 1)[1].lower()
                for folder, exts in FILE_TYPE_GROUPS.items():
                    if ext in exts:
                        present.setdefault(folder, set()).add(ext)
        if not present:
            raise PlanError(f"no organisable files found in {path}")
        steps = [{"action": "launch", "command": f"thunar {path}"},
                 {"action": "wait", "seconds": 2}]
        for folder in present:
            steps += [
                {"action": "key", "key": "ctrl+shift+n"},
                {"action": "wait", "seconds": 0.7},
                {"action": "type", "text": folder},
                {"action": "key", "key": "Return"},
                {"action": "wait", "seconds": 0.7},
            ]
        for folder, exts in present.items():
            for ext in sorted(exts):
                steps += [
                    {"action": "key", "key": "ctrl+s"},
                    {"action": "wait", "seconds": 0.7},
                    {"action": "type", "text": f"*.{ext}"},
                    {"action": "key", "key": "Return"},
                    {"action": "wait", "seconds": 0.5},
                    {"action": "key", "key": "ctrl+x"},
                    {"action": "key", "key": "ctrl+l"},
                    {"action": "type", "text": f"{path}/{folder}"},
                    {"action": "key", "key": "Return"},
                    {"action": "wait", "seconds": 0.5},
                    {"action": "key", "key": "ctrl+v"},
                    {"action": "wait", "seconds": 0.5},
                    {"action": "key", "key": "alt+Up"},
                    {"action": "wait", "seconds": 0.5},
                ]
        steps.append({"action": "done", "answer": f"Folder {path} organised by type"})
        return steps

    def _theme(self, theme):
        return [
            {"action": "launch", "command": self.apps["appearance"]},
            {"action": "wait", "seconds": 2},
            {"action": "click", "target": {"name_contains": theme}},
            {"action": "wait", "seconds": 1},
            {"action": "click", "target": {"role": "push button",
                                           "name_contains": "Close"}},
            {"action": "done", "answer": f"Theme set to {theme}"},
        ]

    def _sounds(self, checkbox="Enable event sounds"):
        return [
            {"action": "launch", "command": self.apps["appearance"]},
            {"action": "wait", "seconds": 2},
            {"action": "click", "target": {"role": "page tab",
                                           "name_contains": "Settings"}},
            {"action": "wait", "seconds": 1},
            {"action": "click", "target": {"role": "check box",
                                           "name_contains": checkbox}},
            {"action": "wait", "seconds": 0.5},
            {"action": "click", "target": {"role": "push button",
                                           "name_contains": "Close"}},
            {"action": "done", "answer": f"{checkbox} toggled"},
        ]

    def _form(self, pairs, submit):
        steps = [{"action": "launch", "command": self.apps["form"]},
                 {"action": "wait", "seconds": 2}]
        for field_name, value in pairs:
            steps.append({"action": "type", "text": value,
                          "target": {"label_contains": field_name}})
            steps.append({"action": "wait", "seconds": 0.3})
        if submit:
            steps.append({"action": "click", "target": {
                "role": "push button", "name_contains": "Submit"}})
            steps.append({"action": "wait", "seconds": 1})
            answer = "Form submitted"
        else:
            # Never claim a submission that was not requested/performed.
            answer = "Form filled (not submitted)"
        steps.append({"action": "done", "answer": answer})
        return steps

    def _parse_form_spec(self, spec):
        """Parse 'name=Ada, city=Pune' into (pairs, submit).

        Strict on purpose (the v1.1 parse fix): every character of the
        spec must be consumed by a field=value pair, a separator, or
        the trailing submit marker.  Anything left over — a second
        instruction glued onto the goal, an 'and'-separated field, a
        truncated value — refuses the whole goal instead of silently
        filling part of the form and reporting success.
        """
        if re.search(r"\s+then\s+", spec, flags=re.I):
            raise PlanError(
                "I can't parse this goal: a form specification cannot "
                "contain a 'then' chain — run the steps as separate "
                "goals. Nothing was executed.")
        submit = False
        m = re.search(r"(?:\s+and)?\s+submit\s*$", spec, flags=re.I)
        if m:
            submit = True
            spec = spec[:m.start()]
        pairs = []
        spans = []
        for match in re.finditer(r"(\w+)\s*=\s*([^,]+)", spec):
            field_name, value = match.group(1), match.group(2).strip()
            if not value:
                raise PlanError(
                    f"I can't parse this goal: field {field_name!r} has "
                    "no value. Nothing was executed.")
            if re.search(r"\w+\s*=", value):
                raise PlanError(
                    f"I can't parse this goal: the value for "
                    f"{field_name!r} contains another 'field=' — fields "
                    "must be separated by commas. Nothing was executed.")
            if re.search(r"(?i)\b(?:and|then)\s+(?:delete|remove|erase|"
                         r"format|send|submit|open|click|press|type|"
                         r"launch|install|uninstall|empty|move|copy|"
                         r"run)\b", value):
                raise PlanError(
                    f"I can't parse this goal: the value for "
                    f"{field_name!r} appears to contain a second "
                    "instruction. Nothing was executed.")
            pairs.append((field_name, value))
            spans.append(match.span())
        if not pairs:
            raise PlanError(
                "I can't parse this goal: a form goal needs "
                "field=value pairs. Nothing was executed.")
        residue = []
        pos = 0
        for start, end in spans:
            residue.append(spec[pos:start])
            pos = end
        residue.append(spec[pos:])
        leftover = "".join(residue).strip(" \t,;")
        if leftover:
            raise PlanError(
                f"I can't parse this goal: {leftover!r} is not a "
                "field=value pair. Nothing was executed.")
        return pairs, submit

    def _article(self, topic, thing):
        patterns = {
            "capital": r"capital of [A-Za-z ]+? is ([A-Za-z ]+?)[,\.\n]",
            "river": r"river is (?:the )?([A-Za-z ]+?)[,\.\n]",
        }
        pattern = patterns.get(thing.lower(),
                               rf"{re.escape(thing)}[:\s]+([A-Za-z0-9 ,\-]+?)[\.\n]")
        return [
            {"action": "launch",
             "command": f"{self.apps['browser']} {self.apps['site_url']}"},
            {"action": "wait", "seconds": 5},
            {"action": "click", "target": {"role": "link",
                                           "name_contains": topic}},
            {"action": "wait", "seconds": 2},
            {"action": "extract", "pattern": pattern, "name": thing},
            {"action": "done"},
        ]

    def _sheet_from_pdf(self, pdf_path, save_as):
        return [
            {"action": "launch",
             "command": f"{self.apps['browser']} file://{pdf_path}"},
            {"action": "wait", "seconds": 9},
            {"action": "click_at", "x": 640, "y": 450},
            {"action": "wait", "seconds": 1},
            {"action": "key", "key": "ctrl+a"},
            {"action": "key", "key": "ctrl+c"},
            {"action": "read_clipboard", "name": "table"},
            {"action": "launch", "command": self.apps["spreadsheet"]},
            {"action": "wait", "seconds": 2},
            {"action": "build_sheet", "source": "clipboard",
             "save_as": save_as},
            {"action": "done", "answer": f"Spreadsheet saved as {save_as}"},
        ]

    # -- dispatch ---------------------------------------------------------
    def plan(self, goal, observation, history=None):
        """Plan a goal, or refuse it.

        v1.1 parse fix: every grammar rule must consume the *entire*
        goal (full-string matches, and a residue check for the form
        grammar).  v1.0 matched prefixes — a truncated or compound
        goal could be partially executed and then reported as success
        (e.g. extra instructions glued after a form spec were silently
        dropped).  A goal that is not fully understood now raises
        PlanError("I can't parse this goal…") and nothing executes.
        """
        g = goal.strip()
        cleaned = _clean_goal(g)
        low = cleaned.lower()
        m = re.fullmatch(r"organi[sz]e (?:the )?folder (\S+)(?: by type)?",
                         cleaned, re.I)
        if m:
            return validate_plan(self._organise(m.group(1)))
        m = re.fullmatch(
            r"(?:enable|disable|turn on|turn off) (?:the )?"
            r"(event sounds|input feedback sounds)"
            r"(?: in (?:the )?appearance(?: settings)?)?", low)
        if m:
            checkbox = ("Enable event sounds" if m.group(1) == "event sounds"
                        else "Enable input feedback sounds")
            return validate_plan(self._sounds(checkbox))
        if " then " not in low:
            m = re.fullmatch(
                r"(?:set|change)(?: the)?(?: appearance)? theme to "
                r"([A-Za-z0-9 _\-]+)", cleaned, re.I)
            if m:
                theme = m.group(1).strip()
                if " and " in theme.lower():
                    raise PlanError(
                        f"I can't parse this goal: the theme name "
                        f"{theme!r} looks like it contains a second "
                        "instruction. Nothing was executed.")
                return validate_plan(self._theme(theme))
        m = re.fullmatch(r"fill (?:out )?(?:the )?form[:\s]+(.+)", cleaned,
                         re.I)
        if m:
            pairs, submit = self._parse_form_spec(m.group(1))
            return validate_plan(self._form(pairs, submit))
        m = re.fullmatch(
            r"open the article about ([\w ]+?) and report (?:its|the) (\w+)",
            cleaned, re.I)
        if m:
            return validate_plan(self._article(m.group(1).strip(),
                                               m.group(2)))
        m = re.fullmatch(
            r"(?:create|build|make) a spreadsheet from (\S+\.pdf)"
            r"(?: saved as (\S+))?", cleaned, re.I)
        if m:
            save_as = m.group(2) or "spreadsheet.gnumeric"
            return validate_plan(self._sheet_from_pdf(m.group(1), save_as))
        if " then " in low:
            steps = []
            for part in re.split(r"\s+then\s+", g, flags=re.I):
                sub = self.plan(part, observation)
                steps.extend(sub[:-1] if sub and sub[-1].get("action") == "done" else sub)
            steps.append({"action": "done"})
            return validate_plan(steps)
        m = re.fullmatch(r"open (?:the )?([a-z0-9 _\-]+)", low)
        if m:
            phrase = m.group(1).strip()
            words = phrase.split()
            # An app name is a noun phrase of at most three words.
            # Anything longer — or containing a conjunction — is a
            # sentence this rule would otherwise swallow whole and
            # "open" as if it were an application (a v1.0 trap: the
            # article grammar's leftovers landed here).
            if len(words) <= 3 and "and" not in words:
                app = self.apps.get(phrase, phrase)
                return validate_plan([
                    {"action": "launch", "command": app},
                    {"action": "wait", "seconds": 2},
                    {"action": "done", "answer": f"Opened {phrase}"}])
        m = re.fullmatch(r"click (?:on )?(.+)", g, re.I)
        if m:
            return validate_plan([
                {"action": "click", "target": {"name_contains": m.group(1).strip()}},
                {"action": "done"}])
        m = re.fullmatch(r"press ([a-z0-9_+\-]+)", g, re.I)
        if m:
            return validate_plan([
                {"action": "key", "key": m.group(1)},
                {"action": "done"}])
        m = re.match(r"type (.+)$", g, re.I)
        if m:
            return validate_plan([
                {"action": "type", "text": m.group(1)},
                {"action": "done"}])
        raise PlanError(
            f"I can't parse this goal with the built-in planner: "
            f"{goal.strip()!r}. Nothing was executed. The built-in "
            "grammar covers organising a folder, appearance settings, "
            "form fills, article facts, PDF-to-spreadsheet, and "
            "open/click/type/press steps joined with 'then'. Try the "
            "Ollama planner for free-form goals.")


def _clean_goal(goal):
    """Strip trailing punctuation and politeness fillers so grammar
    rules can anchor on the full string ('…by type.' or '…please'
    still parse; a second instruction after them does not)."""
    g = goal.strip()
    g = re.sub(r"(?i)[\s.!]+$", "", g)
    g = re.sub(r"(?i)\s+(please|thanks|thank you)$", "", g)
    return g.strip()


def get_planner(mode="auto", model="qwen2.5:1.5b", known_apps=None):
    """Resolve a planner by mode.  'auto' prefers Ollama when a server
    with the model is actually reachable, else the built-in planner.
    Returns (planner, mode_label)."""
    if mode == "builtin":
        return BuiltinPlanner(known_apps), "builtin"
    if mode == "ollama":
        return OllamaPlanner(model=model), f"ollama:{model}"
    ollama = OllamaPlanner(model=model)
    if ollama.available():
        return ollama, f"ollama:{model}"
    return BuiltinPlanner(known_apps), "builtin"
