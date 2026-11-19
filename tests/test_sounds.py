"""Sound tones (v3.3, reworked v3.7.2): per-event tone selection.

The history these tests pin: up to v3.7.1 the server rendered
WAVs and played them through paplay, fire-and-forget with every
failure swallowed.  paplay is absent on many machines (including
this one) and headless desktops have no audio server, so the
tones were silently dead everywhere — the user reported never
hearing a single one.  Playback now lives in the app window
(WebAudio synthesis), and this module owns the part that can be
proven here: WHAT may sound.  The server resolves each event to
its configured tone (``sounds.resolve_tone``) and queues it in
the status payload (``RunManager.emit_tone``); the window plays
what it finds.  A silenced event queues NOTHING.
"""
import os
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from deskdora import server, sounds

ROOT = os.path.join(os.path.dirname(__file__), "..")
INDEX = os.path.join(ROOT, "src", "deskdora", "static", "index.html")


def cfg(events=None, enabled=True, volume=70):
    return {"sounds": {"enabled": enabled, "volume": volume,
                       "events": dict(events or {})}}


# ----------------------------------------------------------------------
# Defaults: completion MUST sound by default (silence was the bug)
# ----------------------------------------------------------------------

def test_defaults_match_spec():
    ev = sounds.DEFAULT_SOUNDS["events"]
    assert sounds.DEFAULT_SOUNDS["enabled"] is True
    assert ev["run_completed"] == "chime"
    assert ev["run_failed"] == "low"
    assert ev["confirmation_waiting"] == "bell"
    assert ev["queue_finished"] == "harp"
    for off in ("app_opened", "command_sent", "companion_connected",
                "command_received"):
        assert ev[off] == "off", off
    # Every default is a real tone or off — nothing unplayable.
    for tone in ev.values():
        assert tone == "off" or tone in sounds.TONES


def test_tone_library_sketches_are_real():
    assert len(sounds.TONES) >= 5
    for tone_id, (_label, notes) in sounds.TONES.items():
        assert notes, tone_id
        for freq, start, dur in notes:
            assert freq > 0 and dur > 0 and start >= 0


# ----------------------------------------------------------------------
# Resolution: the single gating point
# ----------------------------------------------------------------------

def test_resolve_returns_configured_tone():
    assert sounds.resolve_tone(cfg(), "run_completed") == "chime"
    assert sounds.resolve_tone(
        cfg({"run_completed": "pop"}), "run_completed") == "pop"
    assert sounds.resolve_tone(
        cfg({"run_failed": "soft"}), "run_failed") == "soft"


def test_resolve_master_off_silences_everything():
    for event in sounds.SOUND_EVENTS:
        assert sounds.resolve_tone(
            cfg(enabled=False), event) is None, event


def test_resolve_event_off_silences_only_that_event():
    c = cfg({"run_completed": "off"})
    assert sounds.resolve_tone(c, "run_completed") is None
    assert sounds.resolve_tone(c, "run_failed") == "low"
    assert sounds.resolve_tone(c, "confirmation_waiting") == "bell"


def test_resolve_unknown_event_is_silent():
    assert sounds.resolve_tone(cfg(), "explosion") is None


def test_resolve_default_off_events_are_silent():
    assert sounds.resolve_tone(cfg(), "app_opened") is None
    assert sounds.resolve_tone(cfg(), "command_sent") is None


# ----------------------------------------------------------------------
# Legacy boolean configs migrate instead of breaking
# ----------------------------------------------------------------------

def test_legacy_booleans_migrate():
    merged = sounds.sounds_config({"sounds": {"events": {
        "run_completed": True, "run_failed": False,
        "app_opened": True}}})
    assert merged["events"]["run_completed"] == "chime"
    assert merged["events"]["run_failed"] == "off"
    # Switched on before the upgrade — stays on, with a tone.
    assert merged["events"]["app_opened"] == "chime"


def test_unknown_stored_tone_falls_back_to_default():
    merged = sounds.sounds_config(
        {"sounds": {"events": {"run_completed": "kazoo"}}})
    assert merged["events"]["run_completed"] == "chime"


# ----------------------------------------------------------------------
# Strict validation on the write path
# ----------------------------------------------------------------------

def test_validate_accepts_tones_off_and_bools():
    sounds.validate_event_tones({"run_completed": "harp"})
    sounds.validate_event_tones({"run_completed": "off"})
    sounds.validate_event_tones({"run_completed": True})


def test_validate_rejects_invalid_tone():
    with pytest.raises(ValueError):
        sounds.validate_event_tones({"run_completed": "kazoo"})


def test_validate_rejects_unknown_event():
    with pytest.raises(ValueError):
        sounds.validate_event_tones({"explosion": "chime"})


# ----------------------------------------------------------------------
# Dispatch: emit_tone queues exactly what resolve_tone allows
# ----------------------------------------------------------------------

