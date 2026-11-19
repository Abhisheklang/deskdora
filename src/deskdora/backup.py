"""Settings backup: export / import (v3.7.3).

One JSON file (``deskdora-backup.json``) carrying the user's
SETUP: the config, the routines, and the personal templates.
Machine records — audit logs, run history — are not setup and
are never included.

Secrets are excluded BY CONSTRUCTION, on both directions:

* every ``api_key`` value, at any depth of the config (provider
  presets and custom providers alike), is stripped on export —
  and stripped again on import, so even a hand-edited backup
  can't smuggle credentials in;
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
import time

FORMAT = "deskdora-backup"
VERSION = 1

_SECRET_KEYS = {"api_key"}
_CONFIG_SECRETS = {"companion_pin"}

ROUTINE_FIELDS = ("name", "goal", "planner_mode", "schedule",
                  "trigger", "goal_template", "params")


class BackupError(ValueError):
    """A plain-words reason a backup can't be used."""


def _scrub(value, top=False):
    if isinstance(value, dict):
        out = {}
        for key, val in value.items():
            if key in _SECRET_KEYS:
                continue
            if top and key in _CONFIG_SECRETS:
                continue
            out[key] = _scrub(val)
        return out
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def scrub_config(config):
    """A deep copy of a config with every secret removed."""
    return _scrub(copy.deepcopy(config or {}), top=True)


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
    for template in templates:
        if not isinstance(template, dict) \
                or not template.get("name") \
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
    }
