"""Wave 3 code-level tests for vibe S2, U1, U2, U3 (no browser:
these assert the shipped HTML/JS source patterns, per audit rules)."""
import os
import re

STATIC = os.path.join(os.path.dirname(__file__), "..", "src",
                      "deskdora", "static")


def _read(name):
    with open(os.path.join(STATIC, name)) as fh:
        return fh.read()


def test_no_interpolated_onclick_handlers():
    for name in ("index.html", "companion.html"):
        html = _read(name)
        bad = [m for m in re.findall(r'onclick="[^"]*"', html)
               if "${" in m]
        assert bad == [], (name, bad)
        assert "data-act" in html
        assert "ACT_DISPATCH" in html


def test_companion_screen_label_ternary_not_dead():
    html = _read("companion.html")
    assert '" WATCHING" : " WATCHING"' not in html
    assert "VIEWER WATCHING" in html and "VIEWERS WATCHING" in html


def test_companion_poll_sections_isolated():
    html = _read("companion.html")
    assert "runs section failed this cycle" in html
    assert "mcp section failed this cycle" in html
    assert "proposals section failed this cycle" in html
