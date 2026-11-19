"""v3.5.0 — light / dark appearance.

User order, 2026-11-19: "Add a feature; dark mode and light
mode.  By default, it should be in light mode for video
recording for better visuals."

Pinned here:
* the default is light — in DEFAULT_CONFIG, in load_config for
  a config file that predates the theme key (every existing
  install), and in the normalisers;
* a toggle persists through the (atomic) save/load path and
  through the Settings endpoint handlers, and an invalid value
  is refused without touching the stored one;
* the floating window's theme mapping follows the same rule;
* the UI wiring is present in the shipped files, and the dark
  palette values are untouched (dark mode is the accepted
  Control Room design — the -ink tokens equal the fills there).
"""
import json
import os

import pytest
from fastapi import HTTPException

from deskdora import server
from deskdora.overlay import overlay_theme

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
# the default is light
# ----------------------------------------------------------------------

def test_default_theme_is_light():
    assert server.DEFAULT_CONFIG["theme"] == "light"
    assert server.theme_of({}) == "light"
    assert server.theme_of(None) == "light"
    assert server.theme_of({"theme": "dark"}) == "dark"
    # Anything not exactly "dark" falls back to the default.
    assert server.theme_of({"theme": "DARK"}) == "light"
    assert server.theme_of({"theme": "blue"}) == "light"
    assert server.theme_of({"theme": None}) == "light"


def test_missing_config_file_loads_light(config_env):
    assert server.load_config()["theme"] == "light"


def test_existing_config_without_theme_key_loads_light(config_env):
    # Every pre-v3.5 install has a config.json with no theme key;
    # it must open in light mode until the user switches.
    (config_env / "config.json").write_text(json.dumps(
        {"planner": "builtin", "edge_light": "double"}))
    cfg = server.load_config()
    assert cfg["theme"] == "light"
    assert cfg["edge_light"] == "double"  # their settings survive


# ----------------------------------------------------------------------
# the toggle persists
# ----------------------------------------------------------------------

def test_theme_toggle_persists_through_save_load(config_env):
    cfg = server.load_config()
    cfg["theme"] = "dark"
    server.save_config(cfg)
    assert server.load_config()["theme"] == "dark"
    raw = json.loads((config_env / "config.json").read_text())
    assert raw["theme"] == "dark"

    cfg = server.load_config()
    cfg["theme"] = "light"
    server.save_config(cfg)
    assert server.load_config()["theme"] == "light"


def test_config_endpoint_round_trips_theme(config_env):
    # The Settings Appearance control POSTs {theme: ...} to the
    # same handler pair the settings page reads back.
    assert server.config_get()["theme"] == "light"
    out = server.config_post({"theme": "dark"})
    assert out["theme"] == "dark"
    assert server.config_get()["theme"] == "dark"
    raw = json.loads((config_env / "config.json").read_text())
    assert raw["theme"] == "dark"
    out = server.config_post({"theme": "light"})
    assert out["theme"] == "light"
    assert server.config_get()["theme"] == "light"


def test_config_endpoint_rejects_bad_theme(config_env):
    server.config_post({"theme": "dark"})
    with pytest.raises(HTTPException) as excinfo:
        server.config_post({"theme": "solarised"})
    assert excinfo.value.status_code == 400
    # The refused write never landed; the stored value stands.
    assert server.config_get()["theme"] == "dark"


def test_config_post_without_theme_keeps_stored_theme(config_env):
    server.config_post({"theme": "dark"})
    server.config_post({"edge_light": "off"})
    assert server.config_get()["theme"] == "dark"


# ----------------------------------------------------------------------
# the floating window follows the same theme
# ----------------------------------------------------------------------

def test_overlay_theme_follows_config():
    assert overlay_theme({}) == "light"
    assert overlay_theme(None) == "light"
    assert overlay_theme({"theme": "light"}) == "light"
    assert overlay_theme({"theme": "dark"}) == "dark"
    assert overlay_theme({"theme": "junk"}) == "light"


# ----------------------------------------------------------------------
# shipped wiring (source guards, same approach as test_ui_copy)
# ----------------------------------------------------------------------

def test_main_ui_theme_wiring_present():
    text = _read(STATIC, "index.html")
    # The light token block + the pre-paint hint + the control.
    assert ':root[data-theme="light"]' in text
    assert 'localStorage.getItem("deskdora-theme")' in text
    assert 'id="cfgTheme"' in text
    assert 'data-theme-val="light"' in text
    assert 'data-theme-val="dark"' in text
    assert "function applyTheme(" in text
    assert "applyTheme(st.config.theme)" in text
    assert 'postConfig({ theme: b.dataset.themeVal })' in text


def test_companion_theme_wiring_present():
    text = _read(STATIC, "companion.html")
    assert ':root[data-theme="light"]' in text
    assert "st.config.theme" in text


def test_overlay_theme_stylesheets_present():
    src = _read(ROOT, "src", "deskdora", "window.py")
    assert "OVERLAY_CSS_DARK" in src
    assert "OVERLAY_CSS_LIGHT" in src
    assert "#FBFBF7" in src          # the light card surface
    assert "def overlay_css(" in src
    assert "overlay_theme(cfg)" in src


def test_dark_palette_values_unchanged():
    # Dark mode is the accepted Control Room design: its surface
    # values and the -ink tokens (== the fills) must not drift.
    text = _read(STATIC, "index.html")
    assert "--void:#040404" in text
    assert "--lime:#CEDE62" in text
    assert "--lime-ink:#CEDE62" in text
    assert "--mustard-ink:#ECD06F" in text
    # ...and the light block deepens the inks, never the fills.
    light = text.split(':root[data-theme="light"]', 1)[1]
    light = light.split("}", 1)[0]
    assert "--lime-ink:#5C6B1C" in light
    assert "--mustard-ink:#7D6318" in light
    assert "--lime:#CEDE62" not in light  # the fill is not redefined


def test_brand_documents_both_palettes():
    brand = _read(ROOT, "BRAND.md")
    assert "#CEDE62" in brand        # dark palette
    assert "#5C6B1C" in brand        # light lime-ink
    assert "#F2F2EC" in brand        # light paper background
