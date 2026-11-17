"""Features panel (v3.2): master switches must stop the machinery,
not just the UI — companion routes refuse, MCP runs are refused,
API-key auth dies, the watcher stops scanning, settings persist."""
import pytest
from fastapi.testclient import TestClient

from deskdora import server
from deskdora.apikey import ApiKeyStore
from deskdora.overlay import overlay_enabled

client = TestClient(server.app)
FEATURE_KEYS = ("feature_overlay", "feature_companion", "feature_mcp",
                "feature_api", "feature_animations",
                "proposals_enabled")


@pytest.fixture()
def cfg(monkeypatch):
    conf = dict(server.DEFAULT_CONFIG)
    conf.update({"companion_pin": "2468", "screen_share": True})
    monkeypatch.setattr(server, "load_config", lambda: conf)
    monkeypatch.setattr(server.MANAGER, "frame_provider",
                        lambda: (b"\xff\xd8\xff\xe0fakejpeg",
                                 "image/jpeg"))
    return conf


def test_feature_defaults_on(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    fresh = server.load_config()
    for key in FEATURE_KEYS:
        assert fresh[key] is True, key
    assert fresh["screen_share"] is False     # share stays opt-in
    assert fresh["edge_light"] == "single"


def test_feature_toggle_persists(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(server, "CONFIG_PATH", str(path))
    body = client.post("/api/config",
                       json={"feature_mcp": False}).json()
    assert body["feature_mcp"] is False
    assert server.load_config()["feature_mcp"] is False


def test_companion_off_refuses_page_and_endpoints(cfg):
    assert client.get("/companion.html").status_code == 200
    cfg["feature_companion"] = False
    assert client.get("/companion.html").status_code == 403
    assert client.post("/api/companion/auth",
                       headers={"x-companion-pin": "2468"}
                       ).status_code == 403
    assert client.get("/api/screen.mjpg",
                      headers={"x-companion-pin": "2468"}
                      ).status_code == 403


def test_mcp_off_refuses_mcp_runs_only(cfg):
    cfg["feature_mcp"] = False
    res = client.post("/api/run", json={"goal": "anything"},
                      headers={"x-deskdora-client": "mcp"})
    assert res.status_code == 403
    # the desktop UI's own runs are not the MCP server's business
    res = client.post("/api/run", json={"goal": ""},
                      headers={"x-deskdora-client": "ui"})
    assert res.status_code == 400
    assert client.get("/api/mcp").json()["enabled"] is False


def test_api_off_kills_api_key_auth(cfg, tmp_path, monkeypatch):
    store = ApiKeyStore(str(tmp_path / "keys.json"))
    secret = store.generate()
    monkeypatch.setattr(server, "API_KEYS", store)
    with client.stream("GET", "/api/screen.mjpg",
                       headers={"x-api-key": secret}) as resp:
        assert resp.status_code == 200      # key works when API on
    cfg["feature_api"] = False
    assert client.get("/api/screen.mjpg",
                      headers={"x-api-key": secret}
                      ).status_code == 401  # and dies when off


def test_proposals_off_stops_watcher_scan(cfg, monkeypatch):
    calls = []
    monkeypatch.setattr(server.MANAGER.watcher, "scan",
                        lambda folders: calls.append(folders))
    cfg["watch_folders"] = ["/tmp"]
    server._watcher_tick()
    assert calls == [["/tmp"]]
    cfg["proposals_enabled"] = False
    server._watcher_tick()
    assert calls == [["/tmp"]]              # no second scan


def test_overlay_master_switch_helper():
    assert overlay_enabled({}) is True
    assert overlay_enabled({"feature_overlay": True}) is True
    assert overlay_enabled({"feature_overlay": False}) is False
    assert overlay_enabled(None) is True
