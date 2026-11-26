"""DeskDora API key + provider settings endpoints (v3.1).

The API key is verified at the ASGI level with a TestClient
presenting a non-loopback client host — the same fact the middleware
checks in production (the companion-PIN tests use the same trick).
"""
import json

import pytest
from fastapi.testclient import TestClient

from deskdora import server
from deskdora.apikey import ApiKeyStore

client = TestClient(server.app)
remote = TestClient(server.app, client=("192.168.1.50", 50000))


@pytest.fixture
def key_store(tmp_path, monkeypatch):
    store = ApiKeyStore(str(tmp_path / "api-key.json"))
    monkeypatch.setattr(server, "API_KEYS", store)
    return store


# ---------------------------------------------------------------------------
# The store itself
# ---------------------------------------------------------------------------

def test_store_generate_verify_revoke(tmp_path):
    store = ApiKeyStore(str(tmp_path / "api-key.json"))
    assert store.status()["configured"] is False
    assert store.verify("anything") is False
    key = store.generate()
    assert key.startswith("ddk_")
    assert store.verify(key) is True
    assert store.verify(key + "x") is False
    assert store.verify("") is False
    status = store.status()
    assert status["configured"] is True
    assert status["last4"] == key[-4:]
    # Only the hash is on disk — the key itself is nowhere in the file.
    assert key not in (tmp_path / "api-key.json").read_text()
    store.revoke()
    assert store.verify(key) is False
    assert store.status()["configured"] is False


def test_store_regenerate_replaces(tmp_path):
    store = ApiKeyStore(str(tmp_path / "api-key.json"))
    first = store.generate()
    second = store.generate()
    assert first != second
    assert store.verify(first) is False
    assert store.verify(second) is True


# ---------------------------------------------------------------------------
# Middleware: the 401 / 200 sequence
# ---------------------------------------------------------------------------

def test_api_key_middleware_sequence(key_store):
    # No key configured at all: remote mutation is refused.
    assert remote.post("/api/kill").status_code == 401
    # Generate through the local (loopback) API.
    res = client.post("/api/access/key/generate?confirm=true")
    assert res.status_code == 200
    key = res.json()["key"]
    assert res.json()["last4"] == key[-4:]
    # No key presented -> 401; wrong key -> 401.
    assert remote.post("/api/kill").status_code == 401
    assert remote.post(
        "/api/kill", headers={"X-Api-Key": "ddk_wrong"}).status_code == 401
    # Correct key, both header forms -> 200.
    assert remote.post(
        "/api/kill", headers={"X-Api-Key": key}).status_code == 200
    assert remote.post(
        "/api/kill",
        headers={"Authorization": f"Bearer {key}"}).status_code == 200
    # Reads were never gated.
    assert remote.get("/api/status").status_code == 200
    # Revoked key -> 401 again.
    assert client.post(
        "/api/access/key/revoke?confirm=true").status_code == 200
    assert remote.post(
        "/api/kill", headers={"X-Api-Key": key}).status_code == 401


def test_access_status_endpoint(key_store):
    res = client.get("/api/access")
    assert res.status_code == 200
    body = res.json()
    assert body["api_key"]["configured"] is False
    assert body["mcp"]["command"] == "deskdora-mcp"


# ---------------------------------------------------------------------------
# Provider settings endpoints: keys are write-only via the API
# ---------------------------------------------------------------------------

def test_provider_save_masks_key_everywhere():
    res = client.post("/api/providers/groq",
                      json={"api_key": "gsk-secret-1234",
                            "model": "llama-3.1-8b-instant"})
    assert res.status_code == 200
    body = res.json()
    assert body["has_key"] is True and body["key_last4"] == "1234"
    assert body["model"] == "llama-3.1-8b-instant"
    # The full key appears in NO api response: not providers, not
    # config, not status.
    for path in ("/api/providers", "/api/config", "/api/status"):
        text = client.get(path).text
        assert "gsk-secret-1234" not in text, path
    # Clearing works too.
    res = client.post("/api/providers/groq", json={"api_key": ""})
    assert res.json()["has_key"] is False


def test_providers_registry_endpoint():
    res = client.get("/api/providers")
    assert res.status_code == 200
    body = res.json()
    assert len(body["registry"]) >= 20
    ids = {entry["id"] for entry in body["registry"]}
    assert {"openai", "gemini", "anthropic", "azure"} <= ids
    assert body["state"]["openai"]["model"] == "gpt-4o-mini"
    res = client.post("/api/providers/nope", json={"model": "x"})
    assert res.status_code == 404
    res = client.post("/api/providers/groq",
                      json={"base_url": "ftp://bad"})
    assert res.status_code == 400


def test_provider_test_endpoint(monkeypatch):
    class FakePlanner:
        def test_connection(self):
            return True, "fake connection ok"

    monkeypatch.setattr(server, "make_provider_planner",
                        lambda pid, settings, customs=None: FakePlanner())
    res = client.post("/api/providers/groq/test")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "detail": "fake connection ok"}
    res = client.post("/api/providers/nope/test")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Custom providers CRUD
# ---------------------------------------------------------------------------

def test_custom_provider_crud():
    res = client.post("/api/custom-providers", json={
        "name": "Lab Gateway", "protocol": "carrier-pigeon",
        "base_url": "https://llm.lab.internal/v1", "model": "m1"})
    assert res.status_code == 400
    res = client.post("/api/custom-providers", json={
        "name": "Lab Gateway", "protocol": "openai",
        "base_url": "https://llm.lab.internal/v1", "model": "m1",
        "api_key": "lab-secret-9999"})
    assert res.status_code == 200
    created = res.json()
    assert created["id"].startswith("custom-lab-gateway-")
    assert created["key_last4"] == "9999"
    assert "lab-secret-9999" not in client.get("/api/providers").text
    assert "lab-secret-9999" not in client.get("/api/config").text
    cid = created["id"]
    res = client.post(f"/api/custom-providers/{cid}",
                      json={"model": "m2"})
    assert res.json()["model"] == "m2"
    res = client.delete(f"/api/custom-providers/{cid}")
    assert res.status_code == 200
    res = client.delete(f"/api/custom-providers/{cid}")
    assert res.status_code == 404
    customs = client.get("/api/providers").json()["customs"]
    assert all(c["id"] != cid for c in customs)


def test_public_config_masks_provider_keys():
    cfg = {"providers": {"openai": {"api_key": "sk-123456",
                                    "model": "gpt-4o"}},
           "custom_providers": [{"id": "custom-x-1", "api_key": "zz-9876"}]}
    public = server.public_config(cfg)
    assert public["providers"]["openai"]["key_last4"] == "3456"
    assert "api_key" not in public["providers"]["openai"]
    assert public["custom_providers"][0]["key_last4"] == "9876"
    assert "sk-123456" not in json.dumps(public)
