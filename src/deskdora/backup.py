"""Settings backup: export / import (v3.7.3).

One JSON file (``deskdora-backup.json``) carrying the user's
SETUP: the config, the routines, and the personal templates.
Machine records — audit logs, run history — are not setup and
are never included.

Secrets are excluded BY CONSTRUCTION, on both directions:

* every secret-shaped key, at any depth of the config (provider
  presets and custom providers alike), is stripped on export —
  and stripped again on import, so even a hand-edited backup
  can't smuggle credentials in.  "Secret-shaped" is a name
  pattern, not one exact key: ``api_key``, ``apikey``,
  ``apiKey``, ``token``, ``secret``, ``password`` and ``pin``
  variants all count (DD-A5);
* the companion PIN is a credential too and is excluded;
* the DeskDora API key never lives in the config at all (it is
  stored hashed, separately) and cannot be exported.

The export UI says this plainly: keys are not in the file and
will need re-entering after an import.

Format: ``{"format": "deskdora-backup", "version": 1, ...}``.
A different format or version is rejected with a plain message
— no silent partial imports.
"""
from __future__ import annotations

import copy
import re
import time

from .routines import validate_cron

FORMAT = "deskdora-backup"
VERSION = 1

# Words that make a key name secret-shaped wherever they appear
# as a whole word in it (DD-A5).  Matching is by whole word after
# splitting on snake_case / kebab-case / camelCase boundaries,
# so "hotkey" (one word) and "keyboard" are NOT secrets, while
# api_key, apikey, apiKey, API_KEY, access_token, client_secret
# and companion_pin all are.
_SECRET_WORDS = {"apikey", "token", "secret", "password",
                 "passwd", "passphrase", "pin", "credential",
                 "credentials"}


def _key_words(key):
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key))
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w]


def _is_secret_key(key):
    words = _key_words(key)
    if not words:
        return False
    if any(word in _SECRET_WORDS for word in words):
        return True
    # A trailing "key" word marks a credential slot: api_key,
    # access_key, private_key, a bare "key".  One-word names
    # like "hotkey" or "monkey" never reach this branch - their
    # only word is not "key".
    return words[-1] == "key"

ROUTINE_FIELDS = ("name", "goal", "planner_mode", "schedule",
                  "trigger", "goal_template", "params")


class BackupError(ValueError):
    """A plain-words reason a backup can't be used."""


def _scrub(value):
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if _is_secret_key(key):
                continue
            out[key] = _scrub(val)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def scrub_config(config):
    """A deep copy of a config with every secret removed."""
    return _scrub(copy.deepcopy(config or {}))


def build_backup(config, routines, templates, now=None):
    """The backup document for the given live state (pure)."""
    routine_docs = []
    for routine in routines or []:
        doc = {field: routine.get(field)
               for field in ROUTINE_FIELDS}
        doc["params"] = doc.get("params") or []
        routine_docs.append(doc)
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": now if now is not None else time.time(),
        "config": scrub_config(config),
        "routines": routine_docs,
        "templates": [dict(t) for t in (templates or [])],
    }


def _validate_routine(routine):
    """Type-check one backup routine's fields (DD-A2).

    parse_backup is the gate: everything an import will later
    hand to RoutineStore.save must be validated HERE, before
    anything is applied, so a malformed routine (the audit's
    ``"schedule": 123`` is the minimised case) is refused with a
    plain reason instead of crashing the apply halfway through,
    after the existing routines were already deleted."""
    name = routine.get("name")
    for field in ("name", "goal"):
        if not isinstance(routine.get(field), str) \
                or not routine[field].strip():
            raise BackupError(
                "The backup contains a routine without a usable "
                f"{field}.")
    for field in ("planner_mode", "goal_template"):
        value = routine.get(field)
        if value is not None and not isinstance(value, str):
            raise BackupError(
                f"The backup's routine {name!r} has a damaged "
                f"{field} - it should be text.")
    schedule = routine.get("schedule")
    if schedule is not None:
        if not isinstance(schedule, str):
            raise BackupError(
                f"The backup's routine {name!r} has a damaged "
                "schedule - it should be a cron expression in "
                "text form.")
        if schedule:
            try:
                validate_cron(schedule)
            except ValueError as exc:
                raise BackupError(
                    f"The backup's routine {name!r} has an "
                    f"invalid schedule: {exc}")
    trigger = routine.get("trigger")
    if trigger is not None and not isinstance(trigger, dict):
        raise BackupError(
            f"The backup's routine {name!r} has a damaged "
            "trigger - it should be an object.")
    params = routine.get("params")
    if params is not None:
        if not isinstance(params, list) or any(
                not isinstance(p, dict)
                or not isinstance(p.get("name"), str)
                or "default" not in p
                for p in params):
            raise BackupError(
                f"The backup's routine {name!r} has damaged "
                "parameters - each one needs a name and a "
                "default value.")


def parse_backup(data):
    """Validate a would-be backup document; return it scrubbed
    and normalised.  Raises BackupError with the plain reason."""
    if not isinstance(data, dict):
        raise BackupError("That file isn't a DeskDora backup — "
                          "it doesn't contain a backup document.")
    if data.get("format") != FORMAT:
        raise BackupError("That file isn't a DeskDora backup — "
                          "its format stamp doesn't match.")
    version = data.get("version")
    if version != VERSION:
        raise BackupError(
            f"That backup uses format version {version!r}, and "
            f"this DeskDora reads version {VERSION}. Export a "
            "fresh backup from the other install instead.")
    config = data.get("config")
    if not isinstance(config, dict):
        raise BackupError("The backup's settings section is "
                          "missing or damaged.")
    routines = data.get("routines") or []
    templates = data.get("templates") or []
    if not isinstance(routines, list) \
            or not isinstance(templates, list):
        raise BackupError("The backup's routines/templates "
                          "sections are damaged.")
    for routine in routines:
        if not isinstance(routine, dict) \
                or not routine.get("name") \
                or not routine.get("goal"):
            raise BackupError("The backup contains a routine "
                              "without a name or a goal.")
        _validate_routine(routine)
    for template in templates:
        if not isinstance(template, dict) \
                or not isinstance(template.get("name"), str) \
                or not template.get("name") \
                or not isinstance(template.get("goal"), str) \
                or not template.get("goal"):
            raise BackupError("The backup contains a template "
                              "without a name or a goal.")
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": data.get("exported_at"),
        "config": scrub_config(config),
        "routines": [{field: r.get(field)
                      for field in ROUTINE_FIELDS}
                     for r in routines],
        "templates": [dict(t) for t in templates],
    }


def preview_backup(doc):
    """What an import of this (parsed) backup would change —
    the summary the user confirms against."""
    config = doc["config"]
    providers = []
    for pid in (config.get("providers") or {}):
        providers.append(pid)
    for custom in config.get("custom_providers") or []:
        providers.append(custom.get("name") or custom.get("id")
                         or "custom")
    return {
        "exported_at": doc.get("exported_at"),
        "config_sections": len(config),
        "providers": providers,
        "routines": len(doc["routines"]),
        "templates": len(doc["templates"]),
        "secrets_included": False,
        # Stated plainly in the preview (DD-A2): a backup never
        # carries secrets, and importing replaces the whole
        # config, so the live companion PIN and every stored
        # provider API key are cleared by an import.
        "clears_secrets": True,
        "note": "Importing replaces your current setup with "
                "this backup. Backups never include secrets, so "
                "importing clears your companion PIN and every "
                "saved provider API key - you will need to set "
                "them again afterwards.",
    }
