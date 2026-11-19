"""v3.7.0 — provider/model error page.

User order, 2026-11-19: "If model or api key isn't supported or
installed.. it should show a error page."

Pinned here:
* one test per definitive failure class producing the error-page
  payload (provider_unsupported, model_unsupported,
  model_not_installed for Ollama and for LM Studio, key_missing,
  key_rejected) — all against a fake provider transport, no live
  network;
* unreachable (and other non-definitive answers) is NOT the error
  page: validation returns None and a run still lands on the
  v3.3 connection pop-up path;
* the fix actions: the Ollama download reuses the pull endpoint,
  a model switch through the providers endpoint persists through
  save/load and then validates clean, and "check again"
  (POST /api/planner/validate) clears the page state on success
  and keeps it while the problem stands;
* the hard rule: after an error, the config file is byte-for-byte
  identical — no silent fallback to another provider or model.
"""
import io
import json
import os
import urllib.error

import pytest
from fastapi.testclient import TestClient

from deskdora import server, validation

ROOT = os.path.join(os.path.dirname(__file__), "..")
STATIC = os.path.join(ROOT, "src", "deskdora", "static")

client = TestClient(server.app)


# ----------------------------------------------------------------------
# fake provider transport (monkeypatched urllib.request.urlopen)
# ----------------------------------------------------------------------

class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return self._data


def _tags(names):
    return lambda req: _Resp({"models": [{"name": n} for n in names]})


def _models(ids):
    return lambda req: _Resp({"data": [{"id": i} for i in ids]})


def _http_error(code, message="nope"):
    def handler(req):
        raise urllib.error.HTTPError(
            req.full_url, code, "Error", None,
            io.BytesIO(json.dumps(
                {"error": {"message": message}}).encode()))
    return handler


def _unreachable(req):
    raise urllib.error.URLError("Connection refused")


def install_fake(monkeypatch, routes):
    """routes: [(url_suffix, handler)].  Returns the call list.
    An unrouted URL is a test bug, not an unreachable provider."""
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        for suffix, handler in routes:
            if req.full_url.endswith(suffix):
                return handler(req)
        raise AssertionError(f"unexpected request: {req.full_url}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


def cloud_cfg(pid="openai", model="gpt-4o-mini", key="sk-test",
              base="http://127.0.0.1:9/v1"):
    stored = {"model": model, "base_url": base}
    if key is not None:
        stored["api_key"] = key
    return {"planner": pid, "model": "qwen2.5:1.5b",
            "providers": {pid: stored}, "custom_providers": []}


# ----------------------------------------------------------------------
# (a) provider not supported
# ----------------------------------------------------------------------

def test_provider_unsupported(monkeypatch):
    calls = install_fake(monkeypatch, [])
    payload = validation.validate_planner("nosuch-provider", cloud_cfg())
    assert payload["class"] == "provider_unsupported"
    assert payload["provider"] == "nosuch-provider"
    assert payload["can_download"] is False
    assert calls == []  # decided locally — no network


def test_provider_unsupported_label_form():
    payload = validation.validate_planner(
        "nosuch-provider:some-model", cloud_cfg())
    assert payload["class"] == "provider_unsupported"
    assert payload["model"] == "some-model"


def test_auto_and_builtin_skip_validation(monkeypatch):
    calls = install_fake(monkeypatch, [])
    junk = {"planner": "auto", "providers": {}, "custom_providers": []}
    assert validation.validate_planner("auto", junk) is None
    assert validation.validate_planner("builtin", junk) is None
    assert calls == []


# ----------------------------------------------------------------------
# (b) model not supported (the provider's own live list)
# ----------------------------------------------------------------------

def test_model_unsupported_cloud(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _models(["gpt-4o-mini", "gpt-4o"]))])
    payload = validation.validate_planner(
        "openai", cloud_cfg(model="gpt-9-ultra"))
    assert payload["class"] == "model_unsupported"
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-9-ultra"
    assert payload["models"] == ["gpt-4o", "gpt-4o-mini"]  # sorted
    assert payload["can_download"] is False
    assert "gpt-9-ultra" in payload["detail"]


