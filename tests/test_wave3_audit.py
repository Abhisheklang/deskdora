"""Wave 3 regression tests for vibe V1 and CR2."""
import json
import os

from deskdora.audit import RunLog, list_runs, read_log


def _make_run(tmp_path, goal="Goal"):
    log = RunLog(str(tmp_path), goal, "builtin")
    log.event("plan", steps=[])
    log.finish("success", answer="42")
    return log


def test_read_log_tolerates_truncated_last_line(tmp_path):
    log = _make_run(tmp_path)
    with open(log.path, "a") as fh:
        fh.write('{"kind": "step_resul')  # crash mid-write
    events = read_log(log.path)
    assert [e["kind"] for e in events] == ["run_start", "plan", "run_end"]


def test_run_with_bad_line_still_listed(tmp_path):
    log = _make_run(tmp_path)
    with open(log.path, "a") as fh:
        fh.write("{broken\n")
    runs = list_runs(str(tmp_path))
    assert len(runs) == 1 and runs[0]["status"] == "success"


def test_list_runs_summary_has_no_events(tmp_path):
    _make_run(tmp_path)
    runs = list_runs(str(tmp_path), include_events=False)
    assert "events" not in runs[0]
    assert runs[0]["event_count"] == 3
    assert runs[0]["goal"] == "Goal"
    full = list_runs(str(tmp_path))
    assert len(full[0]["events"]) == 3


def test_list_runs_limit(tmp_path):
    for i in range(3):
        _make_run(tmp_path, goal=f"G{i}")
    assert len(list_runs(str(tmp_path), include_events=False, limit=2)) == 2
