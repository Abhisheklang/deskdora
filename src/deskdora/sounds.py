"""Sound tones: short, pleasant, locally synthesised event sounds.

v3.7.2 rework — why this module no longer plays audio itself.
Up to v3.7.1, tones were rendered to WAV files here and played
through ``paplay`` (PulseAudio tools), fire-and-forget with every
failure swallowed.  In practice that meant SILENCE: ``paplay`` is
not installed on many machines (it is absent in the project's own
test environment), a headless or virtual desktop may have no
audio server at all, and because every failure was silent by
design, nothing — not even the Settings PLAY button, which used
the same path — ever told the user the tones were dead.  The user
reported exactly that: no tone, ever.

The playback path that actually works is the app window itself:
WebAudio synthesis in the page, the same note sketches defined
below, no external binary, no audio server, and a Settings
preview that is a direct user gesture so it cannot be blocked.
So this module is now the single source of truth for WHAT may
sound — the tone library, the per-event selection, the master
switch — and the server resolves each event to a tone and queues
it in the status payload (``RunManager.emit_tone``); the window
plays what it finds there.

Settings live in the per-user config under ``sounds``:
``{"enabled": bool, "volume": 0..100,
   "events": {event: tone_id | "off"}}``.
Pre-v3.7.2 configs stored per-event booleans; they migrate on
read (True → the event's default tone, False → "off"), so an
existing user's choices survive the upgrade.
"""
from __future__ import annotations

# ---------------------------------------------------------------------
# The tone library: id -> (label, [(freq_hz, start_s, dur_s), ...]).
# Sketches are deliberately quiet, short and pleasant; the window
# synthesises them with WebAudio (fundamental + a whisper of second
# harmonic, soft attack/decay — the same recipe the old WAV
# renderer used).  TONES_JS in index.html mirrors this table; a
# source guard keeps the two id sets identical.
# ---------------------------------------------------------------------
TONES = {
    "chime": ("Chime — rising three notes",
              [(523.25, 0.0, 0.13), (659.25, 0.09, 0.13),
               (783.99, 0.18, 0.24)]),
    "harp": ("Harp — rising four notes",
             [(523.25, 0.0, 0.12), (659.25, 0.08, 0.12),
              (783.99, 0.16, 0.12), (1046.50, 0.24, 0.28)]),
    "bell": ("Bell — one warm ring",
             [(1318.50, 0.0, 0.35), (987.77, 0.0, 0.28)]),
    "soft": ("Soft — two gentle notes",
             [(392.00, 0.0, 0.18), (523.25, 0.14, 0.30)]),
    "pop": ("Pop — two bright blips",
            [(1174.66, 0.0, 0.08), (880.00, 0.07, 0.14)]),
    "low": ("Low — two falling notes",
            [(220.00, 0.0, 0.20), (164.81, 0.16, 0.32)]),
}

OFF = "off"

# event id -> (label, default tone).  Completion MUST sound by
# default — silence on success was the user's complaint — and so
# do failure and confirmation; the chatty events stay off, as
# they were by default before.
SOUND_EVENTS = {
    "run_completed": ("Run completed", "chime"),
    "run_failed": ("Run failed", "low"),
    "confirmation_waiting": ("Confirmation waiting", "bell"),
    "queue_finished": ("Queue finished", "harp"),
    "app_opened": ("App opened", OFF),
    "companion_connected": ("Companion connected", OFF),
    "command_received": ("Command received from phone", OFF),
    "command_sent": ("Command sent from desktop", OFF),
}

DEFAULT_SOUNDS = {
    "enabled": True,
    "volume": 70,
    "events": {event: spec[1] for event, spec in SOUND_EVENTS.items()},
}


def _migrate_value(event, value):
    """One stored per-event value -> a tone id or "off".

    Booleans are the pre-v3.7.2 shape (True → the event's default
    tone, or "chime" for events whose default is off — the user
    had switched that event ON, so on it stays; False → "off").
    Unknown strings fall back to the event's default: the read
    path is lenient so a hand-edited config can never break the
    app (the write path validates strictly — see below).
    """
    default = SOUND_EVENTS[event][1]
    if isinstance(value, bool):
        if not value:
            return OFF
        return default if default != OFF else "chime"
    if value == OFF or value in TONES:
        return value
    return default


def sounds_config(config):
    """Merge the user's sound settings over the defaults, with
    legacy boolean event values migrated to tone selections."""
    merged = {"enabled": DEFAULT_SOUNDS["enabled"],
              "volume": DEFAULT_SOUNDS["volume"],
              "events": dict(DEFAULT_SOUNDS["events"])}
    raw = (config or {}).get("sounds") or {}
    if "enabled" in raw:
        merged["enabled"] = bool(raw["enabled"])
    if "volume" in raw:
        try:
            merged["volume"] = max(0, min(100, int(raw["volume"])))
        except (TypeError, ValueError):
            pass
    for event, value in (raw.get("events") or {}).items():
        if event in merged["events"]:
            merged["events"][event] = _migrate_value(event, value)
    return merged


def resolve_tone(config, event):
    """The tone id configured for ``event``, or None when the
    event must stay silent: master switch off, the event set to
    "off", or an unknown event.  This is the ONLY gating point —
    the server dispatches exactly what this returns."""
    if event not in SOUND_EVENTS:
        return None
    cfg = sounds_config(config)
    if not cfg["enabled"]:
        return None
    tone = cfg["events"].get(event, OFF)
    return tone if tone != OFF else None


def validate_event_tones(events):
    """Strict validation for the config write path.  ``events``
    maps event id -> tone id | "off" | legacy bool.  Raises
    ValueError naming the problem; the endpoint turns it into a
    400, like every other invalid setting."""
    if not isinstance(events, dict):
        raise ValueError("sounds.events must be an object")
    for event, value in events.items():
        if event not in SOUND_EVENTS:
            raise ValueError(f"unknown sound event: {event}")
        if isinstance(value, bool):
            continue
        if value != OFF and value not in TONES:
            raise ValueError(
                f"invalid tone for {event}: {value!r} "
                f"(choose one of {', '.join(TONES)} or 'off')")
