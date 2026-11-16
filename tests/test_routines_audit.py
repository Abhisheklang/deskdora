import json
import os
from datetime import datetime

import pytest

from deskdora.audit import RunLog, list_runs, read_log
from deskdora.routines import RoutineStore, cron_matches


def test_runlog_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("deskdora.audit.actions.screenshot",
                        lambda path: path)
    log = RunLog(str(tmp_path), "Test goal", "builtin")
    log.event("plan", steps=[])
    log.finish("success", answer="42")
    events = read_log(log.path)
    kinds = [e["kind"] for e in events]
    assert kinds == ["run_start", "plan", "run_end"]
    runs = list_runs(str(tmp_path))
    assert runs and runs[0]["status"] == "success"
    assert runs[0]["goal"] == "Test goal"


def test_routine_store_crud(tmp_path):
    store = RoutineStore(str(tmp_path))
    data = store.save("Morning Tidy", "Organise folder /tmp/x by type",
                      planner_mode="builtin", schedule="0 9 * * 1")
    assert data["name"] == "morning-tidy"
    assert store.get("Morning Tidy")["goal"].startswith("Organise")
    assert len(store.list()) == 1
    store.mark_run("morning-tidy")
    assert store.get("morning-tidy")["run_count"] == 1
    assert store.delete("morning-tidy") is True
    assert store.list() == []


def test_record_from_run(tmp_path):
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    events = [
        {"kind": "run_start", "goal": "Open terminal",
         "planner_mode": "builtin"},
        {"kind": "run_end", "status": "success"},
    ]
    with open(run_dir / "log.jsonl", "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    store = RoutineStore(str(tmp_path / "routines"))
    data = store.record_from_run("term", str(run_dir))
    assert data["goal"] == "Open terminal"
    # a failed run must not become a routine
    events[1]["status"] = "failed"
    with open(run_dir / "log.jsonl", "w") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    with pytest.raises(ValueError):
        store.record_from_run("term2", str(run_dir))


def test_cron_matches():
    monday_9am = datetime(2026, 11, 16, 9, 0)   # 2026-11-16 is a Monday
    tuesday_9am = datetime(2026, 11, 17, 9, 0)
    assert cron_matches("0 9 * * 1", monday_9am) is True
    assert cron_matches("0 9 * * 1", tuesday_9am) is False
    assert cron_matches("*/15 * * * *", datetime(2026, 1, 1, 3, 30)) is True
    assert cron_matches("*/15 * * * *", datetime(2026, 1, 1, 3, 31)) is False
    assert cron_matches("0 9-17 * * *", datetime(2026, 1, 1, 12, 0)) is True
    with pytest.raises(ValueError):
        cron_matches("0 9 * *", monday_9am)