def test_model_in_live_list_validates(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _models(["gpt-4o-mini", "gpt-4o"]))])
    assert validation.validate_planner(
        "openai", cloud_cfg(model="gpt-4o")) is None


def test_custom_provider_model_unsupported(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _models(["llama-3.3-70b"]))])
    cfg = {"planner": "custom-mine", "providers": {},
           "custom_providers": [{
               "id": "custom-mine", "name": "My Gateway",
               "protocol": "openai",
               "base_url": "http://127.0.0.1:9/v1",
               "model": "not-a-model", "api_key": "k"}]}
    payload = validation.validate_planner("custom-mine", cfg)
    assert payload["class"] == "model_unsupported"
    assert payload["provider"] == "custom-mine"
    assert payload["models"] == ["llama-3.3-70b"]


# ----------------------------------------------------------------------
# (c) model not installed (local servers)
# ----------------------------------------------------------------------

def test_model_not_installed_ollama(monkeypatch):
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["llama3.1:8b"]))])
    cfg = {"planner": "ollama", "model": "qwen2.5:7b",
           "providers": {}, "custom_providers": []}
    payload = validation.validate_planner("ollama", cfg)
    assert payload["class"] == "model_not_installed"
    assert payload["provider"] == "ollama"
    assert payload["model"] == "qwen2.5:7b"
    assert payload["can_download"] is True
    assert payload["models"] == ["llama3.1:8b"]


def test_ollama_installed_validates(monkeypatch):
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["qwen2.5:7b", "llama3.1:8b"]))])
    cfg = {"planner": "ollama", "model": "qwen2.5:7b",
           "providers": {}, "custom_providers": []}
    assert validation.validate_planner("ollama", cfg) is None
    # Same matching as OllamaPlanner.available(): the base name
    # counts, so a different tag of the same model is installed.
    cfg["model"] = "qwen2.5:1.5b"
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["qwen2.5:latest"]))])
    assert validation.validate_planner("ollama", cfg) is None


def test_model_not_installed_lmstudio(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _models(["llama-3.1-8b-instruct"]))])
    cfg = {"planner": "lmstudio", "model": "qwen2.5:1.5b",
           "providers": {"lmstudio": {
               "base_url": "http://127.0.0.1:9/v1"}},
           "custom_providers": []}
    payload = validation.validate_planner("lmstudio", cfg)
    assert payload["class"] == "model_not_installed"
    assert payload["provider"] == "lmstudio"
    assert payload["model"] == "qwen2.5-7b-instruct"  # preset default
    assert payload["can_download"] is False
    assert payload["models"] == ["llama-3.1-8b-instruct"]


# ----------------------------------------------------------------------
# (d) API key missing or rejected
# ----------------------------------------------------------------------

def test_key_missing_no_network(monkeypatch):
    calls = install_fake(monkeypatch, [])
    payload = validation.validate_planner(
        "openai", cloud_cfg(key=None))
    assert payload["class"] == "key_missing"
    assert payload["provider"] == "openai"
    assert calls == []  # decided locally — no network


def test_key_rejected_openai_401(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _http_error(401, "bad key"))])
    payload = validation.validate_planner("openai", cloud_cfg())
    assert payload["class"] == "key_rejected"
    assert payload["provider"] == "openai"


def test_key_rejected_anthropic_403(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _http_error(403, "forbidden"))])
    payload = validation.validate_planner(
        "anthropic", cloud_cfg(pid="anthropic",
                               model="claude-sonnet-4-5"))
    assert payload["class"] == "key_rejected"
    assert payload["provider"] == "anthropic"


# ----------------------------------------------------------------------
# unreachable is NOT the error page
# ----------------------------------------------------------------------

def test_cloud_unreachable_is_not_error_page(monkeypatch):
    install_fake(monkeypatch, [("/models", _unreachable)])
    assert validation.validate_planner("openai", cloud_cfg()) is None


def test_cloud_server_error_is_not_error_page(monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _http_error(503, "down"))])
    assert validation.validate_planner("openai", cloud_cfg()) is None