@pytest.fixture
def mgr(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    return server.RunManager()


def test_emit_tone_queues_resolved_tone(mgr):
    assert mgr.emit_tone("run_completed", cfg()) is True
    (entry,) = mgr.state["tone_events"]
    assert entry["event"] == "run_completed"
    assert entry["tone"] == "chime"
    assert entry["seq"] == 1
    assert entry["at"] > 0


def test_emit_tone_master_off_queues_nothing(mgr):
    assert mgr.emit_tone("run_completed",
                         cfg(enabled=False)) is False
    assert mgr.state["tone_events"] == []


def test_emit_tone_event_off_queues_nothing(mgr):
    assert mgr.emit_tone(
        "run_completed", cfg({"run_completed": "off"})) is False
    assert mgr.state["tone_events"] == []
    # …and only that event is silenced.
    assert mgr.emit_tone(
        "run_failed", cfg({"run_completed": "off"})) is True
    assert mgr.state["tone_events"][0]["tone"] == "low"


def test_confirmation_tone_fires_when_confirmation_is_asked(mgr):
    # ui_policy dispatches BEFORE it blocks for the answer.
    result = {}
    thread = threading.Thread(
        target=lambda: result.setdefault(
            "approved", mgr.ui_policy({"action": "key"}, "test step")),
        daemon=True)
    thread.start()
    # pending is set an instant before the dispatch runs; wait
    # for the dispatch itself, not just the pending record.
    deadline = time.time() + 10
    tones = []
    while time.time() < deadline:
        tones = [e for e in mgr.state["tone_events"]
                 if e["event"] == "confirmation_waiting"]
        if tones and mgr.pending is not None:
            break
        time.sleep(0.05)
    assert mgr.pending is not None
    assert len(tones) == 1
    assert tones[0]["tone"] == "bell"
    mgr.answer_confirmation(mgr.pending["id"], True)
    thread.join(timeout=10)
    assert result["approved"] is True


def test_status_payload_carries_tone_events(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    mgr = server.MANAGER
    saved_events, saved_seq = mgr.state["tone_events"], mgr._tone_seq
    try:
        mgr.state["tone_events"] = []
        assert mgr.emit_tone("run_failed", cfg()) is True
        body = TestClient(server.app).get("/api/status").json()
        assert body["state"]["tone_events"][-1]["event"] == \
            "run_failed"
        assert body["state"]["tone_events"][-1]["tone"] == "low"
    finally:
        mgr.state["tone_events"], mgr._tone_seq = \
            saved_events, saved_seq


# ----------------------------------------------------------------------
# Persistence through /api/config (atomic save), with validation
# ----------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    return TestClient(server.app)


def test_per_event_selection_persists(api):
    res = api.post("/api/config", json={"sounds": {
        "events": {"run_completed": "pop",
                   "confirmation_waiting": "off"}}})
    assert res.status_code == 200
    saved = server.load_config()
    assert saved["sounds"]["events"]["run_completed"] == "pop"
    assert saved["sounds"]["events"]["confirmation_waiting"] == "off"
    # Untouched events keep their defaults after the merge.
    assert saved["sounds"]["events"]["run_failed"] == "low"
    assert saved["sounds"]["enabled"] is True


def test_master_and_volume_persist(api):
    res = api.post("/api/config", json={
        "sounds": {"enabled": False, "volume": 25}})
    assert res.status_code == 200
    saved = server.load_config()
    assert saved["sounds"]["enabled"] is False
    assert saved["sounds"]["volume"] == 25


def test_invalid_tone_value_is_rejected(api):
    res = api.post("/api/config", json={"sounds": {
        "events": {"run_completed": "kazoo"}}})
    assert res.status_code == 400
    # Nothing was saved: the previous value stands.
    assert server.load_config()["sounds"]["events"][
        "run_completed"] == "chime"


def test_unknown_event_is_rejected(api):
    res = api.post("/api/config", json={"sounds": {
        "events": {"explosion": "chime"}}})
    assert res.status_code == 400


def test_legacy_bool_update_migrates_on_save(api):
    res = api.post("/api/config", json={"sounds": {
        "events": {"run_completed": False}}})
    assert res.status_code == 200
    assert server.load_config()["sounds"]["events"][
        "run_completed"] == "off"


# ----------------------------------------------------------------------
# Window guards: the player, the pickers, the preview, the mirror
# ----------------------------------------------------------------------

def _read_index():
    with open(INDEX) as fh:
        return fh.read()


def test_window_tone_library_mirrors_the_server():
    html = _read_index()
    js_block = re.search(r"const TONES_JS = \{(.*?)\};", html, re.S)
    assert js_block, "TONES_JS missing"
    js_ids = set(re.findall(r"^  (\w+):", js_block.group(1), re.M))
    assert js_ids == set(sounds.TONES)


def test_window_defaults_mirror_the_server():
    html = _read_index()
    for event, (_label, default) in sounds.SOUND_EVENTS.items():
        assert f'{event}: "{default}"' in html, event


def test_window_player_and_poll_consumption_present():
    html = _read_index()
    assert "function playTone(" in html
    assert "AudioContext" in html
    assert "function consumeToneEvents(" in html
    assert "st.state.tone_events" in html
    assert "consumeToneEvents(st)" in html


def test_settings_picker_and_local_preview_present():
    html = _read_index()
    # Per-event tone picker (a select, not the old checkbox)…
    assert 'class="snd-tone"' in html
    assert '"off", "Off"' in html
    # …and PLAY auditions the picked tone locally in the page.
    assert "playTone(sel.value" in html
    # The old dead path is gone from the preview.
    assert 'body: JSON.stringify({ event: t.dataset.ev })' not in html


def test_no_paplay_playback_path_remains():
    # The dead path is retired, not left as a second player:
    # no paplay helper, no server-side play() entry point.
    import deskdora.sounds as s
    assert not hasattr(s, "_paplay")
    assert not hasattr(s, "play")
