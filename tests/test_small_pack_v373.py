"""Small pack (v3.7.3) — global hotkey, settings backup, and
custom providers in the first-run wizard.

User orders, 2026-11-19: (1) a configurable global hotkey
summons DeskDora (default Ctrl+Alt+D) with a master toggle;
(2) settings export/import as one deskdora-backup.json —
secrets excluded, preview + confirm, a pre-import snapshot
kept; (3) the wizard's AI step can add a custom provider
through the SAME save path Settings uses.

The hotkey's platform layer is Keybinder on X11; the tests
exercise the adapter with a fake backend (this environment
has no gi at all, which is itself one of the states under
test: unavailable backend → nothing registered, no crash).
"""
import io
import json
import os
import urllib.request

import pytest
from fastapi.testclient import TestClient

from deskdora import backup, hotkey, server, validation

INDEX = os.path.join(os.path.dirname(__file__), "..", "src",
                     "deskdora", "static", "index.html")


def read_index():
    with open(INDEX) as fh:
        return fh.read()


# ----------------------------------------------------------------------
# Hotkey: combo parsing
# ----------------------------------------------------------------------

def test_parse_combo_canonicalises():
    assert hotkey.parse_combo("Ctrl+Alt+D") == "<Control><Alt>d"
    assert hotkey.parse_combo("ctrl+shift+F5") == \
        "<Control><Shift>F5"
    assert hotkey.parse_combo("Super+Space") == "<Meta>space"
    assert hotkey.parse_combo("Alt+Return") == "<Alt>Return"


@pytest.mark.parametrize("bad", ["", "   ", "D", "Ctrl+", "Ctrl+Alt",
                                 "Ctrl+Banana+D", "Hyper+D",
                                 "Ctrl+Alt+D+E"])
def test_parse_combo_rejects(bad):
    with pytest.raises(ValueError):
        hotkey.parse_combo(bad)


# ----------------------------------------------------------------------
# Hotkey: binding adapter with a fake backend
# ----------------------------------------------------------------------

class FakeBackend:
    available = True

    def __init__(self):
        self.bound = {}
        self.unbound = []

    def bind(self, accel, callback):
        self.bound[accel] = callback
        return True

    def unbind(self, accel):
        self.unbound.append(accel)
        self.bound.pop(accel, None)


def test_binding_registers_and_releases():
    backend = FakeBackend()
    binding = hotkey.HotkeyBinding(backend=backend)
    fired = []
    assert binding.bind("Ctrl+Alt+D", lambda: fired.append(1))
    assert list(backend.bound) == ["<Control><Alt>d"]
    backend.bound["<Control><Alt>d"]()
    assert fired == [1]
    binding.bind("Ctrl+Alt+E", lambda: None)
    assert backend.unbound == ["<Control><Alt>d"]
    binding.release()
    assert backend.bound == {}


def test_binding_sync_toggle_off_releases():
    backend = FakeBackend()
    binding = hotkey.HotkeyBinding(backend=backend)
    cfg = {"enabled": True, "combo": "Ctrl+Alt+D"}
    assert binding.sync(cfg, lambda: None) == "<Control><Alt>d"
    assert backend.bound
    assert binding.sync({"enabled": False,
                         "combo": "Ctrl+Alt+D"}, lambda: None) \
        is None
    assert backend.bound == {}


def test_binding_unavailable_backend_registers_nothing():
    class Dead:
        available = False

        def bind(self, accel, callback):  # pragma: no cover
            raise AssertionError("must not be called")

    binding = hotkey.HotkeyBinding(backend=Dead())
    assert binding.bind("Ctrl+Alt+D", lambda: None) is False
    assert binding.active_accel is None


def test_config_hotkey_validation_and_partial_merge(client):
    r = client.post("/api/config",
                    json={"hotkey": {"enabled": True, "combo": ""}})
    assert r.status_code == 400
    r = client.post("/api/config",
                    json={"hotkey": {"enabled": True, "combo": "D"}})
    assert r.status_code == 400
    r = client.post("/api/config", json={
        "hotkey": {"enabled": True, "combo": "Ctrl+Shift+F9"}})
    assert r.status_code == 200
    cfg = client.get("/api/config").json()
    assert cfg["hotkey"] == {"enabled": True,
                             "combo": "Ctrl+Shift+F9"}
    # Partial save (toggle only) keeps the recorded combo.
    r = client.post("/api/config",
                    json={"hotkey": {"enabled": False}})
    assert r.status_code == 200
    cfg = client.get("/api/config").json()
    assert cfg["hotkey"] == {"enabled": False,
                             "combo": "Ctrl+Shift+F9"}


