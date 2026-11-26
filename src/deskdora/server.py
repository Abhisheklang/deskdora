"""DeskDora web service: a local FastAPI app (127.0.0.1 only) that
drives the agent and serves the web UI.

One run at a time.  When a run reaches a destructive step, the agent
thread blocks inside the confirmation policy until the user approves or
denies in the UI (or a timeout denies it) — the mandatory-confirmation
safety layer, wired end to end.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (connfail, diagnostics, estimates, filejournal, notify,
               sounds, validation, voice)
from .templates import TemplateStore, builtin_templates
from .agent import Agent, describe_step
from .apikey import ApiKeyStore, presented_key
from .ask import answer_question
from .audit import RunLog, list_runs, read_log
from .autonomy import (DEFAULT_POLICY, AutonomyPolicy, PolicyError,
                      validate_policy)
from .memory import MemoryStore, learn_from_log
from .backup import (BackupError, build_backup, parse_backup,
                     preview_backup)
from .gguf import (GgufError, check_model_file,
                   model_name_from_filename, validate_model_name)
from .hotkey import parse_combo
from .planners import (PROVIDER_REGISTRY, OllamaPlanner, PlanError,
                       PullAggregator, get_planner,
                       make_provider_planner,
                       validate_custom_provider)
from .proactive import ProposalStore, ProactiveWatcher
from .queue import BusyError, TaskQueue
from .report import render_report
from .routines import (RoutineStore, describe_routine,
                       record_parameterized, render_goal)
from .safety import AllowList, ConfirmationGate, KillSwitch, PauseGate
from .scheduler import Scheduler
from .tasks import base_known_apps

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DATA_DIR = os.environ.get(
    "DESKDORA_DATA_DIR", os.path.expanduser("~/.local/share/deskdora"))
RUNS_DIR = os.path.join(DATA_DIR, "runs")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")

DEFAULT_CONFIG = {
    "planner": "auto",          # auto | builtin | ollama
    "model": "qwen2.5:1.5b",
    "allow_apps": [],           # empty = allow all
    "sandbox_mode": True,       # informational flag shown in the UI
    "driver": "auto",           # auto | linux-x11 | linux-wayland | ...
    "voice_updates": False,     # spoken progress updates (offline TTS)
    # Per-category autonomy (v3.0): files/apps/web/messages/money ->
    # free | confirm | never.  Money can never be "free" (hard floor,
    # enforced by autonomy.validate_policy).
    "autonomy": dict(DEFAULT_POLICY),
    "watch_folders": [],        # folders the proactive watcher scans
    "companion_pin": "",        # PIN for non-loopback mutating calls
    # Companion screen share (v3.2): view-only live screen for
    # paired companions.  OFF by default; the app shows an indicator
    # while anyone is watching.  Never shown inside the app itself.
    "screen_share": False,
    # Edge light (v3.2): click-through neon border while the agent
    # works.  off | single | double | flow.
    "edge_light": "single",
    # Features panel (v3.2): master switches.  Off stops the
    # machinery, not just the UI; each switch's enforcement point
    # is named in REPORT-V3.2.
    "feature_overlay": True,     # floating control window (window.py)
    "feature_companion": True,   # companion page + companion endpoints
    "feature_mcp": True,         # MCP-started runs refused when off
    "feature_api": True,         # API-key authentication refused when off
    "feature_animations": True,  # UI motion (complements reduced-motion)
    "proposals_enabled": True,   # proactive watcher scanning
    # v3.3 features
    "sounds": json.loads(json.dumps(sounds.DEFAULT_SOUNDS)),
    # Global hotkey (v3.7.3): bound by the window process on X11
    # while the app runs; see hotkey.py for the platform truth.
    "hotkey": {"enabled": True, "combo": "Ctrl+Alt+D"},
    "dry_run_default": False,    # plan approval before runs, by default
    "app_deny_list": [],         # per-app permissions: denied app names
    "feature_app_permissions": True,  # master switch for the deny list
    "self_recovery": True,       # one alternative attempt per failed step
    "feature_ask_screen": True,  # ask-about-my-screen Q&A
    "feature_undo": True,        # undo for file actions
    # v3.4 features
    "feature_notifications": True,  # desktop notifications master
    "notifications": json.loads(json.dumps(
        notify.DEFAULT_NOTIFICATIONS)),  # per-event toggles
    "feature_estimates": True,   # time-estimate display
    # Appearance (v3.5): "light" | "dark".  Default LIGHT (user
    # order, 2026-11-19: light reads better in screen recordings).
    # Existing installs have no theme key, so they pick up this
    # default until the user switches in Settings.
    "theme": "light",
    # First-run setup (v3.6): the setup wizard is required until
    # the user finishes or skips it once; setup_complete is that
    # marker.  user_name is the optional name from wizard step 1 —
    # used for the wizard's finish greeting and editable later in
    # Settings.  Nothing else reads it.
    "setup_complete": False,
    "user_name": "",
}

THEMES = ("light", "dark")


def theme_of(cfg):
    """The configured theme, normalised: anything that is not
    exactly "dark" is "light" (the default), so a missing or
    hand-edited config value can never strand the UI themeless."""
    return "dark" if (cfg or {}).get("theme") == "dark" else "light"


def setup_required(cfg):
    """True when the first-run setup wizard (v3.6) must run: the
    config carries no completed-setup marker.  Every pre-v3.6
    install has no marker, so it sees the wizard exactly once —
    finishing or skipping sets setup_complete and it never
    auto-appears again (Settings can re-open it on demand)."""
    return not (cfg or {}).get("setup_complete")


def planner_display(mode_label):
    """Truthful human name for a planner mode label, e.g.
    'anthropic:claude-sonnet-4-5' -> 'Anthropic · claude-sonnet-4-5',
    'ollama:qwen2.5:1.5b' -> 'Ollama · qwen2.5:1.5b',
    'builtin' -> 'Built-in'."""
    if not mode_label:
        return ""
    if mode_label == "builtin":
        return "Built-in"
    head, sep, model = mode_label.partition(":")
    preset = PROVIDER_REGISTRY.get(head)
    if preset:
        name = preset["label"].replace(" (local)", "")
        return f"{name} · {model}" if model else name
    try:
        for custom in load_config().get("custom_providers") or []:
            if custom.get("id") == head:
                name = custom.get("name", head)
                return f"{name} · {model}" if model else name
    except Exception:
        pass
    return f"{head} · {model}" if sep else mode_label


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    """Persist config.json atomically (v3.4.4).

    The pre-3.4.4 code truncated config.json in place: a failed
    write (crash, full disk, write block) left an empty file, and
    load_config's silent fallback then reset the user to defaults
    — one bad write blanked their settings.  Write to a temp file
    in the SAME directory as CONFIG_PATH, fsync, then os.replace()
    onto the real path: on POSIX the replace is atomic, so
    config.json always holds a complete old or new document,
    never a partial one.  A temp file orphaned by a crash is
    harmless — load_config only ever reads CONFIG_PATH.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    config_dir = os.path.dirname(CONFIG_PATH) or "."
    os.makedirs(config_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=config_dir,
                                    prefix="config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# DeskDora's own API key (v3.1) — hash-only store next to the config.
API_KEYS = ApiKeyStore(os.path.join(DATA_DIR, "api-key.json"))


def public_config(cfg):
    """Config as the API may show it: provider API keys are masked to
    a last-4 + has_key flag.  The full keys never leave the config
    file — not in API responses, logs, or run records.  The companion
    PIN is masked the same way (has_pin flag only): the API cannot
    tell the local Settings page from a stranger on the LAN, so the
    PIN value itself never leaves in a response."""
    out = json.loads(json.dumps(cfg))
    pin = out.pop("companion_pin", "") or ""
    out["has_pin"] = bool(pin)
    for stored in (out.get("providers") or {}).values():
        key = stored.pop("api_key", "") or ""
        stored["has_key"] = bool(key)
        stored["key_last4"] = key[-4:] if key else ""
    for custom in out.get("custom_providers") or []:
        key = custom.pop("api_key", "") or ""
        custom["has_key"] = bool(key)
        custom["key_last4"] = key[-4:] if key else ""
    return out


def provider_state(cfg):
    """Per-preset effective settings (model / base URL / key state),
    keys masked."""
    stored = cfg.get("providers") or {}
    out = {}
    for pid, spec in PROVIDER_REGISTRY.items():
        s = stored.get(pid) or {}
        key = s.get("api_key") or ""
        out[pid] = {
            "model": s.get("model") or spec["default_model"],
            "base_url": s.get("base_url") or spec["base_url"],
            "has_key": bool(key),
            "key_last4": key[-4:] if key else "",
        }
    return out


class _ManagerRunner:
    """Adapter between TaskQueue and RunManager: the queue speaks in
    plain callables, the manager in HTTP-flavoured methods."""

    def __init__(self, manager):
        self._m = manager

    def busy(self):
        return bool(self._m.state["running"])

    def start(self, goal, planner):
        try:
            self._m.start_run(goal, planner or None, source="queue")
        except HTTPException as exc:
            raise BusyError(str(exc.detail)) from exc

    def join(self):
        # Poll the manager's running flag, not just the worker
        # thread: after a watchdog force-clear (v3.4.1) the worker
        # may still be stuck inside a step as a fenced zombie, and
        # joining it directly would hang the queue forever.  The
        # queue item is marked from the run's recorded result, so
        # a wedged run still lands as "killed" in the queue (and
        # in queue.json) instead of stranding both forever.
        thread = self._m.thread
        while thread is not None and thread.is_alive():
            thread.join(timeout=0.2)
            if not self._m.state.get("running"):
                break
            if self._m.thread is not thread:
                break  # a newer run has taken over the manager
        return self._m.state.get("result")

    def kill_tripped(self):
        return self._m.kill.is_tripped()


def grab_screen_frame(max_width=960):
    """One screen-share frame as (bytes, content_type).

    Captures through the same scrot path the agent uses, then
    downscales and JPEG-encodes with GdkPixbuf (part of the GTK
    stack the app already ships).  Falls back to the raw PNG when
    GdkPixbuf is not importable in the service process."""
    from . import actions
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        actions.screenshot(path)
        try:
            import gi
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            pix = GdkPixbuf.Pixbuf.new_from_file(path)
            if pix.get_width() > max_width:
                scale = max_width / pix.get_width()
                pix = pix.scale_simple(
                    max_width, max(1, int(pix.get_height() * scale)),
                    GdkPixbuf.InterpType.BILINEAR)
            ok, buf = pix.save_to_bufferv("jpeg", ["quality"], ["62"])
            if ok:
                return bytes(buf), "image/jpeg"
        except Exception:
            pass
        with open(path, "rb") as fh:
            return fh.read(), "image/png"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class RunManager:
    """Owns the (single) active run, the kill switch, and any pending
    confirmation request."""

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.agent = None
        self.state = {
            "running": False, "goal": "", "planner_mode": "",
            "last_event": None, "run_dir": None, "result": None,
            # Connection-failure pop-up payload (v3.3) + the dry-run
            # plan awaiting approval.  Both ride /api/status, which
            # is how the companion mirrors them.
            "connection_error": None, "pending_plan": None,
            # Planner error-page payload (v3.7): a definitive setup
            # problem found by pre-run validation.  Rides /api/status
            # alongside connection_error; the UI routes on which of
            # the two is set — page for definitive, pop-up for
            # unreachable.
            "planner_error": None,
            # Completion payload (v3.7.2): set ONLY when a run
            # finishes successfully — goal, steps executed and
            # elapsed time as measured here, never guessed by the
            # client.  The Run view's "work done" card and the
            # floating window's DONE state render this; a failed or
            # killed run never sets it and keeps its own end state.
            # Cleared by the next run, by reset, and by the dismiss
            # endpoint.
            "completion": None,
            # Tone dispatch queue (v3.7.2): the server resolves
            # each sound event to its configured tone (sounds
            # module — master switch + per-event selection) and
            # queues it here; the app window plays what it finds
            # via WebAudio.  Playback moved into the window
            # because the old paplay path was silently dead
            # wherever paplay or an audio server was missing —
            # which included every environment the user ran in.
            "tone_events": [],
            "source": "ui",
        }
        self._tone_seq = 0
        self.kill = KillSwitch()
        self.pause_gate = PauseGate()
        # Kill-switch wedge fix (v3.4.1): the kill switch used to
        # be checked only between steps, so a step that never
        # returned wedged the run forever — running stayed True,
        # reset was refused, only a server restart recovered.
        # Now every run gets a generation number and a per-run
        # cancel event; tripping the kill starts a watchdog that
        # force-clears the run after a bounded grace period, and
        # the generation bump fences the stuck worker (its events,
        # its late result and its cleanup are all ignored, and
        # the agent checks the cancel event before every action
        # dispatch, so it can never act again).
        self.kill_grace_seconds = 5.0
        self._run_generation = 0
        self._run_cancel = None       # cancel Event of the active run
        self._kill_tripped_at = None
        self._watchdog_thread = None
        self._watchdog_generation = None
        self.progress = {"step_index": 0, "step_total": 0, "done": 0,
                         "current_action": ""}
        # Companion screen share (v3.2): live viewer count + the
        # frame source (indirected so tests can serve fake frames).
        self.screen_viewers = 0
        self.companion_sessions = {}   # cookie token -> expiry time
        self.frame_provider = grab_screen_frame
        self.queue = TaskQueue(
            _ManagerRunner(self),
            os.path.join(DATA_DIR, "queue.json"))
        # Queue fully drained: sound tone + ONE summary
        # notification for the whole queue (v3.3/v3.4).
        self.queue.on_drained = self._queue_drained
        self.templates = TemplateStore(
            os.path.join(DATA_DIR, "templates.json"))
        self.pending = None          # dict with an Event + verdict
        # Dry-run plan approval (v3.3): the pending plan + decision.
        self._plan_event = threading.Event()
        self._plan_decision = None   # {"approved": bool, "skip": set}
        self.routines = RoutineStore()
        self.memory = MemoryStore(os.path.join(DATA_DIR, "memory.json"))
        self.scheduler = Scheduler(
            self.routines, self._run_routine,
            os.path.join(DATA_DIR, "scheduler-events.jsonl"))
        self.proposals = ProposalStore(
            os.path.join(DATA_DIR, "proposals.json"))
        self.watcher = ProactiveWatcher(
            self.proposals, self._run_goal,
            policy=AutonomyPolicy(load_config()["autonomy"]))

    def _queue_drained(self):
        cfg = load_config()
        self.emit_tone("queue_finished", cfg)
        snap = self.queue.snapshot()
        finished = [it for it in snap["items"]
                    if it["status"] in ("success", "failed", "blocked",
                                        "killed", "interrupted")]
        if finished:
            ok = sum(1 for it in finished
                     if it["status"] == "success")
            notify.notify(
                "queue_finished", cfg,
                body=f"{len(finished)} tasks finished "
                     f"({ok} succeeded)")

    def _run_goal(self, goal):
        """Proactive/scheduler entry point: the normal run path."""
        try:
            self.start_run(goal, "builtin")
            return True, "run started"
        except HTTPException as exc:
            return False, str(exc.detail)

    # -- scheduler entry point: same path as pressing RUN NOW ---------
    def _run_routine(self, routine):
        try:
            self.routines.mark_run(routine["name"])
            self.start_run(render_goal(routine),
                           routine.get("planner_mode") or None,
                           routine_name=routine["name"])
            return True, "run started"
        except HTTPException as exc:
            return False, str(exc.detail)

    # -- tone dispatch (v3.7.2): resolve an event to its configured
    # tone and queue it for the window to play.  The sounds module
    # owns the gating (master switch, per-event selection); a
    # silent event queues nothing at all.  The queue is capped and
    # rides /api/status in the state payload.
    def emit_tone(self, event, cfg=None):
        tone = sounds.resolve_tone(
            cfg if cfg is not None else load_config(), event)
        if tone is None:
            return False
        with self.lock:
            self._tone_seq += 1
            entry = {"seq": self._tone_seq, "event": event,
                     "tone": tone, "at": time.time()}
            self.state["tone_events"] = (
                self.state["tone_events"] + [entry])[-8:]
        return True

    # -- confirmation policy: blocks the agent thread until the UI answers
    def ui_policy(self, step, description=""):
        cid = uuid.uuid4().hex[:8]
        event = threading.Event()
        record = {"id": cid, "step": step, "description": description,
                  "created_at": time.time(), "event": event,
                  "approved": None}
        with self.lock:
            self.pending = record
        self.emit_tone("confirmation_waiting", load_config())
        notify.notify("confirmation_waiting", load_config(),
                      body=(description or "DeskDora needs a "
                            "confirmation to continue.")[:140])
        if load_config().get("voice_updates"):
            voice.speak("DeskDora needs your confirmation to continue.")
        # 300s timeout -> deny (fail closed).
        event.wait(timeout=300)
        with self.lock:
            if self.pending is record:
                self.pending = None
        return bool(record["approved"])

    def answer_confirmation(self, cid, approved):
        with self.lock:
            record = self.pending
        if not record or record["id"] != cid:
            raise HTTPException(404, "no such pending confirmation")
        record["approved"] = approved
        record["event"].set()
        return {"ok": True}

    def start_run(self, goal, planner_mode=None, routine_name=None,
                  source="ui", dry_run=None):
        cfg = load_config()
        mode = planner_mode or cfg["planner"]
        with self.lock:
            if self.state["running"]:
                raise HTTPException(409, "a run is already in progress")
        # Planner validation (v3.7), BEFORE pre-flight: a
        # definitive setup problem — unknown provider or model,
        # model not installed, API key missing or rejected — is a
        # full error page, not a pop-up, and the run never starts.
        # Validation never changes the configured planner (no
        # silent fallback, ever).  What it cannot decide —
        # unreachable — returns None here and falls through to
        # the pop-up path below, unchanged.
        problem = validation.validate_planner(mode, cfg)
        if problem is not None:
            with self.lock:
                self.state.update({
                    "goal": goal, "source": source,
                    "connection_error": None,
                    "planner_error": {**problem, "goal": goal,
                                      "planner": mode}})
            return {"ok": False, "planner_error": problem}
        # Connection pre-flight (v3.3): an Ollama/cloud planner that
        # can't be reached fails HERE, in seconds, with a plain-
        # language payload for the pop-up — never as a cryptic log
        # line mid-task, and never via a silent planner switch.
        failure = connfail.preflight(mode, cfg)
        if failure is not None:
            with self.lock:
                self.state.update({
                    "goal": goal, "source": source,
                    "planner_error": None,
                    "connection_error": {**failure, "goal": goal,
                                         "planner": mode}})
            notify.notify("connection_failed", cfg,
                          body=failure["headline"])
            return {"ok": False, "connection_error": failure}
        with self.lock:
            self.state.update({"running": True, "goal": goal,
                               "result": None, "source": source,
                               "connection_error": None,
                               "planner_error": None,
                               "completion": None,
                               "pending_plan": None})
            self.progress = {"step_index": 0, "step_total": 0,
                             "done": 0, "current_action": "Planning",
                             "started_at": time.time()}
            self._run_generation += 1
            generation = self._run_generation
            cancel_event = threading.Event()
            self._run_cancel = cancel_event
            self._kill_tripped_at = None
        self.kill.reset()
        self.pause_gate.clear()
        # Sound tones: dispatched to the window's tone queue,
        # never blocking (see emit_tone).
        if source == "ui":
            self.emit_tone("command_sent", cfg)
        elif source == "companion":
            self.emit_tone("command_received", cfg)
        if dry_run is None:
            dry_run = bool(cfg.get("dry_run_default"))

        def _on_event(kind, **fields):
            if generation != self._run_generation:
                return  # fenced zombie from a force-cleared run
            with self.lock:
                self.state["last_event"] = {"ts": time.time(),
                                            "kind": kind, **fields}
                if kind == "plan":
                    steps = fields.get("steps") or []
                    # The trailing "done" marker is not a step the
                    # user watches execute — the app instrument
                    # excludes it, and so does this counter.
                    total = len(steps) - (
                        1 if steps and steps[-1].get("action") == "done"
                        else 0)
                    self.progress.update(
                        {"step_total": total,
                         "done": 0, "step_index": 0,
                         "current_action": "Plan ready"})
                elif kind == "step_start":
                    step = fields.get("step") or {}
                    self.progress["step_index"] = fields.get("index", 0)
                    self.progress["current_action"] = (
                        str(step.get("action", "")) + " " +
                        str(step.get("target") or step.get("key")
                            or "")).strip()
                elif kind == "step_result":
                    self.progress["done"] = max(
                        self.progress["done"],
                        int(fields.get("index", 0)) + 1)
                elif kind == "run_paused":
                    self.progress["current_action"] = "Paused"

        def _work():
            # EVERYTHING runs inside try/finally (v3.4.1): planner
            # init, RunLog creation and driver construction used to
            # sit outside any cleanup, so a setup failure (e.g. a
            # RunLog PermissionError from a failing audit disk)
            # killed this thread and stranded running=True forever.
            # The finally below owns the state — and only for THIS
            # run's generation: a force-cleared zombie finishing
            # late must not clear a newer run's state.
            result = {"status": "failed",
                      "reason": "run did not complete"}
            log = None
            try:
                planner, mode_label = get_planner(
                    mode, model=cfg["model"], known_apps=base_known_apps(),
                    memory=self.memory, providers=cfg.get("providers"),
                    customs=cfg.get("custom_providers"))
                gate = ConfirmationGate(self.ui_policy)
                log = RunLog(RUNS_DIR, goal, mode_label, source=source)
                from .drivers import get_driver
                driver = get_driver(
                    None if cfg.get("driver", "auto") == "auto"
                    else cfg["driver"])
                try:
                    policy = AutonomyPolicy(cfg.get("autonomy"))
                except PolicyError:
                    # A hand-edited config must fail safe, never open.
                    policy = AutonomyPolicy()
                denied = (cfg.get("app_deny_list") or None) \
                    if cfg.get("feature_app_permissions", True) else None
                agent = Agent(planner, mode_label, gate, self.kill,
                              AllowList(cfg["allow_apps"]), log,
                              on_event=_on_event, driver=driver,
                              autonomy=policy, pause_gate=self.pause_gate,
                              denied_apps=denied,
                              recovery_enabled=bool(
                                  cfg.get("self_recovery", True)),
                              cancel_event=cancel_event)
                with self.lock:
                    if generation == self._run_generation:
                        self.agent = agent
                        self.state["planner_mode"] = mode_label
                        self.state["run_dir"] = log.dir
                try:
                    if dry_run:
                        result = self._dry_run(agent, goal, log)
                    else:
                        result = agent.run(goal)
                except Exception as exc:  # agent.run logs; be safe
                    result = {"status": "failed", "reason": str(exc)}
                # A planning/connection failure surfaces as the
                # pop-up payload too (mid-run failures land here
                # after the engine's one bounded replan retry).
                # The whole side-effect block (pop-up, tones,
                # notifications, memory learning, voice) runs only
                # while this run is still the current generation:
                # a force-cleared zombie finishing late stays
                # silent and changes nothing.
                if generation == self._run_generation:
                    if result.get("status") == "failed" and \
                            connfail.planner_kind(mode, cfg) != "builtin":
                        payload = connfail.classify(
                            result.get("reason", ""), mode, cfg)
                        if payload["cause"] != "unknown":
                            with self.lock:
                                self.state["connection_error"] = {
                                    **payload, "goal": goal,
                                    "planner": mode}
                            notify.notify("connection_failed", cfg,
                                          body=payload["headline"])
                    if result.get("status") == "success":
                        self.emit_tone("run_completed", cfg)
                    elif result.get("status") == "failed":
                        self.emit_tone("run_failed", cfg)
                    if result.get("status") == "success":
                        notify.notify(
                            "run_completed", cfg,
                            body=(goal[:80] + " — "
                                  + (result.get("answer")
                                     or ""))[:140])
                    elif result.get("status") == "failed":
                        notify.notify(
                            "run_failed", cfg,
                            body=(goal[:80] + " — "
                                  + (result.get("reason")
                                     or ""))[:140])
                    # Resident memory learns from finished runs
                    # (success only, inside learn_from_log) and
                    # habits count routine runs.
                    try:
                        learn_from_log(self.memory, log.dir,
                                       result.get("status", ""))
                        if routine_name:
                            self.memory.note_routine(routine_name)
                            self.memory.save()
                    except Exception:
                        pass
                    if cfg.get("voice_updates"):
                        summary = result.get("answer") \
                            or result.get("reason") or ""
                        voice.speak(
                            f"Run finished: {result.get('status', '')}. "
                            f"{summary}"[:200])
            except Exception as exc:
                # Run SETUP failed (planner init, RunLog creation,
                # audit write, driver construction): the run never
                # started, but running=True was already published —
                # the finally below is what keeps that from
                # stranding the manager in "a run is in progress"
                # forever (the RunLog PermissionError wedge).
                result = {"status": "failed",
                          "reason": f"run setup failed: "
                                    f"{type(exc).__name__}: {exc}"}
            finally:
                with self.lock:
                    if generation == self._run_generation:
                        self.state["running"] = False
                        self.state["result"] = result
                        if result.get("status") == "success":
                            # The completion moment (v3.7.2): a
                            # successful finish is its own state,
                            # with the numbers as measured here —
                            # the UIs render this payload instead of
                            # reconstructing an ending from client-
                            # side step tracking.  Failed and killed
                            # runs never reach this branch.
                            started = self.progress.get("started_at")
                            finished_at = time.time()
                            self.state["completion"] = {
                                "goal": goal,
                                "steps": int(result.get(
                                    "steps_executed",
                                    self.progress.get("done", 0))
                                    or 0),
                                "elapsed_seconds": (
                                    round(finished_at - started, 1)
                                    if started else None),
                                "finished_at": finished_at,
                                "answer": result.get("answer") or "",
                            }
                        if self._run_cancel is cancel_event:
                            self._run_cancel = None

        self.thread = threading.Thread(target=_work, daemon=True)
        self.thread.start()
        return {"ok": True, "goal": goal}

    def _dry_run(self, agent, goal, log):
        """Dry-run plan approval (v3.3): build the plan, park it in
        state for the UI, and wait for the user's decision.  The
        ENGINE executes with the user's skip set — unticked steps
        are never performed, and the audit log records them as
        skipped by the user."""
        try:
            plan = agent.build_plan(goal)
        except PlanError as exc:
            log.finish("failed", reason=f"planning failed: {exc}",
                       steps_executed=0)
            return {"status": "failed",
                    "reason": f"planning failed: {exc}",
                    "steps_executed": 0}
        self._plan_decision = None
        self._plan_event.clear()
        with self.lock:
            self.state["pending_plan"] = {
                "goal": goal, "planner": agent.planner_mode,
                "steps": [describe_step(s) for s in plan],
                "count": len(plan)}
        deadline = time.time() + 600  # fail closed, like confirmations
        while not self._plan_event.wait(timeout=0.25):
            if self.kill.is_tripped() or time.time() > deadline:
                break
        decision = self._plan_decision or {"approved": False,
                                           "skip": set()}
        with self.lock:
            self.state["pending_plan"] = None
        if self.kill.is_tripped() or not decision["approved"]:
            log.finish("killed", reason="plan not approved",
                       steps_executed=0)
            return {"status": "killed",
                    "reason": "plan not approved by user",
                    "steps_executed": 0}
        return agent.execute_plan(goal, plan,
                                  skip=decision["skip"])

    def approve_plan(self, approved, skip=()):
        with self.lock:
            pending = self.state.get("pending_plan")
        if not pending:
            raise HTTPException(404, "no plan is waiting for approval")
        self._plan_decision = {"approved": bool(approved),
                               "skip": {int(i) for i in skip}}
        self._plan_event.set()
        return {"ok": True}

    def trip_kill(self):
        self.kill.trip()
        # The kill switch also fences the queue: nothing new starts
        # until the user explicitly resumes it.
        self.queue.halt()
        with self.lock:
            self._kill_tripped_at = time.time()
            running = self.state["running"]
            generation = self._run_generation
        if running:
            # A healthy worker notices the kill between steps and
            # finishes on its own; the watchdog exists for the one
            # that cannot — a step that never returns (v3.4.1).
            self._start_watchdog(generation)
        return {"ok": True, "tripped": True}

    # -- kill watchdog (v3.4.1) --------------------------------------
    def _start_watchdog(self, generation):
        """One watchdog per run generation.  It force-clears the
        run if the worker is still going kill_grace_seconds after
        the kill was tripped."""
        with self.lock:
            current = self._watchdog_thread
            if current is not None and current.is_alive() \
                    and self._watchdog_generation == generation:
                return
            thread = threading.Thread(
                target=self._watchdog_loop, args=(generation,),
                daemon=True, name="deskdora-kill-watchdog")
            self._watchdog_thread = thread
            self._watchdog_generation = generation
        thread.start()

    def _watchdog_loop(self, generation):
        deadline = time.time() + self.kill_grace_seconds
        while time.time() < deadline:
            with self.lock:
                if not self.state["running"] \
                        or self._run_generation != generation \
                        or not self.kill.is_tripped():
                    return  # finished in time, superseded, or reset
            time.sleep(0.05)
        self._force_clear(
            generation,
            "kill switch tripped but the current step did not "
            f"return within {self.kill_grace_seconds:g}s — the "
            "run was force-cleared by the kill watchdog")

    def _force_clear(self, generation, reason):
        """End a wedged run without its worker's cooperation.

        The state is cleared HERE, not by the worker: running
        becomes False, the result is recorded as killed, and the
        generation bump fences the stuck worker — its per-run
        cancel event is set permanently (the agent checks it
        before every action dispatch, so when the hung call
        eventually returns, the worker unwinds without acting),
        its events and late cleanup are ignored, and any pending
        confirmation or plan approval is denied so a worker
        blocked there wakes up and exits too.  Returns True when
        this call actually cleared the run."""
        with self.lock:
            if not self.state["running"] \
                    or self._run_generation != generation:
                return False
            cancel = self._run_cancel
            agent = self.agent
            result = {"status": "killed", "reason": reason,
                      "answers": {},
                      "steps_executed": self.progress.get("done", 0),
                      "force_cleared": True}
            self.state["running"] = False
            self.state["result"] = result
            self.progress["current_action"] = \
                "Killed — step did not return"
            self._run_generation += 1
            self._run_cancel = None
            pending = self.pending
            self.pending = None
        if cancel is not None:
            cancel.set()
        if pending is not None:
            pending["approved"] = False
            pending["event"].set()
        # Wake a dry-run plan wait so that worker can unwind too.
        self._plan_decision = {"approved": False, "skip": set()}
        self._plan_event.set()
        if agent is not None:
            try:
                agent.log.event("force_cleared", reason=reason)
            except Exception:
                pass  # the audit line is a bonus, never a blocker
        return True

    # -- companion / overlay controls (v3.2) --------------------------
    def pause_all(self):
        """Freeze: the active run pauses at its next step boundary
        and the queue holds its remaining items."""
        self.pause_gate.request()
        self.queue.pause()
        return {"ok": True, "paused": True}

    def resume_all(self):
        """Resume a frozen run and/or the queue.  After a kill this
        continues the QUEUE with its next item — a killed run is
        never resumed mid-action; it restarts cleanly or not at all."""
        self.pause_gate.clear()
        self.queue.resume()
        return {"ok": True, "paused": False}

    def reset_state(self):
        """Clear a finished/stopped run back to idle.

        A live, unkilled run still refuses (409) — reset is not a
        second stop button.  But a KILLED run can never refuse
        permanently (v3.4.1): if the worker has not finished, reset
        gives it the rest of the kill grace period to die on its
        own, then force-clears it and proceeds."""
        with self.lock:
            running = self.state["running"]
            tripped = self.kill.is_tripped()
            generation = self._run_generation
        if running and not tripped:
            raise HTTPException(409, "a run is in progress")
        if running and tripped:
            deadline = time.time() + self.kill_grace_seconds
            while time.time() < deadline:
                with self.lock:
                    if not self.state["running"]:
                        break
                time.sleep(0.05)
            with self.lock:
                wedged = self.state["running"] \
                    and self._run_generation == generation
            if wedged:
                self._force_clear(
                    generation,
                    "kill switch tripped; the run was force-cleared "
                    "on reset after the grace period")
            with self.lock:
                if self.state["running"]:
                    # A newer run took over while we waited — its
                    # state is not ours to clear.
                    raise HTTPException(409, "a run is in progress")
        with self.lock:
            if self.state["running"]:
                raise HTTPException(409, "a run is in progress")
            self.state.update({"goal": "", "planner_mode": "",
                               "run_dir": None, "result": None,
                               "last_event": None, "source": "ui",
                               "connection_error": None,
                               "planner_error": None,
                               "completion": None})
            self.progress = {"step_index": 0, "step_total": 0,
                             "done": 0, "current_action": ""}
        self.pause_gate.clear()
        self.kill.reset()
        return {"ok": True}

    def dismiss_completion(self):
        """Dismiss the completion state (v3.7.2): the user has seen
        the "work done" moment and moved on.  Only the completion
        marker clears — the run's result stays for History and the
        state pill until reset or the next run, exactly as before.
        A live run refuses: there is nothing to dismiss yet."""
        with self.lock:
            if self.state["running"]:
                raise HTTPException(409, "a run is in progress")
            self.state["completion"] = None
        return {"ok": True}

    def progress_payload(self):
        """Live work-completed view for companion + overlay."""
        q = self.queue.snapshot()
        total = self.progress["step_total"]
        done = self.progress["done"]
        result = self.state["result"]
        if total:
            percent = int(round(100 * done / total))
        elif result and result.get("status") == "success":
            percent = 100
        else:
            percent = 0
        active = [it for it in q["items"]
                  if it["status"] in ("running", "queued")]
        qpos = None
        if q["running_id"]:
            ids = [it["id"] for it in active]
            if q["running_id"] in ids:
                qpos = ids.index(q["running_id"]) + 1
        eta = None
        started = self.progress.get("started_at")
        if self.state["running"] and started and \
                load_config().get("feature_estimates", True):
            eta = estimates.eta_seconds(time.time() - started,
                                        done, total)
        return {
            "goal": self.state["goal"],
            "planner_mode": self.state["planner_mode"],
            "planner_display": planner_display(
                self.state["planner_mode"]),
            "source": self.state.get("source", "ui"),
            "running": self.state["running"],
            "paused": bool(self.pause_gate.paused
                           and self.state["running"]),
            "step_index": self.progress["step_index"],
            "step_total": total,
            "steps_done": done,
            "percent": percent,
            "eta_seconds": eta,
            "current_action": self.progress["current_action"],
            "queue_position": qpos,
            "queue_total": len(active),
            "queue_paused": q["paused"],
            "queue_halted": q["halted"],
        }

    def screen_info(self):
        return {"enabled": bool(load_config().get("screen_share")),
                "viewers": self.screen_viewers}


MANAGER = RunManager()
from . import __version__

_background_started = False


def start_background():
    """Start the scheduler and the proactive watcher exactly once,
    however the app is being served (serve(), uvicorn directly, or
    the desktop launcher)."""
    global _background_started
    if _background_started:
        return
    _background_started = True
    MANAGER.queue.start()
    MANAGER.scheduler.start()
    threading.Thread(target=_watcher_loop, daemon=True,
                     name="deskdora-watcher").start()


from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    start_background()
    yield


app = FastAPI(title="DeskDora", version=__version__, lifespan=_lifespan)


@app.middleware("http")
async def _no_store(request, call_next):
    # The UI is a local app shell: after an upgrade the window must never
    # be served a stale cached page (WebKitGTK happily caches the old UI).
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def _api_key_ok(request):
    """DeskDora API-key authentication.  Refused entirely when API
    access is switched off in Settings > Features — the key stops
    working everywhere, not just in one place."""
    if not load_config().get("feature_api", True):
        return False
    return API_KEYS.verify(presented_key(request.headers))


@app.middleware("http")
async def _companion_guard(request, call_next):
    # Features panel: with the companion switched off, its page and
    # its dedicated endpoints refuse — for local clients too.  The
    # shared /api/* surface the desktop app itself uses stays up.
    if request.url.path in ("/companion.html", "/api/companion/auth",
                            "/api/screen.mjpg") \
            and not load_config().get("feature_companion", True):
        return JSONResponse(
            {"detail": "companion is switched off in Settings"},
            status_code=403)
    # Remote-control safety rule: any call that does NOT come from
    # this machine itself must authenticate - with the companion PIN
    # (the phone companion) or with DeskDora's API key (programmatic
    # clients, v3.1).  Loopback - the owner at their own desktop - is
    # never challenged.  With neither credential configured,
    # non-loopback access is refused outright: serving the app on a
    # LAN must not silently hand strangers the confirm/kill/run
    # buttons, the run history, desktop screenshots, the memory
    # snapshot or the settings backup.
    #
    # Reads: only a minimal status payload (GET /api/status, with
    # the config scrubbed by public_config) stays open to
    # non-loopback clients.  Every other GET under /api/ requires
    # the same authentication as the screen stream (API key, PIN
    # header, or a valid companion session cookie) - BugHunter C4.
    if request.method == "GET" \
            and request.url.path.startswith("/api/") \
            and request.url.path != "/api/status":
        client = request.client.host if request.client else ""
        if client not in _LOCAL_HOSTS and not _screen_auth_ok(request):
            return JSONResponse(
                {"detail": "companion authentication required for "
                           "non-local reads"},
                status_code=401)
    if request.method in ("POST", "DELETE") \
            and request.url.path.startswith("/api/"):
        client = request.client.host if request.client else ""
        if client not in _LOCAL_HOSTS:
            if _api_key_ok(request):
                return await call_next(request)
            pin = load_config().get("companion_pin", "")
            if not pin or request.headers.get("x-companion-pin") != pin:
                return JSONResponse(
                    {"detail": "companion PIN or API key required for "
                               "non-local control"},
                    status_code=401)
    return await call_next(request)


class RunRequest(BaseModel):
    goal: str
    planner: str | None = None
    dry_run: bool | None = None


class SoundRequest(BaseModel):
    event: str


class ApprovePlanRequest(BaseModel):
    approved: bool
    skip: list[int] = []


class AskRequest(BaseModel):
    question: str


class DescribeRequest(BaseModel):
    text: str


class RoutineRunRequest(BaseModel):
    params: dict = {}


class ConfirmRequest(BaseModel):
    approved: bool


class RoutineRequest(BaseModel):
    name: str
    goal: str
    planner_mode: str = ""
    schedule: str | None = None
    trigger: dict | None = None


class FromRunRequest(BaseModel):
    name: str
    run_id: str
    schedule: str | None = None


class PlaceRequest(BaseModel):
    alias: str
    path: str


class ForgetRequest(BaseModel):
    kind: str          # places | apps | goals | routines
    key: str


class SayRequest(BaseModel):
    text: str


@app.get("/api/status")
def status():
    cfg = load_config()
    ollama = OllamaPlanner(model=cfg["model"])
    pending = None
    if MANAGER.pending:
        p = MANAGER.pending
        pending = {"id": p["id"], "step": p["step"],
                   "description": p["description"]}
    return {
        "state": {k: v for k, v in MANAGER.state.items()
                  if k != "last_event"},
        "last_event": MANAGER.state["last_event"],
        "config": public_config(cfg),
        "ollama_available": ollama.available(),
        "pending_confirmation": pending,
        "kill_tripped": MANAGER.kill.is_tripped(),
        "progress": MANAGER.progress_payload(),
        "screen_share": MANAGER.screen_info(),
    }


@app.post("/api/run")
def run(req: RunRequest, request: Request):
    source = request.headers.get("x-deskdora-client", "api")
    if source not in ("ui", "mcp", "api", "companion"):
        source = "api"
    # Features panel: with the MCP server switched off, runs that
    # arrive through it are refused at the service — the MCP process
    # also checks on startup, but the service is the enforcement.
    if source == "mcp" and not load_config().get("feature_mcp", True):
        raise HTTPException(403, "MCP server is switched off in "
                                 "Settings > Features")
    if not req.goal.strip():
        raise HTTPException(400, "goal is empty")
    return MANAGER.start_run(req.goal.strip(), req.planner,
                             source=source, dry_run=req.dry_run)


@app.post("/api/kill")
def kill():
    return MANAGER.trip_kill()


@app.post("/api/pause")
def pause():
    """Freeze: active run pauses at its next step boundary; the
    queue holds its remaining items."""
    return MANAGER.pause_all()


@app.post("/api/resume")
def resume():
    """Resume a frozen run and/or the queue.  After a kill, this
    continues the queue — a killed run never resumes mid-action."""
    return MANAGER.resume_all()


@app.post("/api/reset")
def reset():
    """Clear a finished/stopped run back to idle."""
    return MANAGER.reset_state()


@app.post("/api/completion/dismiss")
def completion_dismiss():
    """Dismiss the success completion state (v3.7.2) — the user
    has seen the "work done" moment and moved on."""
    return MANAGER.dismiss_completion()


@app.post("/api/confirmations/{cid}")
def confirm(cid: str, req: ConfirmRequest):
    return MANAGER.answer_confirmation(cid, req.approved)


@app.get("/api/runs")
def runs():
    out = list_runs(RUNS_DIR)
    for r in out:
        r["planner_display"] = planner_display(r.get("planner_mode"))
    return out


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    path = os.path.join(RUNS_DIR, run_id, "log.jsonl")
    if not os.path.isfile(path):
        raise HTTPException(404, "run not found")
    events = read_log(path)
    shots = sorted(f for f in os.listdir(os.path.join(RUNS_DIR, run_id))
                   if f.endswith(".png"))
    start_ev = next((e for e in events if e["kind"] == "run_start"), {})
    return {"id": run_id, "events": events, "screenshots": shots,
            "planner_display": planner_display(
                start_ev.get("planner_mode")),
            "source": start_ev.get("source", "ui")}


@app.get("/api/runs/{run_id}/screenshots/{name}")
def run_screenshot(run_id: str, name: str):
    path = os.path.join(RUNS_DIR, run_id, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(404, "screenshot not found")
    return FileResponse(path, media_type="image/png")


@app.delete("/api/runs/{run_id}")
def run_delete(run_id: str):
    """Delete one run's history record (its log + screenshots)."""
    name = os.path.basename(run_id)
    path = os.path.join(RUNS_DIR, name)
    if not name or not os.path.isdir(path):
        raise HTTPException(404, "run not found")
    if MANAGER.state.get("running") \
            and MANAGER.state.get("run_dir") == path:
        raise HTTPException(409, "that run is still in progress")
    shutil.rmtree(path)


# ---------------------------------------------------------------------------
# v3.3 endpoints: sounds, plan approval, undo, report export, ask
# ---------------------------------------------------------------------------

@app.post("/api/sound")
def sound_test(req: SoundRequest):
    """Queue one event tone for the window to play (the app
    window's app-opened tone, posted by the window shell at
    launch).  Settings previews no longer come through here —
    they play locally in the page, so a preview can audition the
    tone currently picked, saved or not."""
    cfg = load_config()
    played = MANAGER.emit_tone(req.event, cfg)
    return {"ok": played, "event": req.event}


@app.post("/api/runs/approve-plan")
def approve_plan(req: ApprovePlanRequest):
    return MANAGER.approve_plan(req.approved, req.skip)


@app.post("/api/runs/{run_id}/undo")
def run_undo(run_id: str):
    """Undo a run's journaled file changes (moves only).  Conflicts
    are reported, never silently overwritten."""
    cfg = load_config()
    if not cfg.get("feature_undo", True):
        raise HTTPException(403, "Undo is switched off in "
                                 "Settings > Features")
    name = os.path.basename(run_id)
    path = os.path.join(RUNS_DIR, name)
    if not name or not os.path.isdir(path):
        raise HTTPException(404, "run not found")
    return filejournal.undo_run(path)


@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str):
    """Self-contained HTML report for one run (offline-viewable)."""
    name = os.path.basename(run_id)
    path = os.path.join(RUNS_DIR, name)
    if not name or not os.path.isdir(path):
        raise HTTPException(404, "run not found")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(render_report(path))


@app.post("/api/ask")
def ask(req: AskRequest):
    """Ask about my screen: read-only Q&A over the accessibility
    tree.  Built-in mode answers extractively and says so; an LLM
    planner's answer is labelled with its provider/model."""
    cfg = load_config()
    if not cfg.get("feature_ask_screen", True):
        raise HTTPException(403, "Ask about screen is switched off "
                                 "in Settings > Features")
    if not req.question.strip():
        raise HTTPException(400, "question is empty")
    from .drivers import get_driver
    driver = get_driver(None if cfg.get("driver", "auto") == "auto"
                        else cfg["driver"])
    observation = driver.observe()
    mode = cfg["planner"]
    planner, mode_label = get_planner(
        mode, model=cfg["model"], known_apps=base_known_apps(),
        memory=MANAGER.memory, providers=cfg.get("providers"),
        customs=cfg.get("custom_providers"))
    completer = None
    complete = getattr(planner, "complete_text", None)
    if callable(complete):
        completer = complete
    return answer_question(req.question.strip(), observation,
                           planner_display(mode_label),
                           completer=completer)


@app.post("/api/routines/describe")
def routines_describe(req: DescribeRequest):
    """Plain-words routine draft.  The built-in parser handles the
    common shapes; with an LLM planner configured, its JSON draft
    is tried first and the parser is the fallback.  Either way the
    draft is shown for confirmation before anything is saved."""
    cfg = load_config()
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "description is empty")
    planner, mode_label = get_planner(
        cfg["planner"], model=cfg["model"],
        known_apps=base_known_apps(), memory=MANAGER.memory,
        providers=cfg.get("providers"),
        customs=cfg.get("custom_providers"))
    complete = getattr(planner, "complete_text", None)
    if callable(complete):
        try:
            raw = complete(
                "Turn the user's sentence into a routine draft. "
                "Reply with ONLY a JSON object: {\"goal\": str, "
                "\"schedule\": cron string or null, \"trigger\": "
                "{\"folder\": path} or null}.  Schedules: 'every "
                "morning' = '0 9 * * *'.  If you cannot, reply "
                "{\"error\": \"reason\"}.",
                text)
            draft = json.loads(raw)
            if draft.get("goal") and not draft.get("error"):
                return {"draft": {"goal": draft["goal"],
                                  "schedule": draft.get("schedule"),
                                  "trigger": draft.get("trigger")},
                        "via": planner_display(mode_label)}
        except Exception:
            pass  # fall through to the built-in parser
    try:
        return {"draft": describe_routine(text), "via": "Built-in"}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------------------
