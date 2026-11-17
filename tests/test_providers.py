"""Provider registry + cloud planners (v3.1).

The registry ships 20+ named presets over three wire protocols
(OpenAI-compatible chat completions, Gemini generateContent,
Anthropic messages) plus user-defined custom providers.  Everything
here runs against a LOCAL MOCK server implementing each documented
protocol — request shape, response parsing, model fetching and error
handling are verified for real; the live providers are never
contacted (no keys exist in the test environment).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deskdora.planners import (PROVIDER_REGISTRY, AnthropicPlanner,
                               GeminiPlanner, OpenAICompatiblePlanner,
                               PlanError, get_planner,
                               make_provider_planner,
                               validate_custom_provider)

PLAN_JSON = json.dumps([
    {"action": "launch", "command": "xfce4-terminal"},
    {"action": "wait", "seconds": 1},
    {"action": "done", "answer": "Terminal opened"},
])

REQUESTS = []


class MockProviderHandler(BaseHTTPRequestHandler):
    """One handler speaking all three protocols, keyed off the path."""

    def log_message(self, *args):
        pass

    def _key(self):
        return (self.headers.get("Authorization") or
                self.headers.get("X-Goog-Api-Key") or
                self.headers.get("X-Api-Key") or "")

    def _reply(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        REQUESTS.append({"path": self.path, "body": body,
                         "headers": {k.lower(): v for k, v
                                     in self.headers.items()}})
        if self._key().endswith("bad-key"):
            return self._reply(401, {"error": {
                "message": "invalid api key", "code": "bad_key"}})
        if self.path.endswith("/chat/completions"):
            return self._reply(200, {"choices": [{
                "message": {"role": "assistant", "content": PLAN_JSON}}]})
        if ":generateContent" in self.path:
            return self._reply(200, {"candidates": [{
                "content": {"parts": [{"text": PLAN_JSON}]}}]})
        if self.path.endswith("/messages"):
            return self._reply(200, {
                "content": [{"type": "text", "text": PLAN_JSON}]})
        return self._reply(404, {"error": {"message": "no such route"}})

    def do_GET(self):
        REQUESTS.append({"path": self.path, "body": None,
                         "headers": {k.lower(): v for k, v
                                     in self.headers.items()}})
        if self._key().endswith("bad-key"):
            return self._reply(401, {"error": {
                "message": "invalid api key", "code": "bad_key"}})
        if self.path == "/v1beta/models":
            return self._reply(200, {"models": [
                {"name": "models/gemini-2.5-pro"},
                {"name": "models/gemini-2.0-flash"}]})
        if self.path.endswith("/models"):
            return self._reply(200, {"data": [{"id": "model-b"},
                                               {"id": "model-a"}]})
        return self._reply(404, {"error": {"message": "no such route"}})


@pytest.fixture
def mock_api():
    REQUESTS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockProviderHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    yield {"openai_base": f"http://127.0.0.1:{port}/v1",
           "gemini_base": f"http://127.0.0.1:{port}/v1beta",
           "anthropic_base": f"http://127.0.0.1:{port}/ant"}
    server.shutdown()


class FakeObservation:
    elements = []

    def summary_for_planner(self):
        return "(no elements)"


# ---------------------------------------------------------------------------
# Registry integrity
# ---------------------------------------------------------------------------

def test_registry_has_20_plus_presets():
    assert len(PROVIDER_REGISTRY) >= 20


def test_every_preset_resolves():
    for pid, spec in PROVIDER_REGISTRY.items():
        assert spec["protocol"] in ("openai", "gemini", "anthropic",
                                    "ollama"), pid
        assert spec["base_url"].startswith("http"), pid
        assert spec["models"], pid
        assert spec["default_model"] in spec["models"], pid
        planner = make_provider_planner(pid, {})
        assert planner.model == spec["default_model"], pid
        base = getattr(planner, "base_url", None) or getattr(
            planner, "host", "")
        assert base.startswith("http"), pid


def test_registry_includes_the_named_providers():
    for pid in ("openai", "gemini", "anthropic", "deepseek", "qwen",
                "moonshot", "mistral", "groq", "together", "openrouter",
                "xai", "cohere", "perplexity", "fireworks", "cerebras",
                "azure", "ollama", "lmstudio"):
        assert pid in PROVIDER_REGISTRY, pid


# ---------------------------------------------------------------------------
# Protocol: OpenAI-compatible
# ---------------------------------------------------------------------------

def test_openai_plan_against_mock(mock_api):
    planner = OpenAICompatiblePlanner(
        "openai", "OpenAI", api_key="sk-test-123", model="gpt-4o-mini",
        base_url=mock_api["openai_base"], default_model="gpt-4o-mini")
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[-1] == {"action": "done", "answer": "Terminal opened"}
    (req,) = REQUESTS
    assert req["path"] == "/v1/chat/completions"
    assert req["headers"].get("authorization") == "Bearer sk-test-123"
    assert req["body"]["model"] == "gpt-4o-mini"
    roles = [m["role"] for m in req["body"]["messages"]]
    assert roles == ["system", "user"]
    assert "Open the terminal" in req["body"]["messages"][1]["content"]


def test_preset_plan_against_mock(mock_api):
    # A preset (DeepSeek) resolves to the OpenAI protocol + its own
    # default model, with the base URL overridden by settings.
    planner = make_provider_planner(
        "deepseek", {"api_key": "ds-test-9",
                     "base_url": mock_api["openai_base"]})
    assert isinstance(planner, OpenAICompatiblePlanner)
    assert planner.model == "deepseek-chat"
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[0]["action"] == "launch"
    (req,) = REQUESTS
    assert req["headers"].get("authorization") == "Bearer ds-test-9"
    assert req["body"]["model"] == "deepseek-chat"


def test_openai_fetch_models(mock_api):
    planner = make_provider_planner(
        "groq", {"api_key": "gsk-test",
                 "base_url": mock_api["openai_base"]})
    assert planner.fetch_models() == ["model-a", "model-b"]


# ---------------------------------------------------------------------------
# Protocol: Gemini
# ---------------------------------------------------------------------------

def test_gemini_plan_against_mock(mock_api):
    planner = GeminiPlanner(
        "gemini", "Google Gemini", api_key="gm-test-7",
        model="gemini-2.0-flash", base_url=mock_api["gemini_base"],
        default_model="gemini-2.0-flash")
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[-1]["action"] == "done"
    (req,) = REQUESTS
    assert req["path"] == ("/v1beta/models/gemini-2.0-flash"
                           ":generateContent")
    assert req["headers"].get("x-goog-api-key") == "gm-test-7"
    assert "system_instruction" in req["body"]
    assert req["body"]["contents"][0]["parts"][0]["text"].startswith(
        "Goal: Open the terminal")


def test_gemini_fetch_models(mock_api):
    planner = make_provider_planner(
        "gemini", {"api_key": "gm-test-7",
                   "base_url": mock_api["gemini_base"]})
    assert planner.fetch_models() == ["gemini-2.0-flash",
                                      "gemini-2.5-pro"]


# ---------------------------------------------------------------------------
# Protocol: Anthropic
# ---------------------------------------------------------------------------

def test_anthropic_plan_against_mock(mock_api):
    planner = AnthropicPlanner(
        "anthropic", "Anthropic Claude", api_key="sk-ant-5",
        model="claude-sonnet-4-5", base_url=mock_api["anthropic_base"],
        default_model="claude-sonnet-4-5")
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[-1]["action"] == "done"
    (req,) = REQUESTS
    assert req["path"] == "/ant/messages"
    assert req["headers"].get("x-api-key") == "sk-ant-5"
    assert req["headers"].get("anthropic-version") == "2023-06-01"
    assert isinstance(req["body"]["system"], str)
    assert req["body"]["max_tokens"] > 0
    assert req["body"]["messages"][0]["content"].startswith(
        "Goal: Open the terminal")


def test_anthropic_fetch_models(mock_api):
    planner = make_provider_planner(
        "anthropic", {"api_key": "sk-ant-5",
                      "base_url": mock_api["anthropic_base"]})
    assert planner.fetch_models() == ["model-a", "model-b"]


# ---------------------------------------------------------------------------
# Errors, keys, connection tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider,base_key", [
    ("openai", "openai_base"), ("gemini", "gemini_base"),
    ("deepseek", "openai_base"), ("anthropic", "anthropic_base"),
])
def test_provider_error_is_plan_error_without_key(mock_api, provider,
                                                  base_key):
    planner = make_provider_planner(
        provider, {"api_key": "bad-key",
                   "base_url": mock_api[base_key]})
    with pytest.raises(PlanError) as excinfo:
        planner.plan("Open the terminal", FakeObservation())
    message = str(excinfo.value)
    assert "HTTP 401" in message and "invalid api key" in message
    assert "bad-key" not in message  # the key never leaks into errors


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic",
                                      "deepseek", "groq"])
def test_missing_key_refuses_before_any_request(provider):
    planner = make_provider_planner(provider, {})
    assert planner.available() is False
    with pytest.raises(PlanError, match="no API key"):
        planner.plan("Open the terminal", FakeObservation())


def test_key_optional_local_server_needs_no_key(mock_api):
    planner = make_provider_planner(
        "lmstudio", {"base_url": mock_api["openai_base"]})
    assert planner.available() is True
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[-1]["action"] == "done"


def test_test_connection(mock_api):
    planner = make_provider_planner(
        "openai", {"api_key": "sk-test-123",
                   "base_url": mock_api["openai_base"]})
    ok, detail = planner.test_connection()
    assert ok and "OpenAI" in detail
    bad = make_provider_planner(
        "anthropic", {"api_key": "bad-key",
                      "base_url": mock_api["anthropic_base"]})
    ok, detail = bad.test_connection()
    assert not ok and "HTTP 401" in detail


def test_env_base_url_override(monkeypatch, mock_api):
    monkeypatch.setenv("DESKDORA_OPENAI_BASE_URL",
                       mock_api["openai_base"])
    planner = make_provider_planner("openai", {"api_key": "sk-test-123"})
    assert planner.base_url == mock_api["openai_base"]
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[-1]["action"] == "done"


def test_get_planner_modes():
    planner, label = get_planner(
        "gemini", providers={"gemini": {"api_key": "k",
                                        "model": "gemini-2.5-pro"}})
    assert isinstance(planner, GeminiPlanner)
    assert label == "gemini:gemini-2.5-pro"
    planner, label = get_planner("builtin")
    assert label == "builtin"
    planner, label = get_planner("ollama", model="qwen2.5:7b")
    assert label == "ollama:qwen2.5:7b"


# ---------------------------------------------------------------------------
# Custom providers (configuration only — never user code)
# ---------------------------------------------------------------------------

def test_validate_custom_provider():
    cleaned = validate_custom_provider({
        "name": "My Gateway", "protocol": "openai",
        "base_url": "https://llm.example.internal/v1/",
        "model": "my-model", "api_key": "k"})
    assert cleaned["base_url"] == "https://llm.example.internal/v1"
    for bad in ({"name": "", "protocol": "openai",
                 "base_url": "https://x", "model": "m"},
                {"name": "X", "protocol": "carrier-pigeon",
                 "base_url": "https://x", "model": "m"},
                {"name": "X", "protocol": "gemini",
                 "base_url": "ftp://x", "model": "m"},
                {"name": "X", "protocol": "anthropic",
                 "base_url": "https://x", "model": ""}):
        with pytest.raises(ValueError):
            validate_custom_provider(bad)


def test_custom_provider_plans_against_mock(mock_api):
    custom = {"id": "custom-gw-1", "name": "My Gateway",
              "protocol": "openai", "base_url": mock_api["openai_base"],
              "model": "my-model", "api_key": "gw-key"}
    planner, label = get_planner("custom-gw-1", customs=[custom])
    assert label == "custom-gw-1:my-model"
    plan = planner.plan("Open the terminal", FakeObservation())
    assert plan[-1]["action"] == "done"
    (req,) = REQUESTS
    assert req["body"]["model"] == "my-model"
    assert req["headers"].get("authorization") == "Bearer gw-key"