def test_ollama_unreachable_is_not_error_page(monkeypatch):
    install_fake(monkeypatch, [("/api/tags", _unreachable)])
    cfg = {"planner": "ollama", "model": "qwen2.5:7b",
           "providers": {}, "custom_providers": []}
    assert validation.validate_planner("ollama", cfg) is None


def test_lmstudio_unreachable_is_not_error_page(monkeypatch):
    install_fake(monkeypatch, [("/models", _unreachable)])
    cfg = {"planner": "lmstudio", "model": "qwen2.5:1.5b",
           "providers": {"lmstudio": {
               "base_url": "http://127.0.0.1:9/v1"}},
           "custom_providers": []}
    assert validation.validate_planner("lmstudio", cfg) is None


# ----------------------------------------------------------------------
# server integration: state, routing, the hard rule
# ----------------------------------------------------------------------

@pytest.fixture
def config_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    st = server.MANAGER.state
    st.update({"running": False, "planner_error": None,
               "connection_error": None, "goal": "", "result": None})
    return tmp_path


def _write_config(config_env, data):
    (config_env / "config.json").write_text(json.dumps(data))


def test_run_blocked_by_error_page(config_env):
    _write_config(config_env, {"planner": "nosuch-provider"})
    out = server.MANAGER.start_run("open the terminal")
    assert out["ok"] is False
    assert out["planner_error"]["class"] == "provider_unsupported"
    st = server.MANAGER.state
    assert st["running"] is False
    assert st["planner_error"]["class"] == "provider_unsupported"
    assert st["planner_error"]["goal"] == "open the terminal"
    assert st["connection_error"] is None


def test_run_unreachable_stays_on_popup_path(config_env, monkeypatch):
    install_fake(monkeypatch, [("/api/tags", _unreachable)])
    _write_config(config_env, {"planner": "ollama",
                               "model": "qwen2.5:7b"})
    out = server.MANAGER.start_run("open the terminal")
    assert out["ok"] is False
    assert "connection_error" in out
    assert out["connection_error"]["cause"] == "ollama_down"
    st = server.MANAGER.state
    assert st["planner_error"] is None
    assert st["connection_error"]["cause"] == "ollama_down"
    assert st["running"] is False


def test_config_byte_identical_after_error(config_env, monkeypatch):
    install_fake(monkeypatch, [
        ("/models", _models(["gpt-4o-mini"]))])
    _write_config(config_env, {
        "planner": "openai",
        "providers": {"openai": {
            "api_key": "sk-test", "model": "gpt-9-ultra",
            "base_url": "http://127.0.0.1:9/v1"}}})
    before = (config_env / "config.json").read_bytes()
    out = server.MANAGER.start_run("open the terminal")
    assert out["ok"] is False
    assert out["planner_error"]["class"] == "model_unsupported"
    # The hard rule: the error changed NOTHING on disk — the
    # configured planner and model stand exactly as they were.
    assert (config_env / "config.json").read_bytes() == before
    cfg = server.load_config()
    assert cfg["planner"] == "openai"
    assert cfg["providers"]["openai"]["model"] == "gpt-9-ultra"


def test_reset_clears_planner_error(config_env):
    server.MANAGER.state["planner_error"] = {
        "class": "key_missing", "provider": "openai"}
    server.MANAGER.reset_state()
    assert server.MANAGER.state["planner_error"] is None


# ----------------------------------------------------------------------
# fix actions
# ----------------------------------------------------------------------

def test_model_switch_fix_persists_and_validates(config_env,
                                                 monkeypatch):
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["qwen2.5:1.5b"]))])
    _write_config(config_env, {"planner": "ollama",
                               "model": "missing:7b"})
    cfg = server.load_config()
    payload = validation.validate_planner("ollama", cfg)
    assert payload["class"] == "model_not_installed"
    # The page's fix: pick an installed model, saved through the
    # same providers endpoint Settings uses.
    server.providers_save(
        "ollama", server.ProviderSaveRequest(model="qwen2.5:1.5b"))
    cfg = server.load_config()
    assert cfg["providers"]["ollama"]["model"] == "qwen2.5:1.5b"
    assert validation.validate_planner("ollama", cfg) is None


