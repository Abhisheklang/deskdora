"""v2.0 features: resident memory, voice pipeline, scheduler."""
import os
from datetime import datetime, timedelta

import pytest

from deskdora import voice
from deskdora.memory import MemoryStore
from deskdora.planners import BuiltinPlanner, PlanError
from deskdora.routines import RoutineStore
from deskdora.scheduler import Scheduler, folder_signature


# ---------------------------------------------------------------- memory

@pytest.fixture
def mem(tmp_path):
    return MemoryStore(str(tmp_path / "memory.json"))


def test_memory_places_roundtrip(mem):
    mem.set_place("Invoices", "/home/u/Documents/invoices")
    assert mem.get_place("invoices") == "/home/u/Documents/invoices"
    assert mem.resolve_place("INVOICES") == "/home/u/Documents/invoices"
    assert mem.delete_place("invoices") is True
    assert mem.get_place("invoices") is None


def test_memory_persists_and_survives_corruption(tmp_path):
    path = str(tmp_path / "memory.json")
    store = MemoryStore(path)
    store.set_place("a", "/x")
    assert MemoryStore(path).get_place("a") == "/x"
    with open(path, "w") as fh:
        fh.write("{not json")
    store2 = MemoryStore(path)          # must not raise
    assert store2.snapshot()["places"] == {}
    assert os.path.exists(path + ".corrupt")


def test_memory_learns_only_from_success(mem):
    steps = [{"action": "launch", "command": "thunar /tmp/messy"}]
    mem.learn_from_run("Organise folder /tmp/messy by type", steps,
                       "failed", organised_paths=["/tmp/messy"])
    assert mem.snapshot()["apps"] == {}
    mem.learn_from_run("Organise folder /tmp/messy by type", steps,
                       "success", organised_paths=["/tmp/messy"])
    snap = mem.snapshot()
    assert snap["apps"]["thunar /tmp/messy"]["count"] == 1
    assert snap["places"]["messy"]["path"] == "/tmp/messy"
    assert snap["places"]["messy"]["source"] == "learned"
    assert snap["habits"]["goals"][
        "organise folder /tmp/messy by type"] == 1


def test_memory_clear_and_forget(mem):
    mem.set_place("a", "/x")
    mem.note_app("thunar")
    mem.clear()
    assert mem.snapshot()["places"] == {}
    assert mem.snapshot()["apps"] == {}


def test_planner_resolves_memory_alias(tmp_path):
    messy = tmp_path / "messy"
    messy.mkdir()
    (messy / "a.png").write_text("x")
    store = MemoryStore(str(tmp_path / "memory.json"))
    store.set_place("messy", str(messy))
    planner = BuiltinPlanner(memory=store)
    for goal in ("Organise folder my messy by type",
                 "Organise my messy folder by type",
                 "Organise folder messy by type"):
        plan = planner.plan(goal, None)
        assert plan[0]["command"] == f"thunar {messy}"
    with pytest.raises(PlanError, match="no remembered place"):
        planner.plan("Organise folder my nonexistent by type", None)


def test_planner_without_memory_refuses_alias(tmp_path):
    planner = BuiltinPlanner()
    with pytest.raises(PlanError, match="folder not found"):
        planner.plan("Organise folder my messy by type", None)


# ---------------------------------------------------------------- voice

def test_voice_tts_status_shape():
    status = voice.tts_status()
    assert status["engine"] in ("espeak", "pyttsx3", "null")
    assert status["available"] is (status["engine"] != "null")


def test_voice_render_with_espeak_if_present(tmp_path):
    engine = voice.get_tts()
    if engine.name != "espeak":
        pytest.skip("espeak not available in this environment")
    out = str(tmp_path / "hello.wav")
    engine.render("DeskDora run finished.", out)
    assert os.path.getsize(out) > 1000


def test_voice_stt_pipeline_with_fake_engine(tmp_path):
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF-fake")
    engine = voice.FakeTranscriber("organise folder messy")
    assert voice.transcribe(str(wav), engine=engine) == \
        "organise folder messy"
    # the transcribed text is a goal the planner can act on
    planner = BuiltinPlanner()
    with pytest.raises(PlanError):
        planner.plan("organise folder messy", None)  # no such folder


def test_voice_transcribe_without_engine(monkeypatch):
    monkeypatch.setattr(voice, "get_transcriber", lambda **kw: None)
    with pytest.raises(voice.TranscriptionError, match="no offline"):
        voice.transcribe("/tmp/whatever.wav")


