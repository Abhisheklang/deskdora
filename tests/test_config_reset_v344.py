"""v3.4.4 — atomic config save + reset clears connection_error.

Two defects from the 2026-11-18 rig burst, fixed here:

1. save_config truncated config.json in place.  A failed write
   (crash, full disk, write block) left an empty file, and
   load_config's silent fallback then reset the user to defaults
   — one bad write blanked their settings.  The fix writes to a
   temp file in the same directory, fsyncs, and os.replace()s it
   onto config.json, so the file always holds a complete old or
   new document.

2. reset_state never cleared state["connection_error"], so a
   stale planner/connection failure survived a reset and kept
   showing as if it were current.  The fix clears it in the final
   state update — the single funnel every successful reset path
   (idle, killed-and-finished, killed-and-force-cleared) passes
   through.  A refused reset (409) raises before that update and
   must leave the error untouched.
"""
import json
import os

import pytest
from fastapi import HTTPException

from deskdora import server
from deskdora.safety import KillSwitch


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    return tmp_path


# ----------------------------------------------------------------------
# (1) atomic settings write
# ----------------------------------------------------------------------

def test_save_config_round_trip(config_env):
    cfg = server.load_config()
    cfg["edge_light"] = "flow"
    server.save_config(cfg)
    raw = (config_env / "config.json").read_text()
    assert json.loads(raw)["edge_light"] == "flow"
    assert server.load_config()["edge_light"] == "flow"
    # No temp litter: the directory holds only the real config.
    assert sorted(os.listdir(config_env)) == ["config.json"]


def test_failed_save_preserves_existing_config(config_env, monkeypatch):
    original = server.load_config()
    original["edge_light"] = "flow"
    server.save_config(original)
    path = config_env / "config.json"
    before = path.read_text()

    def exploding_dump(obj, fh, **kw):
        fh.write('{"edge_light": "par')  # partial junk, then die
        raise OSError("simulated mid-write failure")

    real_dump = server.json.dump
    monkeypatch.setattr(server.json, "dump", exploding_dump)
    with pytest.raises(OSError):
        server.save_config({**original, "edge_light": "off"})
    # Restore ONLY the dump patch — monkeypatch.undo() would also
    # revert the fixture's CONFIG_PATH/DATA_DIR patches.
    monkeypatch.setattr(server.json, "dump", real_dump)

    # The original document survived byte-for-byte, loads as the
    # original values, and no temp file was left behind.
    assert path.read_text() == before
    assert server.load_config()["edge_light"] == "flow"
    assert sorted(os.listdir(config_env)) == ["config.json"]

    # And saving still works afterwards.
    updated = server.load_config()
    updated["edge_light"] = "off"
    server.save_config(updated)
    assert server.load_config()["edge_light"] == "off"


# ----------------------------------------------------------------------
# (2) reset clears the stale connection error
# ----------------------------------------------------------------------

@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(server.sounds, "resolve_tone",
                        lambda *a, **k: None)
    monkeypatch.setattr(server.notify, "notify", lambda *a, **k: None)
    mgr = server.RunManager()
    mgr.kill = KillSwitch(str(tmp_path / "STOP"))
    mgr.kill_grace_seconds = 0.3
    return mgr


STALE_ERROR = {"kind": "connect", "message": "Could not connect",
               "goal": "an old goal"}


def test_reset_clears_connection_error(manager):
    manager.state["connection_error"] = dict(STALE_ERROR)
    assert manager.reset_state()["ok"] is True
    assert manager.state["connection_error"] is None


def test_reset_force_clear_path_clears_connection_error(manager):
    # A killed run whose worker never finishes: reset waits out
    # the grace period, force-clears (the v3.4.1 path), and must
    # still clear the stale error on its way through.
    manager.state["running"] = True
    manager.state["connection_error"] = dict(STALE_ERROR)
    manager.trip_kill()
    assert manager.reset_state()["ok"] is True
    assert manager.state["running"] is False
    assert manager.state["connection_error"] is None


def test_refused_reset_leaves_connection_error(manager):
    # Reset is refused for a live, unkilled run — and a refusal
    # must not touch any state, the error included.
    manager.state["running"] = True
    manager.state["connection_error"] = dict(STALE_ERROR)
    with pytest.raises(HTTPException) as excinfo:
        manager.reset_state()
    assert excinfo.value.status_code == 409
    assert manager.state["connection_error"] == STALE_ERROR
    manager.state["running"] = False