def test_check_again_clears_after_fix(config_env, monkeypatch):
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["qwen2.5:1.5b"]))])
    _write_config(config_env, {"planner": "ollama",
                               "model": "missing:7b"})
    out = server.MANAGER.start_run("open the terminal")
    assert out["planner_error"]["class"] == "model_not_installed"
    server.providers_save(
        "ollama", server.ProviderSaveRequest(model="qwen2.5:1.5b"))
    res = client.post("/api/planner/validate",
                      json={"planner": "ollama"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["planner_error"] is None
    assert server.MANAGER.state["planner_error"] is None
    st = client.get("/api/status").json()
    assert st["state"]["planner_error"] is None


def test_check_again_keeps_page_while_broken(config_env, monkeypatch):
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["qwen2.5:1.5b"]))])
    _write_config(config_env, {"planner": "ollama",
                               "model": "missing:7b"})
    res = client.post("/api/planner/validate",
                      json={"planner": "ollama"})
    body = res.json()
    assert body["ok"] is False
    assert body["planner_error"]["class"] == "model_not_installed"
    assert server.MANAGER.state["planner_error"]["class"] == \
        "model_not_installed"


class _FakePullResponse:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_download_fix_action_uses_pull_endpoint(config_env,
                                                monkeypatch):
    # The page's Download button streams from the same pull
    # endpoint the wizard uses — prove the payload offers it and
    # the endpoint streams a successful pull for that model.
    install_fake(monkeypatch, [
        ("/api/tags", _tags(["llama3.1:8b"]))])
    _write_config(config_env, {"planner": "ollama",
                               "model": "qwen2.5:7b"})
    payload = validation.validate_planner(
        "ollama", server.load_config())
    assert payload["can_download"] is True

    lines = [b'{"status":"pulling manifest"}\n',
             b'{"status":"downloading","total":100,"completed":100}\n',
             b'{"status":"success"}\n']

    def fake_pull(req, timeout=None):
        assert json.loads(req.data.decode())["model"] == "qwen2.5:7b"
        return _FakePullResponse(lines)

    monkeypatch.setattr("urllib.request.urlopen", fake_pull)
    res = client.post("/api/providers/ollama/pull",
                      json={"model": payload["model"]})
    assert res.status_code == 200
    events = [json.loads(l) for l in res.text.splitlines() if l.strip()]
    # v3.7.2: events are enriched with whole-model aggregates.
    assert events[-1]["status"] == "success"
    assert "percent" in events[-1]


# ----------------------------------------------------------------------
# shipped wiring (source guards, same approach as v3.5/v3.6)
# ----------------------------------------------------------------------

def _read(*parts):
    with open(os.path.join(*parts)) as fh:
        return fh.read()


def test_error_page_markup_present():
    text = _read(STATIC, "index.html")
    assert 'id="plannerError"' in text
    for el in ("perrTitle", "perrBody", "perrConfig", "perrClass",
               "perrModelRow", "perrModel", "perrUseModel",
               "perrDownloadBlock", "perrDownload", "perrCheck",
               "perrSettings", "perrDismiss", "perrFixMsg"):
        assert f'id="{el}"' in text, el
    # Theme tokens only — the page is correct in both themes.
    assert "#plannerError{position:fixed" in text
    assert "background:var(--void)" in text


def test_error_page_logic_present():
    text = _read(STATIC, "index.html")
    assert "function renderPlannerError(" in text
    assert "st.state.planner_error" in text
    assert '"/api/planner/validate"' in text
    # The download fix streams from the pull endpoint, and the
    # settings fix lands on the AI Providers section.
    assert "perrPullModel(" in text
    assert '"/api/providers/ollama/pull"' in text
    assert 'id="settingsProviders"' in text


def test_brand_documents_error_page():
    brand = _read(ROOT, "BRAND.md")
    assert "Planner error page" in brand
