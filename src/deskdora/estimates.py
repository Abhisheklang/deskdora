"""Time estimates (v3.4), derived from history — or honestly
absent.  An estimate exists only when past runs match the goal
(exactly, or by signature: the goal with paths and numbers
masked).  With no usable history the answer is None and the UI
says "no estimate yet" — a number is never invented."""
from __future__ import annotations

import re
import statistics

from .audit import list_runs

_PATH = re.compile(r"/[\w.\-]+(?:/[\w.\-]+)+")
_NUM = re.compile(r"\d+")


def signature(goal):
    text = _PATH.sub("<path>", (goal or "").lower())
    text = _NUM.sub("<n>", text)
    return " ".join(text.split())


def _run_facts(events):
    start = next((e for e in events if e["kind"] == "run_start"),
                 None)
    end = next((e for e in reversed(events)
                if e["kind"] == "run_end"), None)
    if not start or not end or not start.get("ts") \
            or not end.get("ts"):
        return None
    seconds = end["ts"] - start["ts"]
    if seconds <= 0:
        return None
    return {"goal": start.get("goal", ""),
            "seconds": seconds,
            "steps": end.get("steps_executed") or 0}


def estimate_for_goal(goal, runs_root):
    """Median duration/steps over matching past runs, or None.
    Exact goal matches win; signature matches are the fallback."""
    facts = []
    for entry in list_runs(runs_root):
        fact = _run_facts(entry.get("events") or [])
        if fact:
            facts.append(fact)
    if not facts:
        return None
    exact = [f for f in facts if f["goal"] == goal]
    pool = exact or [f for f in facts
                     if signature(f["goal"]) == signature(goal)]
    if not pool:
        return None
    return {
        "seconds": round(statistics.median(
            [f["seconds"] for f in pool])),
        "steps": int(statistics.median([f["steps"] for f in pool])),
        "based_on": len(pool),
        "match": "exact" if exact else "similar",
    }


def eta_seconds(elapsed, done, total):
    """Remaining-time estimate mid-run.  Meaningless before a few
    steps have landed, so it returns None until then."""
    if done < 5 or total <= 0 or done >= total or elapsed < 5:
        return None
    per_step = elapsed / done
    return round(per_step * (total - done))


def format_estimate(est):
    """'~2 min · ~140 steps (based on 3 previous runs)'"""
    if not est:
        return "no estimate yet"
    seconds = est["seconds"]
    if seconds < 90:
        span = f"~{max(1, round(seconds))} sec"
    else:
        span = f"~{round(seconds / 60)} min"
    return (f"{span} · ~{est['steps']} steps "
            f"(based on {est['based_on']} previous "
            f"run{'s' if est['based_on'] != 1 else ''})")
