"""Planner connection failures, in plain language (v3.3).

A planner that cannot connect must never surface as a cryptic log
line or a silent stall.  This module provides:

- ``preflight(mode, config)`` — a fast, bounded connectivity check
  run BEFORE a run starts with an Ollama or cloud planner.  Returns
  None when the planner is reachable, else a failure payload.
- ``classify(message, ...)`` — turns a planner error (an HTTP
  status, a refused connection, a timeout) into the payload the
  app pop-up and the companion both render: provider name, a
  human cause, and one line on what to check.

HARD RULE (truthful badging): a connection failure NEVER switches
the planner silently.  The payload is information for the user;
the only fallbacks happen when the user taps "Use the built-in
planner instead" in the pop-up.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.request

from .planners import PROVIDER_REGISTRY, make_provider_planner

CAUSES = {
    "invalid_key": (
        "The provider rejected the API key (401).",
        "Check the API key in Settings → Providers."),
    "rate_limited": (
        "The provider is rate-limiting requests (429).",
        "Wait a minute and retry, or pick another planner."),
    "model_not_found": (
        "That model wasn't found at the provider (404).",
        "Pick a different model in Settings → Providers."),
    "provider_error": (
        "The provider returned a server error (5xx).",
        "Try again shortly, or pick another planner."),
    "timeout": (
        "The request timed out.",
        "Check the network connection, then retry."),
    "unreachable": (
        "The provider couldn't be reached over the network.",
        "Check the network connection, then retry."),
    "ollama_down": (
        "Ollama isn't running on this machine.",
        "Start Ollama, or pick another planner."),
    "no_key": (
        "No API key is configured for this provider.",
        "Add the key in Settings → Providers."),
    "unknown": (
        "The connection failed.",
        "Check Settings → Providers, then retry."),
}


def _split_mode(mode):
    """Modes arrive either as a provider id ('openai') or as a
    mode label ('openai:gpt-4o-mini'); normalise to (id, model)."""
    pid, _, model = str(mode or "").partition(":")
    return pid, model


def provider_display(mode, config):
    """Human planner name for the pop-up, mirroring the run badge:
    'Ollama · qwen2.5:1.5b', 'Anthropic Claude · claude-sonnet-4-5',
    'Built-in'."""
    providers = (config or {}).get("providers") or {}
    pid, tail_model = _split_mode(mode)
    if pid == "builtin":
        return "Built-in"
    if pid == "ollama" or (pid in PROVIDER_REGISTRY and
                           PROVIDER_REGISTRY[pid]["protocol"]
                           == "ollama"):
        model = tail_model or providers.get("ollama", {}).get("model") \
            or (config or {}).get("model") or "qwen2.5:1.5b"
        return f"Ollama · {model}"
    if pid in PROVIDER_REGISTRY:
        label = PROVIDER_REGISTRY[pid]["label"]
        model = tail_model or providers.get(pid, {}).get("model") \
            or PROVIDER_REGISTRY[pid].get("default_model") or ""
        return f"{label} · {model}" if model else label
    for custom in (config or {}).get("custom_providers") or []:
        if custom.get("id") == pid:
            return f"{custom.get('label', pid)} · " \
                   f"{tail_model or custom.get('model', '')}".rstrip(" ·")
    return mode


def planner_kind(mode, config):
    pid, _ = _split_mode(mode)
    if pid in (None, "", "builtin", "auto"):
        return "builtin"
    if pid == "ollama" or (pid in PROVIDER_REGISTRY and
                           PROVIDER_REGISTRY[pid]["protocol"]
                           == "ollama"):
        return "ollama"
    return "cloud"


def payload(mode, config, cause, raw=""):
    detail, suggestion = CAUSES.get(cause, CAUSES["unknown"])
    display = provider_display(mode, config)
    return {
        "provider": mode,
        "provider_display": display,
        "cause": cause,
        "headline": f"Couldn't connect to {display}",
        "detail": detail,
        "suggestion": suggestion,
        "raw": str(raw)[:300],
    }


def classify(message, mode, config):
    """Map a planner error message to a failure payload."""
    text = str(message or "")
    kind = planner_kind(mode, config)
    if "no API key" in text:
        cause = "no_key"
    elif re.search(r"HTTP 401\b", text):
        cause = "invalid_key"
    elif re.search(r"HTTP 429\b", text):
        cause = "rate_limited"
    elif re.search(r"HTTP 404\b", text):
        cause = "model_not_found"
    elif re.search(r"HTTP 5\d\d\b", text):
        cause = "provider_error"
    elif "timed out" in text.rsplit(":", 1)[-1] \
            or "errno 110" in text.lower():
        # "{label} unreachable or timed out: {exc}" — the segment
        # after the last colon carries the actual cause.
        cause = "timeout"
    elif "unreachable" in text or "urlopen error" in text \
            or "connection refused" in text.lower() \
            or "errno 111" in text.lower() \
            or "Name or service" in text \
            or "Failed to establish" in text:
        cause = "ollama_down" if kind == "ollama" else "unreachable"
    elif "timed out" in text or "timeout" in text.lower():
        cause = "timeout"
    else:
        cause = "unknown"
    return payload(mode, config, cause, raw=text)


# ---------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------

def _probe_ollama(mode, config, timeout):
    providers = (config or {}).get("providers") or {}
    model = providers.get("ollama", {}).get("model") \
        or (config or {}).get("model") or "qwen2.5:1.5b"
    host = "http://127.0.0.1:11434"
    try:
        req = urllib.request.Request(host + "/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:
        return payload(mode, config, "ollama_down", raw=str(exc))
    names = [m.get("name", "") for m in data.get("models", [])]
    if not any(n.split(":")[0] == model.split(":")[0] for n in names):
        return payload(mode, config, "model_not_found",
                       raw=f"Ollama is running but has no model "
                           f"named {model} (has: {', '.join(names) or 'none'})")
    return None


def _probe_cloud(mode, config, timeout):
    providers = (config or {}).get("providers") or {}
    customs = (config or {}).get("custom_providers") or []
    pid, tail_model = _split_mode(mode)
    settings = dict(providers.get(pid, {}))
    if tail_model:
        settings["model"] = tail_model
    try:
        planner = make_provider_planner(pid, settings, customs=customs)
    except Exception as exc:
        return classify(str(exc), mode, config)
    result = {}

    def _run():
        try:
            ok, detail = planner.test_connection()
            result["ok"], result["detail"] = ok, detail
        except Exception as exc:  # test_connection shouldn't raise
            result["ok"], result["detail"] = False, str(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return payload(mode, config, "timeout",
                       raw=f"no answer within {timeout:.0f}s")
    if result.get("ok"):
        return None
    return classify(result.get("detail", ""), mode, config)


def preflight(mode, config, timeout=6.0):
    """Bounded connectivity check before a run starts.  Returns
    None when the planner is usable (or needs no network), else a
    failure payload for the pop-up.  'auto' and 'builtin' never
    fail here: auto's Ollama→built-in preference is its documented
    behaviour, not a silent switch of an explicit choice."""
    kind = planner_kind(mode, config)
    if kind == "builtin":
        return None
    if kind == "ollama":
        return _probe_ollama(mode, config, min(timeout, 4.0))
    return _probe_cloud(mode, config, timeout)
