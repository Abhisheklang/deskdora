"""Wave 5 regression tests for CR6: activate_window interpolated
the raw window title into the xdotool search pattern ^{title}$,
so titles with regex characters matched nothing, matched the
wrong window, or were invalid patterns.  The title is now escaped
and matches literally."""
import re
import subprocess

import pytest

from deskdora import actions


@pytest.fixture
def captured(monkeypatch):
    box = {}

    class FakeRes:
        returncode = 1
        stdout = ""
        stderr = ""

    def fake_run(argv, **kw):
        box["argv"] = argv
        return FakeRes()

    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    return box


def _pattern(captured, title):
    actions.activate_window(title)
    return captured["argv"][3]


@pytest.mark.parametrize("title", [
    "Notes [Draft] (v2.0) + final.txt",
    "a+b*c?d",
    "Report (2026) [final]",
    "Doc [unclosed",
    "plain title",
    "a.c",
    "100% sure ^really$",
])
def test_pattern_matches_own_title_literally(captured, title):
    pat = _pattern(captured, title)
    assert re.search(pat, title)


def test_dot_title_no_longer_matches_wrong_windows(captured):
    pat = _pattern(captured, "a.c")
    assert not re.search(pat, "abc")
    assert not re.search(pat, "axc")
    assert re.search(pat, "a.c")


def test_pattern_is_escaped_form(captured):
    pat = _pattern(captured, "Notes [Draft] (v2.0) + final.txt")
    assert pat == "^" + re.escape("Notes [Draft] (v2.0) + final.txt") + "$"


def test_empty_title_still_noop(captured):
    assert actions.activate_window("") is False
    assert "argv" not in captured