# v3.4 endpoints: diagnostics, templates, estimates
# ---------------------------------------------------------------------------

class TemplateRequest(BaseModel):
    name: str
    goal: str


@app.get("/api/diagnostics")
def diagnostics_get():
    """Measured live, never hard-coded (diagnostics.py)."""
    cfg = load_config()
    from .drivers import get_driver
    driver = get_driver(None if cfg.get("driver", "auto") == "auto"
                        else cfg["driver"])
    diag = diagnostics.collect(driver, cfg)
    return {**diag, "summary": diagnostics.summary_text(diag)}


@app.get("/api/templates")
def templates_list():
    return {"builtin": builtin_templates(),
            "personal": MANAGER.templates.list()}


@app.post("/api/templates")
def templates_create(req: TemplateRequest):
    try:
        return MANAGER.templates.add(req.name, req.goal)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/templates/{template_id}")
def templates_delete(template_id: str):
    if not MANAGER.templates.delete(template_id):
        raise HTTPException(404, "template not found")
    return {"ok": True}


@app.post("/api/templates/from-run")
def templates_from_run(req: FromRunRequest):
    """Save a history run as a personal template (its goal,
    verbatim — running it goes through the normal path)."""
    run_dir = os.path.join(RUNS_DIR, req.run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(404, "run not found")
    events = read_log(os.path.join(run_dir, "log.jsonl"))
    start = next((e for e in events if e["kind"] == "run_start"),
                 None)
    if not start:
        raise HTTPException(400, "run has no goal recorded")
    try:
        return MANAGER.templates.add(req.name, start["goal"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/estimate")
def estimate_get(goal: str = ""):
    """History-derived estimate, or null — never invented."""
    cfg = load_config()
    if not cfg.get("feature_estimates", True) or not goal.strip():
        return {"estimate": None}
    est = estimates.estimate_for_goal(goal.strip(), RUNS_DIR)
    return {"estimate": est,
            "text": estimates.format_estimate(est) if est else None}


@app.post("/api/routines/from-run-parameterized")
def routines_from_run_parameterized(req: FromRunRequest):
    """Teach a routine from history (v3.3): like from-run, but the
    goal's path literals become parameters with the original
    values as defaults."""
    run_dir = os.path.join(RUNS_DIR, req.run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(404, "run not found")
    try:
        return record_parameterized(MANAGER.routines, req.name,
                                    run_dir, schedule=req.schedule)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "deleted": name}


# ---------------------------------------------------------------------------
# Task queue (v3.2): multiple works, executed in order — one desktop
# has one pointer and one keyboard, so queued tasks take turns.
# ---------------------------------------------------------------------------

class QueueAddRequest(BaseModel):
    goal: str
    planner: str | None = None


class QueueMoveRequest(BaseModel):
    direction: str


@app.get("/api/queue")
def queue_get():
    return MANAGER.queue.snapshot()


@app.post("/api/queue")
def queue_add(req: QueueAddRequest):
    try:
        item = MANAGER.queue.add(req.goal, req.planner)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return item


@app.delete("/api/queue/{item_id}")
def queue_remove(item_id: str):
    if not MANAGER.queue.remove(item_id):
        raise HTTPException(404, "no such queued item")
    return {"ok": True}


@app.post("/api/queue/{item_id}/move")
def queue_move(item_id: str, req: QueueMoveRequest):
    try:
        moved = MANAGER.queue.move(item_id, req.direction)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not moved:
        raise HTTPException(404, "item cannot be moved")
    return MANAGER.queue.snapshot()


@app.post("/api/queue/pause")
def queue_pause():
    MANAGER.queue.pause()
    return MANAGER.queue.snapshot()


@app.post("/api/queue/resume")
def queue_resume():
    MANAGER.queue.resume()
    return MANAGER.queue.snapshot()


@app.post("/api/queue/clear")
def queue_clear():
    removed = MANAGER.queue.clear()
    return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------
# Companion: MCP status, screen-share session + stream (v3.2)
# ---------------------------------------------------------------------
@app.get("/api/mcp")
def mcp_info():
    """How an MCP client connects.  Read-only: no credential
    management is exposed to the companion."""
    return {
        "available": shutil.which("deskdora-mcp") is not None,
        "enabled": bool(load_config().get("feature_mcp", True)),
        "command": "deskdora-mcp",
        "transport": "stdio",
        "config": {"mcpServers": {"deskdora": {
            "command": "deskdora-mcp",
            "env": {"DESKDORA_API_KEY": "<your DeskDora API key>"}}}},
    }


def _screen_auth_ok(request):
    """Screen share is paired-only: DeskDora API key, companion PIN
    header, or a companion session cookie (from /api/companion/auth).
    Loopback is NOT exempt — a live screen is not a read endpoint."""
    if _api_key_ok(request):
        return True
    pin = load_config().get("companion_pin", "")
    if pin and request.headers.get("x-companion-pin") == pin:
        return True
    token = request.cookies.get("dd_companion_session")
    return bool(token
                and MANAGER.companion_sessions.get(token, 0) > time.time())


@app.post("/api/companion/auth")
def companion_auth(request: Request):
    """Exchange the companion PIN for a session cookie, so the
    companion page can load the screen stream (an <img> cannot send
    the PIN header)."""
    pin = load_config().get("companion_pin", "")
    if not pin or request.headers.get("x-companion-pin") != pin:
        raise HTTPException(401, "companion PIN required")
    token = secrets.token_urlsafe(24)
    MANAGER.companion_sessions[token] = time.time() + 12 * 3600
    MANAGER.emit_tone("companion_connected", load_config())
    resp = JSONResponse({"ok": True})
    resp.set_cookie("dd_companion_session", token, max_age=12 * 3600,
                    httponly=True, samesite="lax")
    return resp


@app.get("/api/screen.mjpg")
def screen_stream(request: Request):
    """View-only live screen for paired companions (MJPEG).

    Monitoring-grade: ~3 fps when capture is fast, backing off when
    it is not.  Serves nothing while sharing is off, and stops at
    the next frame the moment it is toggled off."""
    if not _screen_auth_ok(request):
        raise HTTPException(401, "companion authentication required")
    if not load_config().get("screen_share"):
        raise HTTPException(403, "screen sharing is off")

    def frames():
        MANAGER.screen_viewers += 1
        started = time.time()
        try:
            while time.time() - started < 900:
                if not load_config().get("screen_share"):
                    break
                t0 = time.time()
                try:
                    data, ctype = MANAGER.frame_provider()
                except Exception:
                    time.sleep(0.5)
                    continue
                yield (b"--frame\r\nContent-Type: " + ctype.encode()
                       + b"\r\nContent-Length: "
                       + str(len(data)).encode() + b"\r\n\r\n"
                       + data + b"\r\n")
                took = time.time() - t0
                time.sleep(max(0.05, max(0.34, took * 1.25) - took))
        finally:
            MANAGER.screen_viewers -= 1

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/routines")
def routines_list():
    return MANAGER.routines.list()


@app.post("/api/routines")
def routines_create(req: RoutineRequest):
    if req.trigger is not None:
        t = req.trigger
        if t.get("type") != "folder_change" or not t.get("path"):
            raise HTTPException(
                400, "trigger must be {'type': 'folder_change', "
                     "'path': ...}")
    return MANAGER.routines.save(req.name, req.goal, req.planner_mode,
                                 schedule=req.schedule, trigger=req.trigger)


@app.post("/api/routines/from-run")
def routines_from_run(req: FromRunRequest):
    run_dir = os.path.join(RUNS_DIR, req.run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(404, "run not found")
    try:
        return MANAGER.routines.record_from_run(req.name, run_dir,
                                                schedule=req.schedule)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/routines/{name}/run")
def routines_run(name: str, req: RoutineRunRequest | None = None):
    routine = MANAGER.routines.get(name)
    if not routine:
        raise HTTPException(404, "routine not found")
    MANAGER.routines.mark_run(name)
    goal = render_goal(routine, (req.params if req else None))
    return MANAGER.start_run(goal,
                             routine.get("planner_mode") or None,
                             routine_name=routine["name"])


@app.get("/api/scheduler/events")
def scheduler_events():
    return MANAGER.scheduler.events()


# ---------------------------------------------------------------------------
# Resident memory
# ---------------------------------------------------------------------------

@app.get("/api/memory")
def memory_get():
    return MANAGER.memory.snapshot()


@app.post("/api/memory/places")
def memory_set_place(req: PlaceRequest):
    try:
        entry = MANAGER.memory.set_place(req.alias, req.path)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"alias": req.alias.strip().lower(), **entry,
            "exists": os.path.isdir(req.path)}


@app.post("/api/memory/forget")
def memory_forget(req: ForgetRequest):
    if not MANAGER.memory.forget(req.kind, req.key):
        raise HTTPException(404, "no such memory entry")
    return {"ok": True}


@app.post("/api/memory/clear")
def memory_clear():
    MANAGER.memory.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Voice (offline TTS out, pluggable STT in)
# ---------------------------------------------------------------------------

@app.get("/api/voice")
def voice_info():
    return voice.voice_status()


@app.post("/api/voice/say")
def voice_say(req: SayRequest):
    engine = voice.get_tts()
    started = voice.speak(req.text, engine=engine)
    return {"ok": started, "engine": engine.name}


@app.post("/api/voice/render")
def voice_render(req: SayRequest):
    out_dir = os.path.join(DATA_DIR, "voice")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "render.wav")
    try:
        voice.render_speech(req.text, path)
    except voice.VoiceError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "engine": voice.get_tts().name,
            "path": path, "bytes": os.path.getsize(path)}


@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...)):
    import tempfile
    suffix = os.path.splitext(file.filename or "clip.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        text = voice.transcribe(tmp_path)
    except voice.TranscriptionError as exc:
        raise HTTPException(400, str(exc))
    finally:
        os.unlink(tmp_path)
    engine = voice.get_transcriber()
    return {"text": text, "engine": engine.name if engine else None}


@app.delete("/api/routines/{name}")
def routines_delete(name: str):
    if not MANAGER.routines.delete(name):
        raise HTTPException(404, "routine not found")
    return {"ok": True}


@app.get("/api/benchmark")
def benchmark():
    # Results live in the data directory (copied there when a
    # benchmark run is published); DESKDORA_BENCHMARK_RESULTS can
    # point elsewhere.  No developer-machine paths in the product.
    path = (os.environ.get("DESKDORA_BENCHMARK_RESULTS")
            or os.path.join(DATA_DIR, "benchmark-results.json"))
    if os.path.isfile(path):
        with open(path) as fh:
            return json.load(fh)
    return JSONResponse([], status_code=200)


@app.get("/api/config")
def config_get():
    return public_config(load_config())


@app.post("/api/config")
def config_post(cfg: dict):
    if "theme" in cfg and cfg["theme"] not in THEMES:
        raise HTTPException(400, "theme must be 'light' or 'dark'")
    if "user_name" in cfg and not isinstance(cfg["user_name"], str):
        raise HTTPException(400, "user_name must be text")
    if "setup_complete" in cfg \
            and not isinstance(cfg["setup_complete"], bool):
        raise HTTPException(400, "setup_complete must be true or false")
    if "hotkey" in cfg:
        hk = cfg["hotkey"]
        if not isinstance(hk, dict):
            raise HTTPException(400, "hotkey must be an object")
        if "enabled" in hk and not isinstance(hk["enabled"], bool):
            raise HTTPException(400, "hotkey.enabled must be "
                                     "true or false")
        if "combo" in hk:
            try:
                parse_combo(hk["combo"])
            except ValueError as exc:
                raise HTTPException(400, f"hotkey: {exc}")
    merged = load_config()
    current_pin = merged.get("companion_pin", "")
    for key in DEFAULT_CONFIG:
        if key in cfg:
            merged[key] = cfg[key]
    if "companion_pin" in cfg \
            and not str(cfg.get("companion_pin") or "").strip():
        # The PIN field is write-only (public_config never returns
        # it), so a blank field on save means "keep the current
        # PIN", exactly like the provider API key fields.
        merged["companion_pin"] = current_pin
    if "user_name" in cfg:
        # Stored trimmed and length-capped; blank is a legitimate
        # value (the wizard's name step is optional).
        merged["user_name"] = cfg["user_name"].strip()[:80]
    if isinstance(cfg.get("sounds"), dict):
        # Sounds merge field-wise so a partial update can't drop
        # event selections it didn't mention.  Tone selections
        # validate strictly, like every other setting: an unknown
        # event or a tone that doesn't exist is a 400, never a
        # silent save of a value nothing can play.
        current = sounds.sounds_config(merged)
        update = cfg["sounds"]
        if "enabled" in update:
            current["enabled"] = bool(update["enabled"])
        if "volume" in update:
            current["volume"] = update["volume"]
        if isinstance(update.get("events"), dict):
            try:
                sounds.validate_event_tones(update["events"])
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            for event, value in update["events"].items():
                current["events"][event] = \
                    sounds._migrate_value(event, value)
        merged["sounds"] = current
    if isinstance(cfg.get("hotkey"), dict):
        # Field-wise, like sounds: a partial update (just the
        # toggle) must not drop the recorded combo.
        stored_hk = {"enabled": True, "combo": "Ctrl+Alt+D"}
        stored_hk.update(load_config().get("hotkey") or {})
        stored_hk.update(cfg["hotkey"])
        merged["hotkey"] = stored_hk
    if "autonomy" in cfg:
        try:
            merged["autonomy"] = validate_policy(cfg["autonomy"])
        except PolicyError as exc:
            raise HTTPException(400, str(exc))
    save_config(merged)
    return public_config(merged)


# ---------------------------------------------------------------------------
# Planner validation (v3.7): the error page's "Check again".
# ---------------------------------------------------------------------------

class PlannerValidateRequest(BaseModel):
    planner: str | None = None


# ---------------------------------------------------------------------------
# Settings backup: export / import (v3.7.3).  The document carries
# setup only — config (secrets scrubbed by backup.py), routines,
# personal templates.  Import is preview-then-confirm, and every
# apply first snapshots the CURRENT state as a pre-import backup
# the same section can restore.
# ---------------------------------------------------------------------------

def _backup_stores():
    # The same stores the rest of the API reads and writes —
    # one source of truth for routines and templates.
    return MANAGER.routines, MANAGER.templates


def _current_backup_doc():
    routines, templates = _backup_stores()
    return build_backup(load_config(), routines.list(),
                        templates.list())


def _read_backup_file(path):
    path = (path or "").strip()
    if not path:
        raise HTTPException(400, "Enter the path to a backup file.")
    if not os.path.isfile(path):
        raise HTTPException(400, f"No file exists at {path}.")
    try:
        with open(path) as fh:
            data = json.load(fh)
    except Exception:
        raise HTTPException(
            400, "That file couldn't be read as a backup — it "
                 "isn't valid JSON.")
    try:
        return parse_backup(data)
    except BackupError as exc:
        raise HTTPException(400, str(exc))


def _apply_backup(doc):
    """Snapshot current state, then apply the backup.  Returns
    the pre-import snapshot's path."""
    routines, templates = _backup_stores()
    backup_dir = os.path.join(DATA_DIR, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    pre_path = os.path.join(backup_dir, f"pre-import-{stamp}.json")
    with open(pre_path, "w") as fh:
        json.dump(_current_backup_doc(), fh, indent=2)
    # Config: the backup's config over the defaults, through the
    # same validators the settings endpoint applies.
    new_config = json.loads(json.dumps(DEFAULT_CONFIG))
    new_config.update(doc["config"])
    if "theme" in new_config and new_config["theme"] not in THEMES:
        raise HTTPException(400, "theme must be 'light' or 'dark'")
    if isinstance(new_config.get("sounds"), dict):
        new_config["sounds"] = sounds.sounds_config(new_config)
    if "hotkey" in new_config:
        try:
            parse_combo((new_config["hotkey"] or {}).get("combo")
                        or "")
        except (ValueError, AttributeError) as exc:
            raise HTTPException(400, f"hotkey: {exc}")
    if "autonomy" in new_config:
        try:
            new_config["autonomy"] = validate_policy(
                new_config["autonomy"])
        except PolicyError as exc:
            raise HTTPException(400, str(exc))
    save_config(new_config)
    # Routines: replace the set with the backup's definitions.
    for existing in routines.list():
        routines.delete(existing["name"])
    for routine in doc["routines"]:
        routines.save(routine["name"], routine["goal"],
                      planner_mode=routine.get("planner_mode")
                      or "",
                      schedule=routine.get("schedule"),
                      trigger=routine.get("trigger"),
                      goal_template=routine.get("goal_template"),
                      params=routine.get("params") or [])
    templates.replace_all(doc["templates"])
    return pre_path


@app.get("/api/backup/export")
def backup_export():
    doc = _current_backup_doc()
    return JSONResponse(
        doc, headers={"Content-Disposition":
                      'attachment; filename="deskdora-backup.json"'})


class BackupPathRequest(BaseModel):
    path: str | None = None
    confirm: bool = False


@app.post("/api/backup/preview")
def backup_preview(req: BackupPathRequest):
    return preview_backup(_read_backup_file(req.path))


@app.post("/api/backup/import")
def backup_import(req: BackupPathRequest):
    if not req.confirm:
        raise HTTPException(
            400, "Import needs the confirmation step — preview "
                 "the backup and confirm before applying it.")
    doc = _read_backup_file(req.path)
    pre_path = _apply_backup(doc)
    return {"ok": True, "pre_import_backup": pre_path,
            "preview": preview_backup(doc)}


@app.post("/api/backup/restore-preimport")
def backup_restore_preimport():
    backup_dir = os.path.join(DATA_DIR, "backups")
    try:
        candidates = sorted(
            f for f in os.listdir(backup_dir)
            if f.startswith("pre-import-")
            and f.endswith(".json"))
    except FileNotFoundError:
        candidates = []
    if not candidates:
        raise HTTPException(
            400, "There is no pre-import backup to restore yet.")
    doc = _read_backup_file(
        os.path.join(backup_dir, candidates[-1]))
    pre_path = _apply_backup(doc)
    return {"ok": True, "restored": candidates[-1],
            "pre_import_backup": pre_path}


@app.post("/api/planner/validate")
def planner_validate(req: PlannerValidateRequest | None = None):
    """Re-run pre-run planner validation and mirror the verdict
    into state: a definitive problem (re)raises the error page, a
    pass clears it.  Validates the configured planner unless the
    caller names the mode the failed run used (a per-run planner
    override).  Changes nothing else — validation is read-only,
    and the fixes themselves go through the provider endpoints."""
    cfg = load_config()
    mode = (req.planner if req else None) or cfg["planner"]
    problem = validation.validate_planner(mode, cfg)
    with MANAGER.lock:
        if problem is not None:
            MANAGER.state["planner_error"] = {
                **problem, "planner": mode,
                "goal": MANAGER.state.get("goal", "")}
        else:
            MANAGER.state["planner_error"] = None
        current = MANAGER.state["planner_error"]
    return {"ok": problem is None, "planner_error": current}


# ---------------------------------------------------------------------------
# AI providers (v3.1): registry presets + user-defined custom providers.
# Keys live only in the local config file; every response masks them.
# ---------------------------------------------------------------------------

class ProviderSaveRequest(BaseModel):
    api_key: str | None = None
    model: str | None = None
    base_url: str | None = None


class CustomProviderRequest(BaseModel):
    name: str | None = None
    protocol: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


def _custom_public(custom):
    key = custom.get("api_key") or ""
    return {"id": custom["id"], "name": custom["name"],
            "protocol": custom["protocol"],
            "base_url": custom["base_url"], "model": custom["model"],
            "has_key": bool(key), "key_last4": key[-4:] if key else ""}


def _resolve_planner_for(pid, cfg):
    if pid in PROVIDER_REGISTRY:
        return make_provider_planner(
            pid, (cfg.get("providers") or {}).get(pid, {}),
            customs=cfg.get("custom_providers"))
    if any(c.get("id") == pid for c in cfg.get("custom_providers") or []):
        return make_provider_planner(pid, {},
                                     customs=cfg.get("custom_providers"))
    raise HTTPException(404, "unknown provider")


@app.get("/api/providers")
def providers_get():
    cfg = load_config()
    registry = [{"id": pid,
                 **{k: v for k, v in spec.items() if k != "env_base"}}
                for pid, spec in PROVIDER_REGISTRY.items()]
    return {"registry": registry, "state": provider_state(cfg),
            "customs": [_custom_public(c)
                        for c in cfg.get("custom_providers") or []],
            "planner": cfg.get("planner", "auto")}


@app.post("/api/providers/{pid}")
def providers_save(pid: str, req: ProviderSaveRequest):
    if pid not in PROVIDER_REGISTRY:
        raise HTTPException(404, "unknown provider")
    cfg = load_config()
    providers = dict(cfg.get("providers") or {})
    stored = dict(providers.get(pid) or {})
    if req.api_key is not None:
        if req.api_key.strip():
            stored["api_key"] = req.api_key.strip()
        else:
            stored.pop("api_key", None)
    if req.model is not None:
        if req.model.strip():
            stored["model"] = req.model.strip()
        else:
            stored.pop("model", None)
    if req.base_url is not None:
        base = req.base_url.strip().rstrip("/")
        if base:
            if not base.startswith(("http://", "https://")):
                raise HTTPException(
                    400, "base_url must start with http:// or https://")
            stored["base_url"] = base
        else:
            stored.pop("base_url", None)
    providers[pid] = stored
    cfg["providers"] = providers
    save_config(cfg)
    return {"provider": pid, **provider_state(cfg)[pid]}


@app.post("/api/providers/{pid}/test")
def providers_test(pid: str):
    cfg = load_config()
    if pid in PROVIDER_REGISTRY \
            and PROVIDER_REGISTRY[pid]["protocol"] == "ollama":
        stored = (cfg.get("providers") or {}).get("ollama") or {}
        planner = OllamaPlanner(
            model=stored.get("model") or cfg["model"])
        ok = planner.available()
        return {"ok": ok,
                "detail": f"Ollama {'reachable' if ok else 'not reachable'}"
                          f" with model {planner.model}"}
    planner = _resolve_planner_for(pid, cfg)
    ok, detail = planner.test_connection()
    return {"ok": ok, "detail": detail}


@app.post("/api/providers/{pid}/models")
def providers_models(pid: str):
    cfg = load_config()
    planner = _resolve_planner_for(pid, cfg)
    fetch = getattr(planner, "fetch_models", None)
    if fetch is None:
        return {"provider": pid, "models": None}
    try:
        models = fetch()
    except PlanError as exc:
        raise HTTPException(400, str(exc))
    return {"provider": pid, "models": models}


class PullRequest(BaseModel):
    model: str | None = None


def _ollama_planner(cfg):
    stored = (cfg.get("providers") or {}).get("ollama") or {}
    host = stored.get("base_url") \
        or PROVIDER_REGISTRY["ollama"]["base_url"]
    return OllamaPlanner(model=stored.get("model") or cfg["model"],
                         host=host)


@app.get("/api/providers/ollama/installed")
def providers_ollama_installed():
    """The models Ollama has on this machine, live from /api/tags
    (v3.7.2).  reachable=False is a 200, not an error: the picker
    needs to say 'Ollama isn't running' in plain words, which is
    a normal state, not a failure of this endpoint."""
    planner = _ollama_planner(load_config())
    try:
        models = planner.installed_models()
    except PlanError:
        return {"reachable": False, "models": []}
    return {"reachable": True, "models": models}


@app.post("/api/providers/ollama/pull")
def providers_pull_ollama(req: PullRequest):
    """Stream an Ollama model download as NDJSON progress events
    (v3.6, built for the setup wizard's AI step).

    Each line is one Ollama pull event enriched by PullAggregator
    (v3.7.2): Ollama reports bytes per layer, so the raw events
    carry completed_all / total_all / percent for the WHOLE
    model alongside the layer's own numbers — the UI renders the
    aggregates.  A failure — Ollama down, model unknown — arrives
    as a final {"error": ...} event rather than a hung or
    half-dead stream, so the wizard can show it inline and offer
    retry/skip.  Local path only: cloud providers have nothing
    to download."""
    cfg = load_config()
    stored = (cfg.get("providers") or {}).get("ollama") or {}
    model = (req.model or "").strip() \
        or stored.get("model") or cfg["model"]
    planner = _ollama_planner(cfg)
    planner.model = model

    def events():
        agg = PullAggregator()
        try:
            for event in planner.pull():
                yield json.dumps(agg.feed(event)) + "\n"
        except PlanError as exc:
            yield json.dumps({"error": str(exc)}) + "\n"

    return StreamingResponse(events(),
                             media_type="application/x-ndjson")


class ImportCheckRequest(BaseModel):
    path: str | None = None


class ImportRequest(BaseModel):
    path: str | None = None
    name: str | None = None


@app.post("/api/providers/ollama/import/check")
def providers_ollama_import_check(req: ImportCheckRequest):
    """Verify a .gguf file BEFORE importing (v3.7.2): existence,
    extension, GGUF magic, and the header's declared name /
    architecture.  Only the header is read — never the weights."""
    try:
        info = check_model_file(req.path or "")
    except GgufError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **info,
            "suggested_name": model_name_from_filename(req.path)}


@app.post("/api/providers/ollama/import")
def providers_ollama_import(req: ImportRequest):
    """Import a local .gguf file into Ollama (v3.7.2), streamed
    as NDJSON progress in the same shape as the pull endpoint so
    one UI renderer serves both.  The file is validated first
    (header check); Ollama's create API does the importing —
    DeskDora never loads the model itself."""
    try:
        check_model_file(req.path or "")
        name = validate_model_name(
            req.name or model_name_from_filename(req.path or ""))
    except GgufError as exc:
        raise HTTPException(400, str(exc))
    planner = _ollama_planner(load_config())

    def events():
        agg = PullAggregator()
        try:
            for event in planner.create_from_file(
                    name, (req.path or "").strip()):
                yield json.dumps(agg.feed(event)) + "\n"
        except PlanError as exc:
            yield json.dumps({"error": str(exc)}) + "\n"

    return StreamingResponse(events(),
                             media_type="application/x-ndjson")


@app.post("/api/custom-providers")
def customs_create(req: CustomProviderRequest):
    try:
        cleaned = validate_custom_provider(req.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    slug = re.sub(r"[^a-z0-9]+", "-",
                  cleaned["name"].lower()).strip("-") or "provider"
    cid = f"custom-{slug}-{uuid.uuid4().hex[:4]}"
    cfg = load_config()
    customs = list(cfg.get("custom_providers") or [])
    customs.append({"id": cid, **cleaned})
    cfg["custom_providers"] = customs
    save_config(cfg)
    return _custom_public(customs[-1])


@app.post("/api/custom-providers/{cid}")
def customs_update(cid: str, req: CustomProviderRequest):
    cfg = load_config()
    customs = list(cfg.get("custom_providers") or [])
    for custom in customs:
        if custom.get("id") == cid:
            merged = dict(custom)
            for field in ("name", "protocol", "base_url", "model",
                          "api_key"):
                value = getattr(req, field)
                if value is not None:
                    merged[field] = value
            try:
                cleaned = validate_custom_provider(merged)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            custom.update(cleaned)
            cfg["custom_providers"] = customs
            save_config(cfg)
            return _custom_public(custom)
    raise HTTPException(404, "unknown custom provider")


@app.delete("/api/custom-providers/{cid}")
def customs_delete(cid: str):
    cfg = load_config()
    before = cfg.get("custom_providers") or []
    customs = [c for c in before if c.get("id") != cid]
    if len(customs) == len(before):
        raise HTTPException(404, "unknown custom provider")
    cfg["custom_providers"] = customs
    if cfg.get("planner") == cid:
        cfg["planner"] = "auto"
    save_config(cfg)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Access (v3.1): DeskDora's own API key + MCP connection info
# ---------------------------------------------------------------------------

@app.get("/api/access")
def access_get():
    return {"api_key": API_KEYS.status(),
            "mcp": {"command": "deskdora-mcp", "transport": "stdio"}}


@app.post("/api/access/key/generate")
def access_generate():
    key = API_KEYS.generate()
    return {"key": key, **API_KEYS.status()}


@app.post("/api/access/key/revoke")
def access_revoke():
    API_KEYS.revoke()
    return {"ok": True, **API_KEYS.status()}


# ---------------------------------------------------------------------------
# Proactive proposals
# ---------------------------------------------------------------------------

@app.get("/api/proposals")
def proposals_list():
    return MANAGER.proposals.list()


@app.post("/api/proposals/{proposal_id}/approve")
def proposals_approve(proposal_id: str):
    result = MANAGER.watcher.approve(proposal_id)
    if result is None:
        raise HTTPException(404, "no such open proposal")
    return result


@app.post("/api/proposals/{proposal_id}/dismiss")
def proposals_dismiss(proposal_id: str):
    proposal = MANAGER.watcher.dismiss(proposal_id)
    if proposal is None:
        raise HTTPException(404, "no such proposal")
    return proposal


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _watcher_tick():
    cfg = load_config()
    MANAGER.watcher.policy = AutonomyPolicy(cfg.get("autonomy"))
    # Features panel: proposals off = the watcher does not scan at
    # all.  Existing proposals stay listed; no new ones are raised.
    if cfg.get("proposals_enabled", True):
        MANAGER.watcher.scan(cfg.get("watch_folders") or [])


def _watcher_loop():
    while True:
        try:
            _watcher_tick()
        except Exception:
            pass  # a bad folder must never kill the watcher
        time.sleep(45)


def serve(host="127.0.0.1", port=8765):
    import uvicorn
    os.makedirs(RUNS_DIR, exist_ok=True)
    start_background()
    uvicorn.run(app, host=host, port=port, log_level="info")
