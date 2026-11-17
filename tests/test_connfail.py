"""Connection-failure pop-ups (v3.3): pre-flight checks, cause
classification against a local mock provider, the pop-up payload
shape, the companion mirror via /api/status, and the hard rule —
no silent planner switch, ever."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from deskdora import connfail, server

client = TestClient(server.app)


# ----------------------------------------------------------------------
# classification
# ----------------------------------------------------------------------

@pytest.mark.parametrize("message,cause", [
    ("OpenAI request failed (HTTP 401): bad key", "invalid_key"),
    ("OpenAI request failed (HTTP 429): slow down", "rate_limited"),
    ("OpenAI request failed (HTTP 404): no model", "model_not_found"),
    ("OpenAI request failed (HTTP 503): down", "provider_error"),
    ("OpenAI unreachable or timed out: timed out", "timeout"),
    ("OpenAI unreachable or timed out: [Errno 111]", "unreachable"),
    ("no API key configured for OpenAI", "no_key"),
])
def test_classify_causes(message, cause):
    payload = connfail.classify(message, "openai:gpt-4o-mini", {})
    assert payload["cause"] == cause
    assert payload["provider_display"] == "OpenAI · gpt-4o-mini"
    assert payload["detail"] and payload["suggestion"]
    assert payload["headline"].startswith("Couldn't connect to")


def test_classify_ollama_refusal_is_ollama_down():
    payload = connfail.classify("connection refused", "ollama", {})
    assert payload["cause"] == "ollama_down"
    assert "Ollama" in payload["provider_display"]


# ----------------------------------------------------------------------
# pre-flight against a local mock provider
# ----------------------------------------------------------------------

MOCK = {"status": 401}


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        if MOCK["status"] == "sleep":
            time.sleep(8)
        body = json.dumps({
            "choices": [{"message": {"content": "[]"}}]}).encode()
        self.send_response(int(MOCK["status"])
                           if MOCK["status"] != "sleep" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def mock_url():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def cloud_cfg(url, key="sk-test"):
    return {"planner": "openai:gpt-4o-mini", "model": "qwen2.5:1.5b",
            "providers": {"openai": {"api_key": key, "base_url": url,
                                     "model": "gpt-4o-mini"}},
            "custom_providers": []}


@pytest.mark.parametrize("status,cause", [
    (401, "invalid_key"), (429, "rate_limited"),
    (404, "model_not_found"), (503, "provider_error"),
])
def test_preflight_mock_errors(mock_url, status, cause):
    MOCK["status"] = status
    payload = connfail.preflight("openai:gpt-4o-mini",
                                 cloud_cfg(mock_url))
    assert payload is not None
    assert payload["cause"] == cause, payload


def test_preflight_ok_returns_none(mock_url):
    MOCK["status"] = 200
    assert connfail.preflight("openai:gpt-4o-mini",
                              cloud_cfg(mock_url)) is None


def test_preflight_timeout(mock_url):
    MOCK["status"] = "sleep"
    payload = connfail.preflight("openai:gpt-4o-mini",
                                 cloud_cfg(mock_url), timeout=1.5)
    assert payload["cause"] == "timeout"
    MOCK["status"] = 200


def test_preflight_refused_is_unreachable():
    payload = connfail.preflight(
        "openai:gpt-4o-mini",
        cloud_cfg("http://127.0.0.1:9/v1"), timeout=3)
    assert payload["cause"] in ("unreachable", "timeout")


def test_preflight_builtin_never_fails():
    assert connfail.preflight("builtin", {}) is None
    assert connfail.preflight("auto", {}) is None


def test_preflight_ollama_down_or_model_missing():
    payload = connfail.preflight(
        "ollama", {"model": "no-such-model-xyz", "providers": {},
                   "custom_providers": []})
    assert payload is not None
    assert payload["cause"] in ("ollama_down", "model_not_found")


# ----------------------------------------------------------------------
# run start: failure surfaces, no silent switch, companion mirror
# ----------------------------------------------------------------------

def test_run_start_surfaces_failure_without_switching(monkeypatch):
    conf = dict(server.DEFAULT_CONFIG)
    conf.update({"planner": "ollama",
                 "model": "no-such-model-xyz", "providers": {},
                 "custom_providers": []})
    monkeypatch.setattr(server, "load_config", lambda: conf)
    before = server.MANAGER.state.get("planner_mode")
    res = client.post("/api/run", json={"goal": "open the terminal"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["connection_error"]["cause"] in (
        "ollama_down", "model_not_found")
    # no run started, and the planner was NOT silently switched
    assert server.MANAGER.state["running"] is False
    assert server.MANAGER.state.get("planner_mode") == before
    # the companion sees the same failure through /api/status
    st = client.get("/api/status").json()
    assert st["state"]["connection_error"]["provider"]
    assert st["state"]["connection_error"]["goal"] == "open the terminal"
