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
import threading
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .agent import Agent
from .audit import RunLog, list_runs, read_log
from .planners import OllamaPlanner, get_planner
from .routines import RoutineStore
from .safety import AllowList, ConfirmationGate, KillSwitch
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
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w") as fh:
        json.dump(cfg, fh, indent=2)


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
        }
        self.kill = KillSwitch()
        self.pending = None          # dict with an Event + verdict
        self.routines = RoutineStore()

    # -- confirmation policy: blocks the agent thread until the UI answers
    def ui_policy(self, step, description=""):
        cid = uuid.uuid4().hex[:8]
        event = threading.Event()
        record = {"id": cid, "step": step, "description": description,
                  "created_at": time.time(), "event": event,
                  "approved": None}
        with self.lock:
            self.pending = record
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

    def start_run(self, goal, planner_mode=None):
        with self.lock:
            if self.state["running"]:
                raise HTTPException(409, "a run is already in progress")
            self.state.update({"running": True, "goal": goal,
                               "result": None})
        cfg = load_config()
        mode = planner_mode or cfg["planner"]
        self.kill.reset()

        def _on_event(kind, **fields):
            with self.lock:
                self.state["last_event"] = {"ts": time.time(),
                                            "kind": kind, **fields}

        def _work():
            planner, mode_label = get_planner(
                mode, model=cfg["model"], known_apps=base_known_apps())
            gate = ConfirmationGate(self.ui_policy)
            log = RunLog(RUNS_DIR, goal, mode_label)
            from .drivers import get_driver
            driver = get_driver(None if cfg.get("driver", "auto") == "auto"
                                else cfg["driver"])
            agent = Agent(planner, mode_label, gate, self.kill,
                          AllowList(cfg["allow_apps"]), log,
                          on_event=_on_event, driver=driver)
            with self.lock:
                self.agent = agent
                self.state["planner_mode"] = mode_label
                self.state["run_dir"] = log.dir
            try:
                result = agent.run(goal)
            except Exception as exc:  # agent.run already logs; be safe
                result = {"status": "failed", "reason": str(exc)}
            with self.lock:
                self.state["running"] = False
                self.state["result"] = result

        self.thread = threading.Thread(target=_work, daemon=True)
        self.thread.start()
        return {"ok": True, "goal": goal}

    def trip_kill(self):
        self.kill.trip()
        return {"ok": True, "tripped": True}


MANAGER = RunManager()
from . import __version__

app = FastAPI(title="DeskDora", version=__version__)


@app.middleware("http")
async def _no_store(request, call_next):
    # The UI is a local app shell: after an upgrade the window must never
    # be served a stale cached page (WebKitGTK happily caches the old UI).
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class RunRequest(BaseModel):
    goal: str
    planner: str | None = None


class ConfirmRequest(BaseModel):
    approved: bool


class RoutineRequest(BaseModel):
    name: str
    goal: str
    planner_mode: str = ""
    schedule: str | None = None


class FromRunRequest(BaseModel):
    name: str
    run_id: str
    schedule: str | None = None


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
        "config": cfg,
        "ollama_available": ollama.available(),
        "pending_confirmation": pending,
        "kill_tripped": MANAGER.kill.is_tripped(),
    }


@app.post("/api/run")
def run(req: RunRequest):
    if not req.goal.strip():
        raise HTTPException(400, "goal is empty")
    return MANAGER.start_run(req.goal.strip(), req.planner)


@app.post("/api/kill")
def kill():
    return MANAGER.trip_kill()


@app.post("/api/confirmations/{cid}")
def confirm(cid: str, req: ConfirmRequest):
    return MANAGER.answer_confirmation(cid, req.approved)


@app.get("/api/runs")
def runs():
    return list_runs(RUNS_DIR)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    path = os.path.join(RUNS_DIR, run_id, "log.jsonl")
    if not os.path.isfile(path):
        raise HTTPException(404, "run not found")
    events = read_log(path)
    shots = sorted(f for f in os.listdir(os.path.join(RUNS_DIR, run_id))
                   if f.endswith(".png"))
    return {"id": run_id, "events": events, "screenshots": shots}


@app.get("/api/runs/{run_id}/screenshots/{name}")
def run_screenshot(run_id: str, name: str):
    path = os.path.join(RUNS_DIR, run_id, os.path.basename(name))
    if not os.path.isfile(path):
        raise HTTPException(404, "screenshot not found")
    return FileResponse(path, media_type="image/png")


@app.get("/api/routines")
def routines_list():
    return MANAGER.routines.list()


@app.post("/api/routines")
def routines_create(req: RoutineRequest):
    return MANAGER.routines.save(req.name, req.goal, req.planner_mode,
                                 schedule=req.schedule)


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
def routines_run(name: str):
    routine = MANAGER.routines.get(name)
    if not routine:
        raise HTTPException(404, "routine not found")
    MANAGER.routines.mark_run(name)
    return MANAGER.start_run(routine["goal"],
                             routine.get("planner_mode") or None)


@app.delete("/api/routines/{name}")
def routines_delete(name: str):
    if not MANAGER.routines.delete(name):
        raise HTTPException(404, "routine not found")
    return {"ok": True}


@app.get("/api/benchmark")
def benchmark():
    path = os.path.expanduser(
        "~/workspace/deskdora-deliverables/evidence/benchmark/"
        "benchmark-results.json")
    if os.path.isfile(path):
        with open(path) as fh:
            return json.load(fh)
    return JSONResponse([], status_code=200)


@app.get("/api/config")
def config_get():
    return load_config()


@app.post("/api/config")
def config_post(cfg: dict):
    merged = load_config()
    for key in DEFAULT_CONFIG:
        if key in cfg:
            merged[key] = cfg[key]
    save_config(merged)
    return merged


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def serve(host="127.0.0.1", port=8765):
    import uvicorn
    os.makedirs(RUNS_DIR, exist_ok=True)
    uvicorn.run(app, host=host, port=port, log_level="info")
