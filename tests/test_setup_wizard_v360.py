"""v3.6.0 — first-run setup wizard.

User order, 2026-11-19: "When opening app for first time it
should ask some questions like choose theme, your name, pakage
download, etc...."

Pinned here:
* first-run detection — a config with no completed-setup marker
  requires the wizard (fresh installs AND every pre-v3.6
  install); a completed one never does;
* the skip path — one write of setup_complete, defaults standing
  for everything not chosen;
* the finish path — name (trimmed, blank allowed), theme and
  provider choices persist through the atomic save/load path,
  and the provider API key is stored masked like everywhere else;
* re-running setup from Settings never un-completes the marker;
* the Ollama model download streams real pull progress events
  and fails as a PlanError (surfaced by the endpoint as a final
  {"error": ...} event), never a hang;
* the shipped wiring is present in the UI files.
"""
import io
import json
import os
import urllib.error

import pytest
from fastapi import HTTPException

from deskdora import planners, server

ROOT = os.path.join(os.path.dirname(__file__), "..")
STATIC = os.path.join(ROOT, "src", "deskdora", "static")


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    return tmp_path


def _read(*parts):
    with open(os.path.join(*parts)) as fh:
        return fh.read()


# ----------------------------------------------------------------------
# first-run detection
# ----------------------------------------------------------------------

def test_setup_required_by_default(config_env):
    assert server.DEFAULT_CONFIG["setup_complete"] is False
    assert server.DEFAULT_CONFIG["user_name"] == ""
    # No config file at all: the wizard must run.
    assert server.setup_required(server.load_config()) is True
    assert server.setup_required({}) is True
    assert server.setup_required(None) is True
    assert server.setup_required({"setup_complete": True}) is False
    assert server.setup_required({"setup_complete": False}) is True


def test_existing_config_without_marker_requires_setup(config_env):
    # Every pre-v3.6 install has a config.json with no marker; it
    # sees the wizard exactly once, and its settings survive.
    (config_env / "config.json").write_text(json.dumps(
        {"planner": "builtin", "theme": "dark"}))
    cfg = server.load_config()
    assert server.setup_required(cfg) is True
    assert cfg["theme"] == "dark"
    assert cfg["user_name"] == ""


def test_config_exposes_the_marker(config_env):
    # The wizard gate rides the same payload the UI already reads
    # (public_config also feeds /api/status verbatim).
    assert server.config_get()["setup_complete"] is False
    assert server.config_get()["user_name"] == ""


# ----------------------------------------------------------------------
# skip path
# ----------------------------------------------------------------------

def test_skip_path_sets_flag_with_defaults(config_env):
    # Skipping writes exactly one thing: the marker.  Everything
    # not chosen keeps its default.
    out = server.config_post({"setup_complete": True})
    assert out["setup_complete"] is True
    cfg = server.load_config()
    assert server.setup_required(cfg) is False
    assert cfg["theme"] == "light"          # standing default
    assert cfg["user_name"] == ""
    assert cfg["planner"] == "auto"
    raw = json.loads((config_env / "config.json").read_text())
    assert raw["setup_complete"] is True


def test_skip_keeps_choices_already_made(config_env):
    # The theme step persists the moment it is tapped, so a skip
    # afterwards keeps the choice instead of resetting it.
    server.config_post({"theme": "dark"})
    server.config_post({"setup_complete": True})
    cfg = server.load_config()
    assert cfg["theme"] == "dark"
    assert server.setup_required(cfg) is False


# ----------------------------------------------------------------------
# finish path
# ----------------------------------------------------------------------

def test_finish_path_persists_name_theme_provider(config_env):
    server.config_post({"user_name": "  Ada Lovelace  ",
                        "theme": "dark"})
    server.providers_save(
        "openai", server.ProviderSaveRequest(
            api_key="sk-test-1234567890", model="gpt-4o"))
    server.config_post({"planner": "openai", "setup_complete": True})

    cfg = server.load_config()
    assert server.setup_required(cfg) is False
    assert cfg["user_name"] == "Ada Lovelace"     # stored trimmed
    assert cfg["theme"] == "dark"
    assert cfg["planner"] == "openai"
    assert cfg["providers"]["openai"]["model"] == "gpt-4o"
    assert cfg["providers"]["openai"]["api_key"] == "sk-test-1234567890"


def test_finish_path_key_never_leaks_through_the_api(config_env):
    server.providers_save(
        "openai", server.ProviderSaveRequest(
            api_key="sk-test-1234567890", model="gpt-4o"))
    shown = server.config_get()
    stored = shown["providers"]["openai"]
    assert stored["has_key"] is True
    assert stored["key_last4"] == "7890"
    assert "api_key" not in stored
    assert "sk-test-1234567890" not in json.dumps(shown)


def test_blank_name_is_allowed(config_env):
    out = server.config_post({"user_name": "   "})
    assert out["user_name"] == ""
    out = server.config_post({"user_name": ""})
    assert out["user_name"] == ""
    assert server.load_config()["user_name"] == ""


def test_user_name_must_be_text(config_env):
    server.config_post({"user_name": "Ada"})
    with pytest.raises(HTTPException) as excinfo:
        server.config_post({"user_name": 42})
    assert excinfo.value.status_code == 400
    # The refused write never landed.
    assert server.load_config()["user_name"] == "Ada"


def test_setup_complete_must_be_a_bool(config_env):
    with pytest.raises(HTTPException) as excinfo:
        server.config_post({"setup_complete": "yes"})
    assert excinfo.value.status_code == 400
    assert server.setup_required(server.load_config()) is True


