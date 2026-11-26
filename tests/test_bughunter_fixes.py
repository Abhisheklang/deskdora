"""Regression tests for the BugHunter audit fixes (2026-11-26).

One section per confirmed bug, in audit order.  PINs used here are
6+ characters so they stay valid under the C5 minimum-length rule.
"""
import pytest
from fastapi.testclient import TestClient

from deskdora import server

PIN = "654321"


@pytest.fixture()
def pin_set():
    """Set a known companion PIN through the config file, restore
    the previous value afterwards."""
    original = server.load_config().get("companion_pin", "")
    server.save_config({**server.load_config(), "companion_pin": PIN})
    yield PIN
    server.save_config({**server.load_config(), "companion_pin": original})


def _remote():
    return TestClient(server.app, client=("192.168.1.50", 50000))


def _local():
    return TestClient(server.app, client=("127.0.0.1", 50000))


# ---------------------------------------------------------------- C1
# The companion PIN must never appear in an API response.

def test_c1_config_and_status_hide_pin_for_remote_and_loopback(pin_set):
    for client in (_remote(), _local()):
        for path in ("/api/config", "/api/status"):
            res = client.get(path)
            assert res.status_code == 200
            assert PIN not in res.text, path
            body = res.json()
            cfg = body["config"] if path == "/api/status" else body
            assert "companion_pin" not in cfg
            assert cfg["has_pin"] is True


def test_c1_pin_still_settable_and_blank_keeps_current(pin_set):
    local = _local()
    # Setting a new PIN via POST still works.
    res = local.post("/api/config", json={"companion_pin": "987654"})
    assert res.status_code == 200
    assert server.load_config()["companion_pin"] == "987654"
    assert "987654" not in res.text
    assert res.json()["has_pin"] is True
    # A blank PIN field on save means keep the current PIN.
    res = local.post("/api/config", json={"companion_pin": ""})
    assert res.status_code == 200
    assert server.load_config()["companion_pin"] == "987654"
