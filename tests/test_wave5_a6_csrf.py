"""Wave 5 regression tests for DD-A6: the CSRF guard exempted
Origin: null, so an opaque-origin page (sandboxed iframe, file://)
in a Fetch-Metadata-less engine could fire state-changing requests.
Origin: null is now refused as cross-origin, while requests with
no Origin header at all (desktop shell, curl, local processes)
behave exactly as before."""
import pytest
from fastapi.testclient import TestClient

from deskdora import server


@pytest.fixture(autouse=True)
def _reset_kill():
    yield
    server.MANAGER.kill.reset()


client = TestClient(server.app)


def test_origin_null_kill_refused():
    res = client.post("/api/kill", headers={"Origin": "null"})
    assert res.status_code == 403
    assert not server.MANAGER.kill.is_tripped()


def test_origin_null_key_generate_refused_and_key_unchanged():
    res = client.post("/api/access/key/generate?confirm=true",
                      headers={"Origin": "null"})
    assert res.status_code == 403
    assert "key" not in res.json()


def test_origin_null_with_cross_site_still_refused():
    res = client.post("/api/kill", headers={
        "Origin": "null", "Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403
    assert not server.MANAGER.kill.is_tripped()


def test_foreign_origin_still_refused():
    res = client.post("/api/kill", headers={
        "Origin": "https://evil.example", "Host": "127.0.0.1:8765"})
    assert res.status_code == 403
    assert not server.MANAGER.kill.is_tripped()


def test_no_origin_still_works():
    # The desktop shell, curl and local processes send no Origin
    # header at all: unchanged behaviour (DD-A6 hard requirement).
    res = client.post("/api/kill")
    assert res.status_code == 200
    assert server.MANAGER.kill.is_tripped()


def test_same_origin_still_works():
    res = client.post("/api/kill", headers={
        "Origin": "http://testserver", "Host": "testserver"})
    assert res.status_code == 200