def test_run_again_never_uncompletes_the_marker(config_env):
    # Settings → RUN SETUP AGAIN re-opens the wizard without
    # touching the flag; finishing again keeps it set.
    server.config_post({"setup_complete": True})
    assert server.setup_required(server.load_config()) is False
    server.config_post({"user_name": "Grace Hopper"})
    server.config_post({"setup_complete": True})
    cfg = server.load_config()
    assert cfg["setup_complete"] is True
    assert cfg["user_name"] == "Grace Hopper"
    assert server.setup_required(cfg) is False


# ----------------------------------------------------------------------
# Ollama model download (pull) — streamed progress, honest failures
# ----------------------------------------------------------------------

class _FakePullResponse:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


PULL_LINES = [
    b'{"status":"pulling manifest"}\n',
    b'{"status":"downloading","digest":"sha256:abc",'
    b'"total":100,"completed":40}\n',
    b'{"status":"downloading","digest":"sha256:abc",'
    b'"total":100,"completed":100}\n',
    b'{"status":"verifying sha256 digest"}\n',
    b'{"status":"success"}\n',
]


def test_pull_streams_progress_events(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return _FakePullResponse(PULL_LINES)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    planner = planners.OllamaPlanner(model="qwen2.5:7b")
    events = list(planner.pull())
    assert [e["status"] for e in events] == [
        "pulling manifest", "downloading", "downloading",
        "verifying sha256 digest", "success"]
    assert events[1]["completed"] == 40 and events[1]["total"] == 100
    assert seen["url"].endswith("/api/pull")
    assert seen["body"]["model"] == "qwen2.5:7b"
    assert seen["body"]["stream"] is True


def test_pull_unreachable_raises_plan_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    planner = planners.OllamaPlanner(model="qwen2.5:7b")
    with pytest.raises(planners.PlanError) as excinfo:
        list(planner.pull())
    assert "not reachable" in str(excinfo.value)


def test_pull_http_error_carries_ollamas_detail(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", None,
            io.BytesIO(b'{"error":"model \'nope:1b\' not found"}'))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    planner = planners.OllamaPlanner(model="nope:1b")
    with pytest.raises(planners.PlanError) as excinfo:
        list(planner.pull())
    assert "not found" in str(excinfo.value)


def test_pull_midstream_error_raises(monkeypatch):
    lines = [b'{"status":"pulling manifest"}\n',
             b'{"error":"pull model manifest: file does not exist"}\n']

    def fake_urlopen(req, timeout=None):
        return _FakePullResponse(lines)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    planner = planners.OllamaPlanner(model="nope:1b")
    with pytest.raises(planners.PlanError) as excinfo:
        list(planner.pull())
    assert "file does not exist" in str(excinfo.value)


def test_pull_endpoint_streams_ndjson(config_env, monkeypatch):
    from fastapi.testclient import TestClient

    def fake_urlopen(req, timeout=None):
        return _FakePullResponse(PULL_LINES)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = TestClient(server.app)
    res = client.post("/api/providers/ollama/pull",
                      json={"model": "qwen2.5:7b"})
    assert res.status_code == 200
    lines = [json.loads(l) for l in res.text.splitlines() if l.strip()]
    # v3.7.2: events are enriched with whole-model aggregates.
    assert lines[-1]["status"] == "success"
    assert lines[-1]["percent"] == 100
    assert lines[1]["completed"] == 40


def test_pull_endpoint_failure_is_a_final_error_event(
        config_env, monkeypatch):
    from fastapi.testclient import TestClient

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = TestClient(server.app)
    res = client.post("/api/providers/ollama/pull",
                      json={"model": "qwen2.5:7b"})
    assert res.status_code == 200
    lines = [json.loads(l) for l in res.text.splitlines() if l.strip()]
    assert len(lines) == 1
    assert "not reachable" in lines[0]["error"]


# ----------------------------------------------------------------------
# shipped wiring (source guards, same approach as test_theme_v350)
# ----------------------------------------------------------------------

def test_wizard_markup_present():
    text = _read(STATIC, "index.html")
    assert 'id="wizard"' in text
    for step in range(5):
        assert f'data-wiz-step="{step}"' in text
    assert 'id="wizName"' in text
    assert 'id="wizTheme"' in text
    assert 'data-wiz-theme="light"' in text
    assert 'data-wiz-theme="dark"' in text
    assert 'id="wizProvider"' in text
    assert 'id="wizAiError"' in text
    assert 'id="wizSkip"' in text
    assert "SKIP SETUP" in text
    assert 'id="wizNext"' in text
    assert 'id="wizBack"' in text


def test_wizard_logic_present():
    text = _read(STATIC, "index.html")
    assert "function openWizard(" in text
    assert "checkFirstRun(" in text
    assert "if (!cfg.setup_complete) openWizard()" in text
    assert "patch.setup_complete = true" in text
    # The theme step re-skins live through the same applyTheme the
    # rest of the app uses — not a wizard-only copy.
    assert "applyTheme(b.dataset.wizTheme)" in text
    # The download streams from the pull endpoint.
    assert '"/api/providers/ollama/pull"' in text
    assert "wizPullModel(" in text


def test_settings_reopen_and_name_field_present():
    text = _read(STATIC, "index.html")
    assert 'id="cfgRunSetup"' in text
    assert "RUN SETUP AGAIN" in text
    assert '$("#cfgRunSetup").addEventListener("click", openWizard)' \
        in text
    assert 'id="cfgName"' in text
    assert '$("#cfgName").value = cfg.user_name || "";' in text
    assert 'user_name: $("#cfgName").value.trim()' in text


def test_brand_documents_the_wizard():
    brand = _read(ROOT, "BRAND.md")
    assert "First-run setup wizard" in brand
