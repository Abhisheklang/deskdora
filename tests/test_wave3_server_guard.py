"""Wave 3 regression tests for vibe S3 and S4."""
import pytest
from fastapi.testclient import TestClient

from deskdora import server


@pytest.fixture(autouse=True)
def _reset_kill():
    # /api/kill trips the manager's kill switch (and its STOP
    # file); never leak that state into other tests.
    yield
    server.MANAGER.kill.reset()


client = TestClient(server.app)
remote = TestClient(server.app, client=("192.168.1.50", 50000))


def test_docs_and_openapi_not_served():
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
        assert remote.get(path).status_code == 404


def test_cross_origin_post_refused():
    res = client.post("/api/kill", headers={
        "Origin": "http://evil.example",
        "Host": "127.0.0.1:8765"})
    assert res.status_code == 403
    res = client.post("/api/queue/clear", headers={
        "Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403


def test_same_origin_and_no_origin_posts_still_work():
    # No Origin header: local processes and the desktop shell.
    assert client.post("/api/kill").status_code == 200
    # Same-origin browser request from the served UI itself.
    res = client.post("/api/kill", headers={
        "Origin": "http://testserver", "Host": "testserver"})
    assert res.status_code == 200
