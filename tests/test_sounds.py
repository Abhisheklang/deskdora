"""Sound tones (v3.3): locally synthesised WAVs, per-event
toggles, fire-and-forget playback.  A disabled event must produce
NO playback call; defaults follow the spec (completed / failed /
confirmation on; app-opened / command-sent / phone-connected off).
"""
import wave

from deskdora import server, sounds


def cfg(**over):
    base = {"sounds": {"enabled": True, "volume": 70,
                       "events": dict(sounds.DEFAULT_SOUNDS["events"])}}
    base.update(over)
    return base


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, path, volume):
        self.calls.append((path, volume))
        return True


def test_defaults_match_spec():
    ev = sounds.DEFAULT_SOUNDS["events"]
    assert sounds.DEFAULT_SOUNDS["enabled"] is True
    for on in ("run_completed", "run_failed", "confirmation_waiting",
               "queue_finished"):
        assert ev[on] is True, on
    for off in ("app_opened", "command_sent", "companion_connected",
                "command_received"):
        assert ev[off] is False, off


def test_enabled_event_plays(tmp_path):
    rec = Recorder()
    assert sounds.play("run_completed", cfg(), str(tmp_path),
                       player=rec) is True
    assert len(rec.calls) == 1
    assert rec.calls[0][1] == 70


def test_disabled_event_makes_no_playback_call(tmp_path):
    rec = Recorder()
    assert sounds.play("app_opened", cfg(), str(tmp_path),
                       player=rec) is False
    assert rec.calls == []


def test_master_off_makes_no_playback_call(tmp_path):
    rec = Recorder()
    off = cfg()
    off["sounds"]["enabled"] = False
    assert sounds.play("run_completed", off, str(tmp_path),
                       player=rec) is False
    assert rec.calls == []


def test_force_previews_a_disabled_event(tmp_path):
    rec = Recorder()
    assert sounds.play("app_opened", cfg(), str(tmp_path),
                       player=rec, force=True) is True
    assert len(rec.calls) == 1


def test_unknown_event_is_silent(tmp_path):
    rec = Recorder()
    assert sounds.play("explosion", cfg(), str(tmp_path),
                       player=rec) is False
    assert rec.calls == []


def test_tone_is_a_real_wav(tmp_path):
    path = sounds.tone_path(str(tmp_path), "run_failed")
    with wave.open(path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 22050
        assert wf.getnframes() > 1000


def test_failed_tone_differs_from_completed(tmp_path):
    a = sounds.tone_path(str(tmp_path), "run_completed")
    b = sounds.tone_path(str(tmp_path), "run_failed")
    assert open(a, "rb").read() != open(b, "rb").read()


def test_sounds_config_persists(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    client = TestClient(server.app)
    body = {"sounds": {"enabled": False, "volume": 25,
                       "events": {"run_completed": False}}}
    client.post("/api/config", json=body)
    saved = server.load_config()
    assert saved["sounds"]["enabled"] is False
    assert saved["sounds"]["volume"] == 25
    # untouched events keep their defaults after the merge
    assert saved["sounds"]["events"]["run_failed"] is True
    assert saved["sounds"]["events"]["run_completed"] is False