# ----------------------------------------------------------------------
# Backup: fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    from deskdora.routines import RoutineStore
    from deskdora.templates import TemplateStore
    monkeypatch.setattr(server.MANAGER, "routines",
                        RoutineStore(str(tmp_path / "routines")))
    monkeypatch.setattr(server.MANAGER, "templates",
                        TemplateStore(str(tmp_path
                                            / "templates.json")))
    return TestClient(server.app)


def seed(client):
    client.post("/api/config", json={"user_name": "Original"})
    client.post("/api/providers/openai",
                json={"api_key": "sk-SECRET-KEY",
                      "model": "gpt-4o-mini"})
    client.post("/api/custom-providers", json={
        "name": "My Gateway", "protocol": "openai",
        "base_url": "https://gw.example.com/v1",
        "model": "gw-model", "api_key": "custom-SECRET"})
    client.post("/api/config", json={"companion_pin": "998877"})
    client.post("/api/routines", json={
        "name": "Morning sort", "goal": "Sort the downloads"})
    client.post("/api/templates", json={
        "name": "My template", "goal": "Do the thing"})


def test_export_excludes_all_secrets(client):
    seed(client)
    r = client.get("/api/backup/export")
    assert r.status_code == 200
    text = r.text
    assert "sk-SECRET-KEY" not in text
    assert "custom-SECRET" not in text
    assert "998877" not in text
    doc = r.json()
    assert doc["format"] == "deskdora-backup"
    assert doc["version"] == 1
    assert doc["config"]["user_name"] == "Original"
    assert doc["config"]["providers"]["openai"]["model"] == \
        "gpt-4o-mini"
    assert [rt["name"] for rt in doc["routines"]] == \
        ["morning-sort"]
    assert [t["name"] for t in doc["templates"]] == ["My template"]


def test_backup_round_trip_and_preimport_snapshot(client, tmp_path):
    seed(client)
    doc = client.get("/api/backup/export").json()
    path = tmp_path / "deskdora-backup.json"
    path.write_text(json.dumps(doc))
    # Mutate everything the backup should restore.
    client.post("/api/config", json={"user_name": "Changed"})
    client.delete("/api/routines/Morning sort?confirm=true")
    # Preview reports the change set without applying it.
    r = client.post("/api/backup/preview",
                    json={"path": str(path)})
    assert r.status_code == 200
    preview = r.json()
    assert preview["routines"] == 1 and preview["templates"] == 1
    assert preview["secrets_included"] is False
    assert client.get("/api/config").json()["user_name"] == \
        "Changed"
    # Import requires the explicit confirmation.
    r = client.post("/api/backup/import", json={"path": str(path)})
    assert r.status_code == 400
    r = client.post("/api/backup/import",
                    json={"path": str(path), "confirm": True})
    assert r.status_code == 200
    pre = r.json()["pre_import_backup"]
    assert os.path.isfile(pre)
    # The snapshot holds the PRE-import (mutated) state.
    with open(pre) as fh:
        snap = json.load(fh)
    assert snap["config"]["user_name"] == "Changed"
    # And the live state is the backup's again.
    assert client.get("/api/config").json()["user_name"] == \
        "Original"
    names = [rt["name"] for rt in
             client.get("/api/routines").json()]
    assert names == ["morning-sort"]
    names = [t["name"] for t in
             client.get("/api/templates").json()["personal"]]
    assert names == ["My template"]
    # Restore-preimport brings back the mutated state.
    client.post("/api/config", json={"user_name": "Again"})
    r = client.post("/api/backup/restore-preimport")
    assert r.status_code == 409  # DD-A3: confirmation required
    r = client.post("/api/backup/restore-preimport?confirm=true")
    assert r.status_code == 200
    assert client.get("/api/config").json()["user_name"] == \
        "Changed"


