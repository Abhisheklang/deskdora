"""Wave 4 regression tests for DD-A1: Scheduler.tick() and
RoutineStore.list() must be total over stored data.  A routine
with a non-string schedule, a corrupt-JSON routine file, or a
non-object routine file must never abort the tick pass - the
routines sorted after the bad file still fire, on every tick."""
import json
import os
from datetime import datetime

import pytest

from deskdora.routines import (RoutineStore, cron_matches,
                               due_routines, validate_cron)
from deskdora.scheduler import Scheduler

NOW = datetime(2026, 11, 17, 10, 30)


def _write(store, fname, text):
    with open(os.path.join(store.root, fname), "w") as fh:
        fh.write(text)


def _scheduler(store, tmp_path, calls):
    def runner(routine):
        calls.append(routine["name"])
        return True, "started"
    return Scheduler(store, runner, str(tmp_path / "events.jsonl"))


def test_nonstring_schedule_does_not_abort_tick(tmp_path):
    store = RoutineStore(str(tmp_path / "routines"))
    # Sorted first, so the old code aborted before "zzz-good".
    _write(store, "aaa-bad.json", json.dumps(
        {"name": "aaa-bad", "goal": "g", "schedule": 123}))
    store.save("zzz-good", "goal", schedule="* * * * *")
    calls = []
    scheduler = _scheduler(store, tmp_path, calls)
    assert scheduler.tick(NOW) == ["zzz-good"]
    # And again on the next tick: the failure is not one-shot.
    assert scheduler.tick(NOW.replace(minute=31)) == ["zzz-good"]
    assert calls == ["zzz-good", "zzz-good"]


@pytest.mark.parametrize("bad", [0, 9.5, ["* * * * *"],
                                 {"expr": "* * * * *"}])
def test_typed_schedule_shapes_all_isolated(tmp_path, bad):
    store = RoutineStore(str(tmp_path / "routines"))
    _write(store, "aaa-bad.json", json.dumps(
        {"name": "aaa-bad", "goal": "g", "schedule": bad}))
    store.save("zzz-good", "goal", schedule="* * * * *")
    calls = []
    scheduler = _scheduler(store, tmp_path, calls)
    assert scheduler.tick(NOW) == ["zzz-good"]


def test_corrupt_json_file_is_quarantined_and_skipped(tmp_path):
    store = RoutineStore(str(tmp_path / "routines"))
    _write(store, "aaa-bad.json", "{not json at all")
    store.save("zzz-good", "goal", schedule="* * * * *")
    calls = []
    scheduler = _scheduler(store, tmp_path, calls)
    assert scheduler.tick(NOW) == ["zzz-good"]
    # The bad file was quarantined, not left to poison every tick.
    assert not os.path.exists(
        os.path.join(store.root, "aaa-bad.json"))
    assert os.path.exists(
        os.path.join(store.root, "aaa-bad.json.corrupt"))
    assert [r["name"] for r in store.list()] == ["zzz-good"]


def test_nonobject_routine_file_is_quarantined(tmp_path):
    store = RoutineStore(str(tmp_path / "routines"))
    _write(store, "aaa-list.json", json.dumps([1, 2, 3]))
    store.save("zzz-good", "goal")
    assert [r["name"] for r in store.list()] == ["zzz-good"]
    assert os.path.exists(
        os.path.join(store.root, "aaa-list.json.corrupt"))


def test_string_schedule_behaviour_unchanged(tmp_path):
    # The wave 3 CR1 string behaviour must stay identical.
    store = RoutineStore(str(tmp_path / "routines"))
    store.save("tidy", "goal", schedule="0 9 * * *")
    calls = []
    scheduler = _scheduler(store, tmp_path, calls)
    assert scheduler.tick(NOW) == []
    assert scheduler.tick(datetime(2026, 11, 17, 9, 0)) == ["tidy"]
    with pytest.raises(ValueError):
        validate_cron("*/0 * * * *")


def test_cron_helpers_raise_valueerror_for_typed_input():
    for bad in (123, 9.5, ["* * * * *"], {"a": 1}):
        with pytest.raises(ValueError):
            validate_cron(bad)
        with pytest.raises(ValueError):
            cron_matches(bad, NOW)


def test_due_routines_skips_bad_schedule(tmp_path):
    store = RoutineStore(str(tmp_path / "routines"))
    _write(store, "aaa-bad.json", json.dumps(
        {"name": "aaa-bad", "goal": "g", "schedule": 42}))
    store.save("zzz-good", "goal", schedule="* * * * *")
    assert [r["name"] for r in due_routines(store, NOW)] == \
        ["zzz-good"]
