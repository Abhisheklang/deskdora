"""Positioning rewrite (v3.7.2) — copy guards.

User order, 2026-11-19: DeskDora is no longer offline-only —
online AI is available via the user's own API key (the v3.1
provider registry) and the phone companion exists — so the
README, the description, and the app copy must match.

The position these guards pin: local-first BY DEFAULT (fully
working offline with Ollama or the built-in planner); OPTIONAL
online paths — cloud planners with the user's own API key, and
the phone companion over the local network.  One app with
optional online paths, not a separate "online version".  The
disclosure standard is the one Settings already carries: when a
cloud planner is active, your goal and a text summary of your
screen are sent to that provider.
"""
import os

ROOT = os.path.join(os.path.dirname(__file__), "..")
README = os.path.join(ROOT, "README.md")
PYPROJECT = os.path.join(ROOT, "pyproject.toml")
CONTROL = os.path.join(ROOT, "packaging", "control")
BRAND = os.path.join(ROOT, "BRAND.md")
INIT = os.path.join(ROOT, "src", "deskdora", "__init__.py")
STATIC = os.path.join(ROOT, "src", "deskdora", "static")

DESCRIPTION = (
    "Local-first desktop agent for Linux (Ubuntu/GNOME): "
    "accessibility-tree grounding, Ollama-powered planning with "
    "optional cloud planners via your own API key, a "
    "mandatory-confirmation safety layer, and a complete audit "
    "log of every action.")


def _read(path):
    with open(path) as fh:
        return fh.read()


def _flat(text):
    return " ".join(text.split())


# ----------------------------------------------------------------------
# The description line: one text, everywhere it is carried
# ----------------------------------------------------------------------

def test_description_line_matches_across_surfaces():
    assert f"**{DESCRIPTION}**" in _read(README)
    assert f'description = "{DESCRIPTION}"' in _read(PYPROJECT)
    assert f"Description: {DESCRIPTION}" in _read(CONTROL)


def test_no_surface_still_claims_vision_fallback():
    # The v3.7.1 audit cleaned the README; the package metadata
    # still carried the stale line until this rewrite.
    for path in (README, PYPROJECT, CONTROL):
        assert "with vision fallback" not in _read(path), path


# ----------------------------------------------------------------------
# README: local-first by default, online paths disclosed
# ----------------------------------------------------------------------

def test_readme_intro_scopes_the_offline_claim():
    flat = _flat(_read(README))
    # The old unscoped sentence is gone…
    assert "and nothing leaves the machine. Cloud planners" \
        not in flat
    # …replaced by the scoped default + both opt-in paths.
    assert "nothing leaves the machine by default" in flat
    assert "cloud planners (v3.1)" in flat
    assert "your goal and a text summary of your screen go to " \
           "that provider" in flat
    assert "phone companion (v3.0)" in flat
    assert "over your local network only" in flat
    assert "no cloud relay" in flat


def test_readme_never_calls_the_app_offline_only():
    text = _read(README)
    assert "offline-only" not in text
    assert "fully offline" not in text


# ----------------------------------------------------------------------
# In-app copy: the wizard carries the same scoped position
# ----------------------------------------------------------------------

def test_wizard_copy_is_scoped_and_discloses():
    html = _read(os.path.join(STATIC, "index.html"))
    # The old unscoped wizard claim is gone.
    assert "nothing leaves this machine. Cloud providers" \
        not in _flat(html)
    # The scoped default + the Settings-standard disclosure.
    assert "nothing leaves this machine unless you choose a " \
           "cloud provider" in _flat(html)
    assert "receive your goal and a text summary of your screen" \
        in _flat(html)
    # The Settings disclosure itself is the standard — untouched.
    assert "When a cloud planner is active, your goal and a text " \
           "summary of your screen are sent to that provider." \
        in _flat(html)


def test_wizard_ai_step_explains_the_choice_before_picking():
    # User order, 2026-11-19 (~17:12): the wizard's AI step must
    # EXPLAIN the offline/online choice before the user picks —
    # framing at the top, a grouped picker, one line per group.
    html = _read(os.path.join(STATIC, "index.html"))
    flat = _flat(html)
    # Framing at the top of the step.
    assert "brain can run offline on this computer, or online " \
           "through a cloud provider with your own API key" in flat
    assert "Offline is the default" in flat
    # The two group labels, in the wizard picker and in the
    # planner selects (where the built-in planner appears).
    assert html.count("Offline — on this computer") >= 2
    assert html.count("Online — cloud, your API key") >= 2
    # One-line explanation per group.
    assert "runs locally, nothing leaves the machine" in flat
    assert "Ollama needs a one-time model download" in flat
    assert "Needs internet and your own key" in flat
    # The grouping is by the registry's own local flag, and
    # Ollama stays the preselected default.
    assert "groups[p.local ? 0 : 1]" in html
    assert 'cur : "ollama"' in html
    assert 'id="wizAiGroups"' in html
    # Settings → AI Providers mirrors the same one-line framing,
    # ahead of the disclosure sentence it already carried.
    assert "The brain can run offline on this computer — " \
           "Ollama, LM Studio, or the built-in planner — or " \
           "online through a cloud provider with your own API " \
           "key." in flat


def test_companion_copy_has_no_offline_claim():
    html = _read(os.path.join(STATIC, "companion.html"))
    assert "offline-only" not in html
    assert "LOCAL NETWORK COMPANION" in html


# ----------------------------------------------------------------------
# Brand + package docstring carry the same line
# ----------------------------------------------------------------------

def test_brand_records_the_positioning_line():
    text = _read(BRAND)
    assert "local-first by default, with optional online paths" \
        in _flat(text)
    assert "Copy must never call DeskDora offline-only" in text


def test_package_docstring_offers_the_cloud_option():
    text = _read(INIT)
    assert "only with your own API key" in _flat(text)
    assert "a cloud provider" in _flat(text)
