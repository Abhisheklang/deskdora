"""Wave 3 regression tests for vibe S5: destructive endpoints
require an explicit confirm=true, not just transport auth."""
from fastapi.testclient import TestClient

from deskdora import server

client = TestClient(server.app)


def test_destructive_endpoints_refuse_without_confirm():
    assert client.post("/api/memory/clear").status_code == 409
    assert client.post("/api/queue/clear").status_code == 409
    assert client.delete("/api/routines/nope").status_code == 409
    assert client.delete("/api/templates/nope").status_code == 409
    assert client.delete("/api/runs/nope").status_code == 409
    assert client.post("/api/runs/nope/undo").status_code == 409


def test_destructive_endpoints_proceed_with_confirm():
    assert client.post("/api/memory/clear?confirm=true").status_code == 200
    assert client.post("/api/queue/clear?confirm=true").status_code == 200
    # With confirmation given, a missing object is a plain 404.
    assert client.delete("/api/routines/nope?confirm=true").status_code == 404
    assert client.delete("/api/templates/nope?confirm=true").status_code == 404
