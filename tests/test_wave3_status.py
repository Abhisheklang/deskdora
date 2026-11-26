"""Wave 3 regression tests for vibe CR3 and CR4."""
from fastapi.testclient import TestClient

from deskdora import server


def test_status_caches_ollama_probe(monkeypatch):
    calls = []
    class FakePlanner:
        def __init__(self, model=None):
            pass
        def available(self):
            calls.append(1)
            return True
    monkeypatch.setattr(server, "OllamaPlanner", FakePlanner)
    server._ollama_probe_cache.update(ts=0.0, available=False)
    client = TestClient(server.app)
    assert client.get("/api/status").json()["ollama_available"] is True
    assert client.get("/api/status").json()["ollama_available"] is True
    assert len(calls) == 1  # second poll served from the cache


def test_voice_transcribe_rejects_oversized_upload(monkeypatch):
    monkeypatch.setattr(server.voice, "transcribe",
                        lambda path: "should not happen")
    client = TestClient(server.app)
    big = b"0" * (26 * 1024 * 1024)
    res = client.post("/api/voice/transcribe",
                      files={"file": ("clip.wav", big, "audio/wav")})
    assert res.status_code == 413
