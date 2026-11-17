"""Voice: offline speech out (TTS) and speech in (STT).

DeskDora is local-first, so its voice is too — no cloud speech APIs.

**Speech out** — engines, in preference order:
  1. ``espeak-ng`` / ``espeak`` (system binary; tiny, robotic, always
     offline).  Speaking renders through the default audio device;
     ``render()`` writes a WAV file instead, which is how the feature
     is verified in a headless rig with no sound card.
  2. ``pyttsx3`` (Python library; itself usually backed by espeak).
  3. ``null`` — no engine; ``speak()`` reports False honestly.

**Speech in** — a pipeline with a pluggable engine:
  ``transcribe(wav_path)`` tries PocketSphinx (fully offline, small,
  weak accuracy) and then Vosk (offline, needs a separately downloaded
  model directory).  If no engine is installed, TranscriptionError
  says exactly that.  The pipeline — WAV in, text out, text handed to
  the planner — is unit-tested with a fake engine; the real engines'
  accuracy is whatever it is, and the module never dresses it up.

Nothing in here raises into the agent loop: a voice failure must
never fail a desktop task.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading


class VoiceError(RuntimeError):
    pass


class TranscriptionError(VoiceError):
    pass


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

class EspeakTTS:
    name = "espeak"

    def __init__(self, binary):
        self.binary = binary

    def speak(self, text):
        try:
            subprocess.run([self.binary, text], timeout=60,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False

    def render(self, text, wav_path):
        res = subprocess.run([self.binary, "-w", wav_path, text],
                             capture_output=True, text=True, timeout=60)
        if res.returncode != 0 or not os.path.exists(wav_path):
            raise VoiceError(f"espeak render failed: {res.stderr.strip()}")
        return wav_path


class Pyttsx3TTS:
    name = "pyttsx3"

    def speak(self, text):
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
            return True
        except Exception:
            return False

    def render(self, text, wav_path):
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.save_to_file(text, wav_path)
            engine.runAndWait()
        except Exception as exc:
            raise VoiceError(f"pyttsx3 render failed: {exc}")
        if not os.path.exists(wav_path):
            raise VoiceError("pyttsx3 render produced no file")
        return wav_path


class NullTTS:
    name = "null"

    def speak(self, text):
        return False

    def render(self, text, wav_path):
        raise VoiceError("no TTS engine available on this machine")


def get_tts():
    """Best available speech-out engine (never raises)."""
    for binary in ("espeak-ng", "espeak"):
        path = shutil.which(binary)
        if path:
            return EspeakTTS(path)
    try:
        import pyttsx3  # noqa: F401
        return Pyttsx3TTS()
    except Exception:
        return NullTTS()


def tts_status():
    engine = get_tts()
    return {"engine": engine.name, "available": engine.name != "null"}


def speak(text, engine=None):
    """Speak asynchronously; returns immediately.  False if no engine
    or the utterance failed to start."""
    engine = engine or get_tts()
    if engine.name == "null" or not text:
        return False
    threading.Thread(target=engine.speak, args=(text,), daemon=True).start()
    return True


def render_speech(text, wav_path, engine=None):
    engine = engine or get_tts()
    return engine.render(text, wav_path)


# ---------------------------------------------------------------------------
# STT
# ---------------------------------------------------------------------------

class PocketSphinxTranscriber:
    name = "pocketsphinx"

    def transcribe(self, wav_path):
        try:
            from pocketsphinx import AudioFile
        except ImportError as exc:
            raise TranscriptionError(
                "pocketsphinx is not installed") from exc
        text = ""
        for phrase in AudioFile(audio_file=wav_path):
            text += str(phrase) + " "
        return text.strip()


class VoskTranscriber:
    name = "vosk"

    def __init__(self, model_path):
        self.model_path = model_path

    def transcribe(self, wav_path):
        try:
            import vosk
        except ImportError as exc:
            raise TranscriptionError("vosk is not installed") from exc
        import json
        import wave
        wf = wave.open(wav_path, "rb")
        model = vosk.Model(self.model_path)
        rec = vosk.KaldiRecognizer(model, wf.getframerate())
        while True:
            data = wf.readframes(4000)
            if not data:
                break
            rec.AcceptWaveform(data)
        return json.loads(rec.FinalResult()).get("text", "").strip()


class FakeTranscriber:
    """Deterministic stand-in used by tests (and only by tests)."""

    name = "fake"

    def __init__(self, text=""):
        self.text = text

    def transcribe(self, wav_path):
        if not os.path.exists(wav_path):
            raise TranscriptionError(f"no such file: {wav_path}")
        return self.text


def get_transcriber(model_path=None):
    """Best available speech-in engine, or None (with the reason
    available via stt_status()).  Vosk with a model is preferred —
    its recognition quality is markedly better than PocketSphinx's
    small built-in model."""
    model_path = model_path or os.environ.get("DESKDORA_VOSK_MODEL", "")
    if model_path and os.path.isdir(model_path):
        try:
            import vosk  # noqa: F401
            return VoskTranscriber(model_path)
        except Exception:
            pass
    try:
        import pocketsphinx  # noqa: F401
        return PocketSphinxTranscriber()
    except Exception:
        pass
    return None


def stt_status(model_path=None):
    engine = get_transcriber(model_path)
    if engine is None:
        return {"engine": None, "available": False,
                "note": "no offline STT engine installed "
                        "(pocketsphinx or vosk+model)"}
    return {"engine": engine.name, "available": True}


def transcribe(wav_path, engine=None):
    engine = engine or get_transcriber()
    if engine is None:
        raise TranscriptionError(
            "no offline speech-to-text engine is installed on this "
            "machine (looked for pocketsphinx, then vosk with a model "
            "at $DESKDORA_VOSK_MODEL)")
    return engine.transcribe(wav_path)


def voice_status():
    return {"tts": tts_status(), "stt": stt_status()}
