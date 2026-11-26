"""Wave 3 regression tests for vibe CR1 and V4."""
from datetime import datetime

import pytest

from deskdora.routines import RoutineStore, cron_matches, validate_cron


def test_step_zero_is_value_error_not_zero_division():
    with pytest.raises(ValueError):
        cron_matches("*/0 * * * *", datetime(2026, 1, 5, 9, 0))
    with pytest.raises(ValueError):
        validate_cron("*/0 * * * *")
    with pytest.raises(ValueError):
        validate_cron("not a cron")


def test_bad_schedule_refused_at_save(tmp_path):
    store = RoutineStore(root=str(tmp_path))
    with pytest.raises(ValueError):
        store.save("bad", "goal", schedule="*/0 * * * *")
    data = store.save("good", "goal", schedule="0 9 * * *")
    assert data["schedule"] == "0 9 * * *"


def test_dom_dow_or_semantics():
    monday = datetime(2026, 1, 5, 9, 0)  # 2026-01-05 is a Monday
    assert monday.weekday() == 0
    # Both restricted: standard cron fires on the Monday match even
    # though the day-of-month is 5, not 1.
    assert cron_matches("0 9 1 * 1", monday) is True
    assert cron_matches("0 9 5 * 1", monday) is True
    assert cron_matches("0 9 2 * 2", monday) is False
    # Only one restricted: the restricted one decides.
    assert cron_matches("0 9 5 * *", monday) is True
    assert cron_matches("0 9 * * 2", monday) is False


def test_scheduler_survives_bad_stored_schedule(tmp_path):
    # A schedule stored before validation existed (direct file) must
    # not stop other routines: tick catches it per routine.
    import json
    import os
    root = str(tmp_path)
    with open(os.path.join(root, "bad.json"), "w") as fh:
        json.dump({"name": "bad", "goal": "g", "schedule": "*/0 * * * *",
                   "trigger": None}, fh)
    with open(os.path.join(root, "good.json"), "w") as fh:
        json.dump({"name": "good", "goal": "g", "schedule": "* * * * *",
                   "trigger": None}, fh)
    from deskdora.scheduler import Scheduler
    fired = []
    sched = Scheduler(RoutineStore(root=root),
                      lambda r: (fired.append(r["name"]) or True, ""),
                      str(tmp_path / "events.jsonl"))
    out = sched.tick(now=datetime(2026, 1, 5, 9, 0))
    assert "good" in out


def test_corrupt_proposals_quarantined(tmp_path):
    from deskdora.proactive import ProposalStore
    path = str(tmp_path / "proposals.json")
    with open(path, "w") as fh:
        fh.write("{not json")
    store = ProposalStore(path)
    assert store.items == []
    assert (tmp_path / "proposals.json.corrupt").exists()


def test_corrupt_templates_quarantined(tmp_path):
    from deskdora.templates import TemplateStore
    path = str(tmp_path / "templates.json")
    with open(path, "w") as fh:
        fh.write("{not json")
    store = TemplateStore(path)
    assert store.list() == []
    assert (tmp_path / "templates.json.corrupt").exists()
