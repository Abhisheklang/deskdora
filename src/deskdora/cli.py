"""DeskDora command line.

  deskdora run "<goal>"     run one goal on the desktop
  deskdora tasks            list the built-in gate/benchmark tasks
  deskdora bench            run the benchmark suite
  deskdora serve            start the web UI service (127.0.0.1:8765)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .agent import Agent
from .audit import RunLog
from .planners import get_planner
from .safety import (AllowList, ConfirmationGate, KillSwitch, approve_all,
                     terminal_policy)
from .tasks import base_known_apps, gate_tasks


def default_runs_dir():
    return os.environ.get(
        "DESKDORA_RUNS_DIR",
        os.path.expanduser("~/.local/share/deskdora/runs"))


def default_memory():
    from .memory import MemoryStore
    data_dir = os.environ.get(
        "DESKDORA_DATA_DIR",
        os.path.expanduser("~/.local/share/deskdora"))
    return MemoryStore(os.path.join(data_dir, "memory.json"))


def build_agent(goal, planner_mode="auto", model="qwen2.5:1.5b",
                auto_approve=False, runs_dir=None, max_steps=400,
                known_apps=None, on_event=None, allow_apps=None,
                driver=None, memory=None, autonomy=None):
    apps = base_known_apps()
    if known_apps:
        apps.update(known_apps)
    planner, mode_label = get_planner(planner_mode, model=model,
                                      known_apps=apps,
                                      memory=memory or default_memory())
    if autonomy is None:
        # CLI runs honour the same per-category policy the service
        # uses (stored in the shared config file); anything invalid
        # falls back to None = legacy all-confirm behaviour.
        try:
            from .autonomy import AutonomyPolicy
            from .server import load_config
            autonomy = AutonomyPolicy(load_config().get("autonomy"))
        except Exception:
            autonomy = None
    kill = KillSwitch()
    kill.reset()
    if auto_approve:
        print("[DeskDora] NOTE: auto-approve policy is ON — destructive "
              "actions will be confirmed automatically. This is disclosed "
              "in the run log.", file=sys.stderr)
        policy = approve_all
    else:
        policy = terminal_policy()
    gate = ConfirmationGate(policy)
    log = RunLog(runs_dir or default_runs_dir(), goal, mode_label)
    return Agent(planner, mode_label, gate, kill, AllowList(allow_apps),
                 log, max_steps=max_steps, on_event=on_event, driver=driver,
                 autonomy=autonomy)


def cmd_run(args):
    memory = default_memory()
    agent = build_agent(args.goal, planner_mode=args.planner,
                        model=args.model, auto_approve=args.yes,
                        runs_dir=args.runs_dir, max_steps=args.max_steps,
                        memory=memory)
    result = agent.run(args.goal)
    try:
        from .memory import learn_from_log
        learn_from_log(memory, agent.log.dir, result.get("status", ""))
    except Exception:
        pass
    print(json.dumps(result, indent=2, default=str))
    print(f"run log: {agent.log.dir}", file=sys.stderr)
    return 0 if result["status"] == "success" else 1


def cmd_tasks(_args):
    for task in gate_tasks():
        print(f"{task.id:18s} {task.title}")
    return 0


def cmd_bench(args):
    from .benchmark import run_benchmark
    return run_benchmark(planner_mode=args.planner, model=args.model,
                         limit=args.limit)


def cmd_serve(args):
    from .server import serve
    serve(host=args.host, port=args.port)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="deskdora",
                                     description="Local-first desktop agent")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one goal")
    p_run.add_argument("goal")
    p_run.add_argument("--planner", default="auto",
                       choices=["auto", "builtin", "ollama"])
    p_run.add_argument("--model", default="qwen2.5:1.5b")
    p_run.add_argument("--yes", action="store_true",
                       help="auto-approve confirmations (disclosed in log)")
    p_run.add_argument("--runs-dir", default=None)
    p_run.add_argument("--max-steps", type=int, default=400)
    p_run.set_defaults(fn=cmd_run)

    p_tasks = sub.add_parser("tasks", help="list gate tasks")
    p_tasks.set_defaults(fn=cmd_tasks)

    p_bench = sub.add_parser("bench", help="run the benchmark")
    p_bench.add_argument("--planner", default="builtin",
                         choices=["auto", "builtin", "ollama"])
    p_bench.add_argument("--model", default="qwen2.5:1.5b")
    p_bench.add_argument("--limit", type=int, default=None)
    p_bench.set_defaults(fn=cmd_bench)

    p_serve = sub.add_parser("serve", help="start the web UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.set_defaults(fn=cmd_serve)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
