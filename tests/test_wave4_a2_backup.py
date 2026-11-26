"""Wave 4 regression tests for DD-A2: backup import must be
atomic.  The whole backup is validated before anything is
applied; a bad backup changes nothing - config and routines
stay byte-identical - and the preview states plainly that
importing clears the companion PIN and stored API keys."""
import json
import os

import pytest
from fastapi.testclient import TestClient

from deskdora import backup, server
from deskdora.routines import RoutineStore
from deskdora.templates import TemplateStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    monkeypatch.setattr(server.MANAGER, "routines",
                        RoutineStore(str(tmp_path / "routines")))
    monkeypatch.setattr(server.MANAGER, "templates",
                        TemplateStore(str(tmp_path
                                            / "templates.json")))
    return TestClient(server.app)


def _state_bytes(tmp_path):
    config = tmp_path / "config.json"
    config_bytes = config.read_bytes() if config.exists() else None
    routines = {}
    root = tmp_path / "routines"
    if root.is_dir():
        for fname in sorted(os.listdir(root)):
            routines[fname] = (root / fname).read_bytes()
    return config_bytes, routines


def _seed(client):
    client.post("/api/config", json={"user_name": "Original"})
    client.post("/api/routines", json={
        "name": "Morning sort", "goal": "Sort the downloads",
        "schedule": "0 9 * * *"})


def _doc(routines, config=None):
    return {"format": "deskdora-backup", "version": 1,
            "config": config if config is not None
            else {"user_name": "Imported"},
            "routines": routines, "templates": []}


# ----------------------------------------------------------------------
# Validation happens in parse_backup, before any apply
# ----------------------------------------------------------------------

def test_parse_backup_rejects_typed_schedule():
    doc = _doc([{"name": "bad", "goal": "g", "schedule": 123}])
    with pytest.raises(backup.BackupError):
        backup.parse_backup(doc)


@pytest.mark.parametrize("routine", [
    {"name": "x", "goal": "g", "schedule": "*/0 * * * *"},
    {"name": "x", "goal": "g", "schedule": ["0 9 * * *"]},
    {"name": "x", "goal": "g", "planner_mode": 7},
    {"name": "x", "goal": "g", "goal_template": {"t": 1}},
    {"name": "x", "goal": "g", "trigger": "folder"},
    {"name": "x", "goal": "g", "params": "none"},
    {"name": "x", "goal": "g", "params": [{"default": 1}]},
    {"name": 5, "goal": "g"},
    {"name": "x", "goal": ["g"]},
])
def test_parse_backup_rejects_typed_damage(routine):
    with pytest.raises(backup.BackupError):
        backup.parse_backup(_doc([routine]))


def test_parse_backup_accepts_well_typed_backup():
    doc = _doc([{"name": "ok", "goal": "g", "schedule": "0 9 * * *",
                 "planner_mode": "", "trigger": None,
                 "goal_template": None,
                 "params": [{"name": "folder",
                             "default": "/tmp"}]}])
    parsed = backup.parse_backup(doc)
    assert parsed["routines"][0]["name"] == "ok"


def test_preview_states_secret_clearing_plainly():
    parsed = backup.parse_backup(_doc([]))
    preview = backup.preview_backup(parsed)
    assert preview["secrets_included"] is False
    assert preview["clears_secrets"] is True
    assert "companion PIN" in preview["note"]
    assert "API key" in preview["note"]


# ----------------------------------------------------------------------
# Endpoint: a bad backup changes nothing at all
# ----------------------------------------------------------------------

def test_bad_backup_import_leaves_state_byte_identical(client,
                                                       tmp_path):
    _seed(client)
    before = _state_bytes(tmp_path)
    path = tmp_path / "bad-backup.json"
    path.write_text(json.dumps(_doc([
        {"name": "fine", "goal": "g", "schedule": "0 9 * * *"},
        {"name": "bad", "goal": "g", "schedule": 123},
        {"name": "never-saved", "goal": "g"}])))
    # The audit's minimised case previewed clean (200) before the
    # fix; now both preview and import refuse it up front.
    r = client.post("/api/backup/preview", json={"path": str(path)})
    assert r.status_code == 400
    r = client.post("/api/backup/import",
                    json={"path": str(path), "confirm": True})
    assert r.status_code == 400
    assert _state_bytes(tmp_path) == before
    assert client.get("/api/config").json()["user_name"] == \
        "Original"
    names = [rt["name"] for rt in client.get("/api/routines").json()]
    assert names == ["morning-sort"]


def test_failed_apply_rolls_back_byte_identical(client, tmp_path,
                                                monkeypatch):
    _seed(client)
    before = _state_bytes(tmp_path)
    path = tmp_path / "good-backup.json"
    path.write_text(json.dumps(_doc(
        [{"name": "replacement", "goal": "Other goal"}],
        config={"user_name": "Imported"})))

    def boom(items):
        raise RuntimeError("templates store exploded")

    monkeypatch.setattr(server.MANAGER.templates, "replace_all",
                        boom)
    raw = TestClient(server.app, raise_server_exceptions=False)
    r = raw.post("/api/backup/import",
                 json={"path": str(path), "confirm": True})
    assert r.status_code == 500
    # Config and routines are exactly as before the failed import.
    assert _state_bytes(tmp_path) == before
    assert client.get("/api/config").json()["user_name"] == \
        "Original"
    names = [rt["name"] for rt in client.get("/api/routines").json()]
    assert names == ["morning-sort"]