# ---------------------------------------------------------------- scheduler

@pytest.fixture
def sched(tmp_path):
    store = RoutineStore(str(tmp_path / "routines"))
    calls = []

    def runner(routine):
        calls.append(routine["name"])
        return True, "started"

    scheduler = Scheduler(store, runner, str(tmp_path / "events.jsonl"),
                          poll_seconds=1)
    return store, scheduler, calls


def test_scheduler_cron_fires_once_per_minute(sched):
    store, scheduler, calls = sched
    store.save("tidy", "Organise folder /tmp/x by type",
               schedule="* * * * *")
    now = datetime(2026, 11, 17, 10, 30)
    assert scheduler.tick(now) == ["tidy"]
    assert scheduler.tick(now) == []            # same minute: no refire
    assert scheduler.tick(now + timedelta(minutes=1)) == ["tidy"]
    assert calls == ["tidy", "tidy"]
    events = scheduler.events()
    assert [e["kind"] for e in events] == ["fired", "fired"]


def test_scheduler_cron_not_due(sched):
    store, scheduler, calls = sched
    store.save("tidy", "goal", schedule="0 9 * * *")
    assert scheduler.tick(datetime(2026, 11, 17, 10, 30)) == []
    assert calls == []


def test_scheduler_folder_trigger(sched, tmp_path):
    store, scheduler, calls = sched
    watched = tmp_path / "watched"
    watched.mkdir()
    store.save("tidy", "goal",
               trigger={"type": "folder_change", "path": str(watched)})
    now = datetime(2026, 11, 17, 10, 30)
    assert scheduler.tick(now) == []            # baseline sighting
    (watched / "new.png").write_text("x")
    assert scheduler.tick(now) == ["tidy"]      # change fires
    assert scheduler.tick(now) == []            # no change, no fire
    (watched / "new.png").unlink()
    assert scheduler.tick(now) == ["tidy"]      # deletion is a change


def test_scheduler_busy_runner_records_skip(tmp_path):
    store = RoutineStore(str(tmp_path / "routines"))
    store.save("tidy", "goal", schedule="* * * * *")
    scheduler = Scheduler(store, lambda r: (False, "run already active"),
                           str(tmp_path / "events.jsonl"))
    assert scheduler.tick(datetime(2026, 11, 17, 10, 30)) == []
    assert scheduler.events()[-1]["kind"] == "skipped"


def test_folder_signature_detects_changes(tmp_path):
    assert folder_signature(str(tmp_path / "nope")) is None
    before = folder_signature(str(tmp_path))
    (tmp_path / "a.txt").write_text("x")
    assert folder_signature(str(tmp_path)) != before


def test_scheduler_trigger_absorbs_own_changes(sched, tmp_path):
    # After a firing, the next change (typically the fired run's own
    # effects) is absorbed as the new baseline; the trigger then
    # re-arms on a quiet tick and can fire on a later real change.
    store, scheduler, calls = sched
    watched = tmp_path / "watched2"
    watched.mkdir()
    store.save("tidy", "goal",
               trigger={"type": "folder_change", "path": str(watched)})
    now = datetime(2026, 11, 17, 10, 30)
    assert scheduler.tick(now) == []                 # baseline
    (watched / "a.png").write_text("x")
    assert scheduler.tick(now) == ["tidy"]           # real change fires
    (watched / "b.png").write_text("x")              # run's own effect
    assert scheduler.tick(now) == []                 # absorbed
    assert scheduler.tick(now) == []                 # quiet: re-armed
    (watched / "c.png").write_text("x")
    assert scheduler.tick(now) == ["tidy"]           # fires again


def test_learn_from_log_dir_and_file(tmp_path):
    # Regression: learn_from_log must accept a run directory (the
    # server passes log.dir) as well as a log file path.
    import json as _json
    from deskdora.memory import learn_from_log
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    events = [
        {"kind": "run_start", "goal": "Organise folder /tmp/messy by type",
         "planner_mode": "builtin"},
        {"kind": "plan", "steps": [
            {"action": "launch", "command": "thunar /tmp/messy"}]},
        {"kind": "run_end", "status": "success"},
    ]
    with open(run_dir / "log.jsonl", "w") as fh:
        for e in events:
            fh.write(_json.dumps(e) + "\n")
    store = MemoryStore(str(tmp_path / "memory.json"))
    learn_from_log(store, str(run_dir), "success")
    assert store.get_place("messy") == "/tmp/messy"
