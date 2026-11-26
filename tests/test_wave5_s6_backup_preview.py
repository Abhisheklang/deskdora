"""Wave 5 regression tests for S6: backup preview (and import)
answered four distinguishable questions about an arbitrary path -
200 parsed preview, "isn't valid JSON", "format stamp doesn't
match", "No file exists at..." - a path-existence oracle.  All
read failures now return one uniform 400, while a valid backup
still previews with full detail."""
import json

import pytest
from fastapi.testclient import TestClient

from deskdora import server
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
                        TemplateStore(str(tmp_path / "templates.json")))
    return TestClient(server.app)


UNIFORM = "That path can't be used as a backup."


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_preview_errors_are_uniform(client, tmp_path):
    not_json = _write(tmp_path, "notjson.txt", "this is not json")
    wrong = _write(tmp_path, "other.json",
                   json.dumps({"format": "something-else",
                               "version": 1}))
    bad_shape = _write(tmp_path, "badshape.json", json.dumps(
        {"format": "deskdora-backup", "version": 1,
         "config": {}, "routines": [{"name": "x", "goal": "g",
                                     "schedule": 123}],
         "templates": []}))
    missing = str(tmp_path / "does-not-exist-xyz.json")
    answers = set()
    for path in (not_json, wrong, bad_shape, missing):
        r = client.post("/api/backup/preview", json={"path": path})
        assert r.status_code == 400
        answers.add(r.json()["detail"])
    # One indistinguishable answer for every failure shape, and it
    # never echoes the submitted path back.
    assert answers == {UNIFORM}
    for path in (not_json, wrong, bad_shape, missing):
        r = client.post("/api/backup/preview", json={"path": path})
        assert path not in r.json()["detail"]


def test_import_errors_are_uniform_too(client, tmp_path):
    missing = str(tmp_path / "does-not-exist-xyz.json")
    r = client.post("/api/backup/import",
                    json={"path": missing, "confirm": True})
    assert r.status_code == 400
    assert r.json()["detail"] == UNIFORM


def test_valid_backup_preview_still_detailed(client, tmp_path):
    path = _write(tmp_path, "valid.json", json.dumps(
        {"format": "deskdora-backup", "version": 1,
         "config": {"user_name": "Original"}, "routines": [],
         "templates": []}))
    r = client.post("/api/backup/preview", json={"path": path})
    assert r.status_code == 200
    body = r.json()
    assert body["routines"] == 0 and body["templates"] == 0
    assert body["clears_secrets"] is True
    assert "companion PIN" in body["note"]


def test_empty_path_still_prompts(client):
    r = client.post("/api/backup/preview", json={"path": "  "})
    assert r.status_code == 400
    assert "Enter the path" in r.json()["detail"]
