"""UI copy guard (v3.1): the bulletin/disclaimer style the user
ordered removed must not creep back into the shipped UI, and the
v3.1 settings sections must be present."""
import os

STATIC = os.path.join(os.path.dirname(__file__), "..", "src",
                      "deskdora", "static")

BULLETIN_PHRASES = [
    "enforced in code, not just here",
    "service must be started with a network host",
    "remote control is refused",
    "Recommended while you evaluate DeskDora",
    "anything that leaves the machine",
    "never sent anywhere",
    "NOTHING THE AGENT DOES IS INVISIBLE",
    "STORED ONLY ON THIS MACHINE",
    "Viewing status needs no PIN",
    "money always needs a human yes",
    "a proposal executes itself; otherwise it waits",
]


def _read(name):
    with open(os.path.join(STATIC, name)) as fh:
        return fh.read()


def test_no_bulletin_copy_in_ui():
    for name in ("index.html", "companion.html"):
        text = _read(name)
        for phrase in BULLETIN_PHRASES:
            assert phrase not in text, (name, phrase)


def test_v31_settings_sections_present():
    text = _read("index.html")
    for marker in ("AI PROVIDERS", "ACCESS", "deskdora-mcp",
                   "provSelect", "apiKeyGen", "custAdd"):
        assert marker in text, marker
