"""v3.7.1 — user-facing audit: internal names out of the UI,
README claims cut to what the code does, and the stale run-view
glitch fixed.

User order, 2026-11-19: check the user side for UI glitches and
internal names in the UI or README; fix what isn't right; remove
feature claims that don't hold up.

Pinned here:
* the failed-attempts -> reset -> idle sequence at the /api/status
  level (exactly what the Run view polls): a failed attempt leaves
  a goal in state with NO run and NO result — a goal alone is not
  a run — and after reset the status is fully idle;
* the Run view's sync logic in index.html: the run panel is only
  adopted for a run that exists (running / result / pending plan),
  and a settle-to-idle branch returns the entry form when the
  server is idle and empty;
* the rendered copy of both pages carries no internal vocabulary
  (rig / verdict / assertion / endpoint / stdio / dev rationale);
* an unknown planner-error class can never render its raw internal
  name — the page falls back to a plain label;
* the README no longer claims a vision fallback, a PyPI install,
  an exact-sounding provider count that is wrong, or end-to-end
  Wayland verification.
"""
import json
import os
import urllib.error

import pytest
from fastapi.testclient import TestClient

from deskdora import server

ROOT = os.path.join(os.path.dirname(__file__), "..")
STATIC = os.path.join(ROOT, "src", "deskdora", "static")
README = os.path.join(ROOT, "README.md")

client = TestClient(server.app)


def _read(path):
    with open(path) as fh:
        return fh.read()


# ----------------------------------------------------------------------
# Part 3 — the failed-attempts -> reset -> idle sequence
# ----------------------------------------------------------------------

@pytest.fixture
def config_env(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CONFIG_PATH",
                        str(tmp_path / "config.json"))
    st = server.MANAGER.state
    st.update({"running": False, "planner_error": None,
               "connection_error": None, "goal": "", "result": None,
               "pending_plan": None, "run_dir": None})
    return tmp_path


def _write_config(config_env, data):
    (config_env / "config.json").write_text(json.dumps(data))


def _unreachable(req):
    raise urllib.error.URLError("Connection refused")


def test_failed_attempts_then_reset_status_is_fully_idle(
        config_env, monkeypatch):
    # Attempt 1: a definitive planner problem (error page path).
    _write_config(config_env, {"planner": "nosuch-provider"})
    res = client.post("/api/run", json={"goal": "open the terminal"})
    assert res.status_code == 200
    assert res.json()["ok"] is False
    st = client.get("/api/status").json()["state"]
    # The state that used to flip the Run view into a fake run:
    # a goal is present, but there is no run and no result.
    assert st["goal"] == "open the terminal"
    assert st["running"] is False
    assert st["result"] is None
    assert st["planner_error"]["class"] == "provider_unsupported"

    # Attempt 2: an unreachable planner (connection pop-up path).
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: _unreachable(req))
    _write_config(config_env, {"planner": "ollama",
                               "model": "qwen2.5:7b"})
    res = client.post("/api/run", json={"goal": "tidy the folder"})
    assert res.json()["ok"] is False
    st = client.get("/api/status").json()["state"]
    assert st["goal"] == "tidy the folder"
    assert st["running"] is False
    assert st["result"] is None
    assert st["connection_error"]["cause"] == "ollama_down"
    assert st["planner_error"] is None

    # Reset: the status the fixed Run view settles on — fully idle.
    res = client.post("/api/reset")
    assert res.status_code == 200
    payload = client.get("/api/status").json()
    st = payload["state"]
    assert st["running"] is False
    assert st["goal"] == ""
    assert st["result"] is None
    assert st["planner_error"] is None
    assert st["connection_error"] is None
    assert st["pending_plan"] is None
    assert not payload.get("pending_confirmation")


def test_run_view_adopts_panel_only_for_a_real_run():
    text = _read(os.path.join(STATIC, "index.html"))
    # The panel-adopt branch is gated on a run that exists.
    assert "const hasRun = running || !!st.state.result ||" in text
    assert "if (st.state.goal && hasRun && !S.goal && !dismissed)" \
        in text
    # The old bare-goal adoption is gone.
    assert "if (st.state.goal && !S.goal && !dismissed)" not in text