def test_backup_rejects_bad_files(client, tmp_path):
    junk = tmp_path / "junk.json"
    junk.write_text("this is not json")
    r = client.post("/api/backup/preview",
                    json={"path": str(junk)})
    assert r.status_code == 400
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"format": "something-else",
                                 "version": 1}))
    r = client.post("/api/backup/preview",
                    json={"path": str(wrong)})
    assert r.status_code == 400
    future = tmp_path / "future.json"
    future.write_text(json.dumps({"format": "deskdora-backup",
                                  "version": 99, "config": {},
                                  "routines": [],
                                  "templates": []}))
    r = client.post("/api/backup/preview",
                    json={"path": str(future)})
    assert r.status_code == 400
    r = client.post("/api/backup/preview",
                    json={"path": str(tmp_path / "missing.json")})
    assert r.status_code == 400


def test_restore_without_snapshot_is_plain_refusal(client):
    r = client.post("/api/backup/restore-preimport?confirm=true")
    assert r.status_code == 400
    assert "no pre-import backup" in r.json()["detail"]


def test_scrub_removes_keys_at_any_depth():
    cfg = {"providers": {"openai": {"api_key": "x", "model": "m"}},
           "custom_providers": [{"api_key": "y"}],
           "companion_pin": "123456", "theme": "dark"}
    out = backup.scrub_config(cfg)
    assert "api_key" not in json.dumps(out)
    assert "companion_pin" not in out
    assert out["theme"] == "dark"
    # The input is not mutated.
    assert cfg["providers"]["openai"]["api_key"] == "x"


# ----------------------------------------------------------------------
# Wizard custom providers: the shared save path
# ----------------------------------------------------------------------

def test_wizard_custom_provider_shared_path(client):
    r = client.post("/api/custom-providers", json={
        "name": "Wizard GW", "protocol": "openai",
        "base_url": "https://wiz.example.com/v1",
        "model": "wiz-model", "api_key": "wiz-SECRET"})
    assert r.status_code == 200
    created = r.json()
    assert created["id"].startswith("custom-")
    assert "wiz-SECRET" not in json.dumps(created)
    # It appears in the same providers payload the wizard reads,
    # with the key masked exactly like Settings shows it.
    payload = client.get("/api/providers").json()
    (entry,) = [c for c in payload["customs"]
                if c["id"] == created["id"]]
    assert entry["has_key"] is True
    assert entry["key_last4"] == "CRET"
    assert "wiz-SECRET" not in json.dumps(payload)
    assert "wiz-SECRET" not in client.get("/api/config").text
    # And it can be saved as the active planner through the
    # custom update endpoint the wizard's flush uses.
    r = client.post("/api/config",
                    json={"planner": created["id"]})
    assert r.status_code == 200


def test_wizard_custom_provider_bad_address_rejected(client):
    r = client.post("/api/custom-providers", json={
        "name": "Bad", "protocol": "openai",
        "base_url": "not-a-url", "model": "m", "api_key": "k"})
    assert r.status_code == 400
    assert "http" in r.json()["detail"]


def test_saved_custom_is_supported_by_validation(client,
                                                  monkeypatch):
    r = client.post("/api/custom-providers", json={
        "name": "Valid GW", "protocol": "openai",
        "base_url": "http://127.0.0.1:9/v1",
        "model": "gw-model", "api_key": "k"})
    cid = r.json()["id"]
    cfg = server.load_config()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        return Resp(json.dumps(
            {"data": [{"id": "gw-model"}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    # Supported + model listed → validates clean (None), never
    # provider_unsupported.
    assert validation.validate_planner(cid, cfg) is None


# ----------------------------------------------------------------------
# UI source guards
# ----------------------------------------------------------------------

def test_ui_guards_hotkey_backup_wizard_custom():
    html = read_index()
    for needle in ('id="hkEnabled"', 'id="hkCombo"',
                   'id="hkRecord"', "hkRecording",
                   "Wayland, global hotkeys are restricted",
                   'id="bkPath"', 'id="bkPreviewBtn"',
                   'id="bkImportBtn"', 'id="bkRestoreBtn"',
                   "/api/backup/export", "/api/backup/preview",
                   "/api/backup/import",
                   "/api/backup/restore-preimport",
                   "API keys and the companion PIN are NOT "
                   "included",
                   'id="wizCustomBlock"', 'id="wizCustName"',
                   'id="wizCustProtocol"', 'id="wizCustBase"',
                   'id="wizCustKey"', 'id="wizCustModel"',
                   'id="wizCustAdd"', "wizAddCustom",
                   '"/api/custom-providers/" + pid',
                   "Custom — your providers",
                   "in the list?", "Add its API address"):
        assert needle in html, needle
