"""Wave 4 regression tests for DD-A5: the backup scrub must
strip secret-shaped key names by pattern, at every depth, not
only the exact key "api_key".  The planted shapes are the
audit's: apikey, apiKey, API_KEY, password, token,
access_token, secret, client_secret, pin and nested
companion_pin must never reach an export - while legitimate
keys that merely contain innocent substrings (hotkey, keyboard,
a pinned note) must survive."""
import json

import pytest

from deskdora import backup

PLANTED = "PLANTED-SECRET-VALUE"


@pytest.mark.parametrize("key", [
    "api_key", "apikey", "apiKey", "API_KEY", "api-key",
    "token", "access_token", "refreshToken", "auth_token",
    "secret", "client_secret", "appSecret",
    "password", "passwd", "passphrase",
    "pin", "companion_pin", "PIN",
    "private_key", "access_key", "encryption_key", "key",
    "credentials", "aws_credentials",
])
def test_secret_shaped_keys_are_stripped(key):
    cfg = {"providers": {"some": {key: PLANTED, "model": "m"}}}
    out = backup.scrub_config(cfg)
    assert PLANTED not in json.dumps(out)
    assert out["providers"]["some"]["model"] == "m"


def test_audit_minimised_shape():
    # The property run's shrunk example, verbatim shape.
    cfg = {"providers": {"some": {"apikey": PLANTED}}}
    assert PLANTED not in json.dumps(backup.build_backup(
        cfg, [], []))


def test_secrets_stripped_at_every_depth():
    cfg = {"companion_pin": PLANTED,
           "providers": {"openai": {"api_key": PLANTED}},
           "custom_providers": [{"name": "gw",
                                 "apiKey": PLANTED}],
           "deep": {"deeper": [{"access_token": PLANTED},
                               {"nested": {"companion_pin":
                                            PLANTED}}]}}
    out = backup.scrub_config(cfg)
    assert PLANTED not in json.dumps(out)


@pytest.mark.parametrize("key", [
    "hotkey", "keyboard", "monkey", "key_combo", "pinned_note",
    "spin", "tokenizer_model", "secretary_name",
])
def test_innocent_keys_survive(key):
    # These contain the letters but not the whole WORDS:
    # "tokenizer" is not "token", "secretary" is not "secret",
    # "pinned" is not "pin", and "hotkey" is one word, not a
    # trailing "key" word.
    cfg = {"section": {key: "keep-me"}}
    out = backup.scrub_config(cfg)
    assert out["section"][key] == "keep-me"


def test_whole_config_shape_survives():
    cfg = {"theme": "dark", "user_name": "A",
           "hotkey": {"enabled": True, "combo": "Ctrl+Alt+D"},
           "providers": {"openai": {"api_key": PLANTED,
                                    "model": "gpt-4o-mini",
                                    "base_url": "https://x"}},
           "companion_pin": PLANTED}
    out = backup.scrub_config(cfg)
    assert out["hotkey"] == {"enabled": True,
                             "combo": "Ctrl+Alt+D"}
    assert out["providers"]["openai"]["model"] == "gpt-4o-mini"
    assert PLANTED not in json.dumps(out)
    # The input is not mutated.
    assert cfg["providers"]["openai"]["api_key"] == PLANTED


def test_import_scrubs_smuggled_variant_keys():
    doc = {"format": "deskdora-backup", "version": 1,
           "config": {"providers": {"x": {"apikey": PLANTED,
                                          "token": PLANTED}}},
           "routines": [], "templates": []}
    parsed = backup.parse_backup(doc)
    assert PLANTED not in json.dumps(parsed)