def test_run_view_settles_back_to_entry_form_when_idle():
    text = _read(os.path.join(STATIC, "index.html"))
    marker = "Server idle and empty: settle back to the entry form."
    assert marker in text
    block = text[text.index(marker):]
    # The settle branch restores the form and clears the local goal.
    assert '$("#cmdRun").hidden = true; $("#cmdIdle").hidden = false;' \
        in block
    assert 'S.goal = ""; S.result = null;' in block
    # …and it is keyed on the fully-idle status contract.
    assert "!st.state.pending_plan && !st.pending_confirmation &&" \
        in text


# ----------------------------------------------------------------------
# Part 1 + 4 — rendered copy carries no internal vocabulary
# ----------------------------------------------------------------------

INDEX_BANNED = [
    "REFERENCE RIG",                # test-rig vocabulary in a note
    "EVERY VERDICT",                # QA vocabulary
    "Assertion-verified",           # QA vocabulary as a KPI label
    "END-STATE VERIFIED",           # QA vocabulary as a section note
    "NO MODELS ENDPOINT",           # API jargon in a status message
    "its endpoints. Off refuses",   # API jargon in a feature hint
    "(stdio)",                      # transport jargon in Settings
    "reads better in screen recordings",  # dev rationale as UI copy
]

COMPANION_BANNED = [
    "STDIO",                        # transport jargon on the phone UI
]


def test_index_copy_has_no_internal_vocabulary():
    text = _read(os.path.join(STATIC, "index.html"))
    for phrase in INDEX_BANNED:
        assert phrase not in text, phrase


def test_companion_copy_has_no_internal_vocabulary():
    text = _read(os.path.join(STATIC, "companion.html"))
    for phrase in COMPANION_BANNED:
        assert phrase not in text, phrase


def test_benchmark_copy_is_plain_language():
    text = _read(os.path.join(STATIC, "index.html"))
    assert "EVERY RESULT CHECKED AGAINST THE TASK'S END STATE" in text
    assert "End states checked" in text
    assert "RESULTS CHECKED" in text


def test_planner_error_class_never_renders_raw():
    text = _read(os.path.join(STATIC, "index.html"))
    # Unknown classes fall back to a plain label, never the raw
    # internal class name.
    assert 'PERR_CLASSES[pe.class] || "Setup problem"' in text
    assert "PERR_CLASSES[pe.class] || pe.class" not in text


# ----------------------------------------------------------------------
# Part 2 — README claims match the code
# ----------------------------------------------------------------------

def test_readme_makes_no_vision_fallback_claim():
    text = _read(README)
    assert "with vision fallback" not in text
    assert "exists as a fallback" not in text
    assert "accessibility tree first, vision fallback" not in text
    # …and says the honest thing instead, in both places.
    assert "There is no vision fallback" in text
    assert "no vision/OCR fallback" in text
    assert "Grounding: the accessibility tree only" in text


def test_readme_tagline_has_no_vision_claim():
    text = _read(README)
    assert "accessibility-tree grounding, Ollama-powered planning" \
        in text


def test_readme_provider_count_is_exact():
    text = _read(README)
    assert "20+ named providers" not in text
    assert "19 named cloud providers" in text
    assert "21 built-in presets" in text


def test_readme_wayland_not_claimed_verified():
    text = _read(README)
    assert "Wayland is supported through the desktop" not in text
    assert "where every feature is verified" not in text
    # Owner's order (2026-11-19): the test-status hedges came out —
    # the README states what the driver does, not its test status.
    assert "not verified end-to-end" not in text
    assert "has not been verified" not in text
    assert "untested" not in text
    # The functional truth that replaced the hedge stays stated.
    flat = " ".join(text.split())
    assert "depends on the compositor's portal backend" in flat


def test_readme_no_pypi_or_stale_version_claims():
    text = _read(README)
    assert "pip install deskdora" not in text
    assert "deskdora_3.2.0_amd64.deb" not in text
    assert "before/after screenshots" not in text
