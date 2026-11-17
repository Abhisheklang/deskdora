#!/usr/bin/env python3
"""Run the DeskDora v0.1 five-task gate inside a desktop test session.

Usage (with the desktop session environment sourced):
  python3 scripts/run_gate.py [--task organise-folder] [--planner builtin]

Writes gate-results.json next to this script's evidence output directory
and prints a per-task PASS/FAIL table.  Exit code is 0 only if all tasks
pass — the report, not the exit code, is the deliverable.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from deskdora.cli import build_agent          # noqa: E402
from deskdora.tasks import (SITE_PORT, FIXTURES, gate_tasks,  # noqa: E402
                            prepare_gate_goals, task_ctx)

EVIDENCE = os.environ.get("DESKDORA_EVIDENCE_DIR",
                          os.path.join(os.getcwd(), "evidence", "gate"))
APPS_TO_CLOSE = ["thunar", "gnumeric", "xfce4-appearance-settings",
                 "mousepad", "form_app.py"]


def ensure_site_server():
    url = f"http://127.0.0.1:{SITE_PORT}/index.html"
    try:
        urllib.request.urlopen(url, timeout=2)
        return "already running"
    except Exception:
        pass
    subprocess.Popen(
        [sys.executable, "-m", "http.server", str(SITE_PORT),
         "--directory", str(FIXTURES / "site")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    for _ in range(20):
        try:
            urllib.request.urlopen(url, timeout=2)
            return "started"
        except Exception:
            time.sleep(0.5)
    return "FAILED to start"


def clean_desktop():
    for app in APPS_TO_CLOSE:
        subprocess.run(["pkill", "-f", app], capture_output=True)
    subprocess.run(["pkill", "-f", "chrome-linux64"], capture_output=True)
    time.sleep(1.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default=None)
    parser.add_argument("--planner", default="builtin")
    args = parser.parse_args()

    os.makedirs(EVIDENCE, exist_ok=True)
    print(f"test site: {ensure_site_server()}")
    tasks = gate_tasks()
    if args.task:
        tasks = [t for t in tasks if t.id == args.task]
    results = []
    for task in tasks:
        ctx = task_ctx(task)
        print(f"\n=== {task.id}: {task.title} ===")
        clean_desktop()
        try:
            task.setup(ctx)
        except Exception as exc:
            print(f"SETUP FAILED: {exc}")
            results.append({"id": task.id, "title": task.title,
                            "verdict": "FAIL", "stage": "setup",
                            "detail": str(exc)})
            continue
        prepare_gate_goals([task])
        known = dict(ctx.data.get("apps", {}))
        agent = build_agent(task.goal, planner_mode=args.planner,
                            auto_approve=True,
                            runs_dir=os.path.join(EVIDENCE, "runs"),
                            known_apps=known)
        os.chdir(ctx.dir)
        started = time.time()
        run = agent.run(task.goal)
        elapsed = round(time.time() - started, 1)
        try:
            ok, detail = task.assertion(ctx, run)
        except Exception as exc:
            ok, detail = False, f"assertion error: {exc}"
        verdict = "PASS" if (ok and run["status"] == "success") else "FAIL"
        print(f"run status={run['status']} steps={run['steps_executed']} "
              f"({elapsed}s) assertion={'OK' if ok else 'FAILED'}: {detail}")
        print(f"VERDICT: {verdict}  (log: {agent.log.dir})")
        results.append({
            "id": task.id, "title": task.title, "goal": task.goal,
            "verdict": verdict, "run_status": run["status"],
            "run_reason": run.get("reason", ""), "answers": run.get("answers"),
            "steps": run["steps_executed"], "seconds": elapsed,
            "assertion_detail": detail, "log_dir": agent.log.dir,
            "planner_mode": agent.planner_mode,
        })
        clean_desktop()
    out = os.path.join(EVIDENCE, "gate-results.json")
    with open(out, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\nGATE: {passed}/{len(results)} tasks passed -> {out}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
