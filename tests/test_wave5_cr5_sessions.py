"""Wave 5 regression tests for CR5: the companion_sessions table
was never pruned - expired rows accumulated for the life of the
process (the verdict probe inserted 50 expired sessions and the
store still held 50 after exercising the only read path).
Expired sessions are now dropped when an expired token is
presented and swept on every new companion login."""
import time

import pytest
from fastapi.testclient import TestClient

from deskdora import server


@pytest.fixture()
def share_cfg(monkeypatch):
    cfg = dict(server.DEFAULT_CONFIG)
    cfg.update({"companion_pin": "246810", "screen_share": True})
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr(server.MANAGER, "frame_provider",
                        lambda: (b"\xff\xd8\xff\xe0fakejpeg",
                                 "image/jpeg"))
    server.MANAGER.companion_sessions.clear()
    yield cfg
    server.MANAGER.companion_sessions.clear()


def _client():
    return TestClient(server.app)


def test_expired_session_pruned_on_read(share_cfg):
    sessions = server.MANAGER.companion_sessions
    sessions["expired-tok"] = time.time() - 1000
    client = _client()
    client.cookies.set("dd_companion_session", "expired-tok")
    assert client.get("/api/screen.mjpg").status_code == 401
    assert "expired-tok" not in sessions


def test_verdict_probe_fifty_expired_sessions_gone(share_cfg):
    # The verdict probe, re-run against the store's real paths:
    # 50 expired sessions inserted, then the store is exercised
    # (one expired-token read, one fresh login) and none remain.
    sessions = server.MANAGER.companion_sessions
    for i in range(50):
        sessions[f"tok-{i}"] = time.time() - 1000 - i
    assert len(sessions) == 50
    client = _client()
    client.cookies.set("dd_companion_session", "tok-0")
    assert client.get("/api/screen.mjpg").status_code == 401
    assert "tok-0" not in sessions
    ok = client.post("/api/companion/auth",
                     headers={"x-companion-pin": "246810"})
    assert ok.status_code == 200
    remaining_expired = [t for t, exp in sessions.items()
                         if exp <= time.time()]
    assert remaining_expired == []
    assert len(sessions) == 1   # only the fresh login's session


def test_live_session_survives_read_and_prune(share_cfg):
    sessions = server.MANAGER.companion_sessions
    sessions["live-tok"] = time.time() + 3600
    client = _client()
    client.cookies.set("dd_companion_session", "live-tok")
    with client.stream("GET", "/api/screen.mjpg") as resp:
        assert resp.status_code == 200
    assert sessions.get("live-tok", 0) > time.time()
    ok = client.post("/api/companion/auth",
                     headers={"x-companion-pin": "246810"})
    assert ok.status_code == 200
    assert sessions.get("live-tok", 0) > time.time()
