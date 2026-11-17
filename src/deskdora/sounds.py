"""Sound tones: short, subtle, locally synthesised event sounds.

Every tone is generated in code as a small WAV (sine partials with a
soft attack/decay envelope) — no downloaded or copied sound files,
nothing that needs a licence.  Playback goes through ``paplay``
(the standard PulseAudio/PipeWire path) fire-and-forget: a missing
binary, a missing audio server, or a muted machine must never
block or fail the agent's work, so every failure is silent.

Settings live in the per-user config under ``sounds``:
``{"enabled": bool, "volume": 0..100, "events": {event: bool}}``.
The Features-panel rule applies: this module is where the toggles
are enforced — a disabled event produces no playback call at all.
"""
from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import wave

SAMPLE_RATE = 22050

# event id -> (label, default_on, [(freq_hz, start_s, dur_s), ...])
# Note sketches are deliberately quiet and short; the "grating"
# ones (they fire constantly) default off, per the user's call.
SOUND_EVENTS = {
    "app_opened": (
        "App opened", False,
        [(659.25, 0.0, 0.14), (880.0, 0.09, 0.20)]),
    "run_completed": (
        "Run completed", True,
        [(523.25, 0.0, 0.13), (659.25, 0.09, 0.13),
         (783.99, 0.18, 0.24)]),
    "run_failed": (
        "Run failed", True,
        [(220.0, 0.0, 0.20), (164.81, 0.16, 0.32)]),
    "confirmation_waiting": (
        "Confirmation waiting", True,
        [(783.99, 0.0, 0.12), (783.99, 0.22, 0.18)]),
    "companion_connected": (
        "Companion connected", False,
        [(987.77, 0.0, 0.16)]),
    "command_received": (
        "Command received from phone", False,
        [(1174.66, 0.0, 0.08), (987.77, 0.07, 0.12)]),
    "command_sent": (
        "Command sent from desktop", False,
        [(880.0, 0.0, 0.10)]),
    "queue_finished": (
        "Queue finished", True,
        [(523.25, 0.0, 0.12), (783.99, 0.10, 0.14),
         (1046.5, 0.20, 0.26)]),
}

DEFAULT_SOUNDS = {
    "enabled": True,
    "volume": 70,
    "events": {event: spec[1] for event, spec in SOUND_EVENTS.items()},
}


def sounds_config(config):
    """Merge the user's sound settings over the defaults."""
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
    for event, on in (raw.get("events") or {}).items():
        if event in merged["events"]:
            merged["events"][event] = bool(on)
    return merged


def event_enabled(config, event):
    cfg = sounds_config(config)
    return bool(cfg["enabled"] and cfg["events"].get(event, False))


# ---------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------

def _render_wav(path, notes):
    total = max(start + dur for _f, start, dur in notes) + 0.05
    frames = int(total * SAMPLE_RATE)
    samples = [0.0] * frames
    for freq, start, dur in notes:
        s0 = int(start * SAMPLE_RATE)
        n = int(dur * SAMPLE_RATE)
        for i in range(n):
            t = i / SAMPLE_RATE
            # soft attack, smooth decay — no clicks
            env = min(1.0, i / (0.012 * SAMPLE_RATE)) * \
                (1.0 - (i / n) ** 2) ** 1.5
            # fundamental + a whisper of second harmonic
            value = math.sin(2 * math.pi * freq * t) + \
                0.25 * math.sin(4 * math.pi * freq * t)
            if s0 + i < frames:
                samples[s0 + i] += 0.5 * env * value
    peak = max(1e-6, max(abs(s) for s in samples))
    scale = 0.62 / peak
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b"".join(
            struct.pack("<h", int(max(-1.0, min(1.0, s * scale))
                                   * 32767))
            for s in samples))
    return path


def tone_path(cache_dir, event):
    """The WAV for an event, generated on first use."""
    path = os.path.join(cache_dir, f"tone-{event}.wav")
    if not os.path.exists(path):
        os.makedirs(cache_dir, exist_ok=True)
        _render_wav(path, SOUND_EVENTS[event][2])
    return path


# ---------------------------------------------------------------------
# Playback (fire-and-forget; every failure is silent)
# ---------------------------------------------------------------------

def _paplay(path, volume):
    binary = shutil.which("paplay")
    if not binary:
        return False
    # paplay volume is 0..65536
    vol = int(max(0, min(100, volume)) / 100 * 65536)
    try:
        subprocess.Popen([binary, f"--volume={vol}", path],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def play(event, config, cache_dir, player=None, force=False):
    """Play the tone for ``event`` if settings allow it.  Returns
    True when a playback was actually started.  ``player`` is
    injectable for tests: player(path, volume) -> bool.  ``force``
    is for the Settings test buttons: an explicit tap previews the
    tone even when its toggle is off."""
    if event not in SOUND_EVENTS:
        return False
    cfg = sounds_config(config)
    if not force and (not cfg["enabled"]
                      or not cfg["events"].get(event, False)):
        return False
    try:
        path = tone_path(cache_dir, event)
    except Exception:
        return False
    player = player or _paplay
    try:
        return bool(player(path, cfg["volume"]))
    except Exception:
        return False
