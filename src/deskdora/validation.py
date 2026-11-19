"""Pre-run planner validation (v3.7): the error page's brain.

Before any run starts, the configured planner is checked for
DEFINITIVE setup problems — the ones that can be answered
unambiguously:

* ``provider_unsupported`` — the configured provider id is not in
  the registry and is not a saved custom provider;
* ``model_unsupported`` — the provider's own live model list (its
  models endpoint) does not include the configured model;
* ``model_not_installed`` — a local server answered, but the model
  is not there: Ollama's /api/tags lacks it, or LM Studio is not
  serving it;
* ``key_missing`` — a key-based provider has no key saved (decided
  locally, no network), or demands one the probe didn't send;
* ``key_rejected`` — the provider's models endpoint answered
  401/403 to the saved key.

``validate_planner`` returns a payload dict for the error page,
or None.  None means "no definitive problem": the planner
validated, OR the provider could not be reached at all.
Unreachable is deliberately NOT decided here — a refused
connection or a timeout is not proof of a setup problem, and it
keeps the v3.3 "Couldn't connect" pop-up path
(``connfail.preflight``) exactly as before.  Only definitive
answers raise the error page.

HARD RULE (shared with connfail): a validation failure NEVER
changes the configured planner or model.  The payload is
information plus fix actions; every fix is a user action.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

from . import connfail
from .planners import PROVIDER_REGISTRY, make_provider_planner


def _split_mode(mode):
    """Modes arrive either as a provider id ('openai') or as a
    mode label ('openai:gpt-4o-mini'); normalise to (id, model)."""
    pid, _, model = str(mode or "").partition(":")
    return pid, model


def _label(pid, config):
    """Bare provider name for headlines: 'OpenAI', 'Ollama',
    a custom provider's name, else the id itself."""
    if pid in PROVIDER_REGISTRY:
        return PROVIDER_REGISTRY[pid]["label"].replace(" (local)", "")
    for custom in (config or {}).get("custom_providers") or []:
        if custom.get("id") == pid:
            return custom.get("name") or pid
    return pid


def _payload(cls, mode, config, model, headline, detail, suggestion,
             models=None, can_download=False, raw=""):
    pid, _ = _split_mode(mode)
    return {
        "class": cls,
        "provider": pid,
        "provider_display": connfail.provider_display(mode, config),
        "model": model,
        "headline": headline,
        "detail": detail,
        "suggestion": suggestion,
        "models": list(models) if models is not None else None,
        "can_download": bool(can_download),
        "raw": str(raw)[:300],
    }


# ---------------------------------------------------------------------
# Local servers: probed directly, so "answered but the model is
# absent" is distinguishable from "did not answer at all".
# ---------------------------------------------------------------------

def _validate_ollama(mode, config, model, timeout):
    providers = (config or {}).get("providers") or {}
    stored = providers.get("ollama") or {}
    host = (stored.get("base_url")
            or PROVIDER_REGISTRY["ollama"]["base_url"]).rstrip("/")
    try:
        req = urllib.request.Request(host + "/api/tags")
        with urllib.request.urlopen(
                req, timeout=min(timeout, 4.0)) as resp:
            data = json.load(resp)
    except Exception:
        return None  # no definitive answer — the pop-up path decides
    names = [m.get("name", "") for m in data.get("models", [])
             if m.get("name")]
    if any(n == model or n.split(":")[0] == model.split(":")[0]
           for n in names):
        return None
    return _payload(
        "model_not_installed", mode, config, model,
        "That model isn't downloaded yet",
        f"Ollama is running, but “{model}” isn't on this machine. "
        "The run didn't start, and the configured planner was not "
        "switched.",
        "Download it here (it can be several GB), or pick a model "
        "that is already installed.",
        models=names, can_download=True)


def _validate_lmstudio(mode, config, planner, model, timeout):
    """LM Studio serves only the models it has loaded, through its
    OpenAI-style /models list — an answered list without the
    configured model is a definitive 'not installed'."""
    headers = ({"Authorization": f"Bearer {planner.api_key}"}
               if planner.api_key else {})
    req = urllib.request.Request(planner.base_url + "/models",
                                 headers=headers)
    try:
        with urllib.request.urlopen(
                req, timeout=min(timeout, 6.0)) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return _key_payload(mode, config, model,
                                has_key=bool(planner.api_key),
                                raw=f"HTTP {exc.code}")
        return None  # some other answer — not ours to judge
    except Exception:
        return None  # unreachable — the pop-up path decides
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    if model in ids:
        return None
    return _payload(
        "model_not_installed", mode, config, model,
        "LM Studio isn't serving that model",
        f"LM Studio answered, but it isn't serving “{model}” "
        "right now. The run didn't start, and the configured "
        "planner was not switched.",
        "Load the model in LM Studio, or pick one it is already "
        "serving.",
        models=ids)


# ---------------------------------------------------------------------
# Key classes
# ---------------------------------------------------------------------

def _key_payload(mode, config, model, has_key, raw=""):
    pid, _ = _split_mode(mode)
    label = _label(pid, config)
    if not has_key:
        return _payload(
            "key_missing", mode, config, model,
            f"No API key saved for {label}",
            f"{label} needs an API key and none is saved (or the "
            "one sent wasn't accepted). The run didn't start.",
            "Add the key in provider settings, then check again.",
            raw=raw)
    return _payload(
        "key_rejected", mode, config, model,
        f"{label} rejected the API key",
        f"When DeskDora checked, {label} answered with an "
        "authorisation error (401/403) — the saved key isn't "
        "accepted. The run didn't start.",
        "Check the key in provider settings, then check again.",
        raw=raw)


# ---------------------------------------------------------------------
# Cloud + custom providers: the models endpoint is the probe.
# ---------------------------------------------------------------------

def _probe_models(planner, timeout):
    """Fetch the provider's live model list, bounded by a thread
    join (the same pattern as connfail's cloud pre-flight, so a
    provider that accepts the connection and then hangs cannot
    stall the run path).  Returns (outcome, value):

    - ("ok", list | None) — the endpoint answered; None means it
      offered no usable list, so the model cannot be judged here;
    - ("rejected", raw) — it answered 401/403;
    - ("indeterminate", None) — unreachable, timed out, or an
      answer this validation does not judge (5xx, 404, ...).
    """
    result = {}

    def _run():
        try:
            result["models"] = planner.fetch_models()
        except Exception as exc:  # PlanError in practice
            result["error"] = str(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return "indeterminate", None
    if "error" in result:
        text = result["error"]
        if re.search(r"HTTP 40[13]\b", text):
            return "rejected", text
        return "indeterminate", None
    return "ok", result.get("models")


def validate_planner(mode, config, timeout=6.0):
    """The one pre-run validation the run path uses.  Returns an
    error-page payload for a definitive setup problem, else None
    (planner fine, or unreachable — the pop-up path's call)."""
    config = config or {}
    pid, tail_model = _split_mode(mode)
    if pid in ("", "auto", "builtin"):
        return None  # nothing to validate: no provider involved
    providers = config.get("providers") or {}
    customs = config.get("custom_providers") or []
    custom = next((c for c in customs if c.get("id") == pid), None)

    # (a) provider not supported — decided locally, no network.
    if pid not in PROVIDER_REGISTRY and custom is None:
        return _payload(
            "provider_unsupported", mode, config, tail_model,
            "That provider isn't set up",
            f"The planner is set to “{pid}”, which isn't one of "
            "DeskDora's providers and isn't a saved custom "
            "provider. The run didn't start, and nothing was "
            "changed.",
            "Open provider settings and pick a provider that "
            "exists — or switch the planner to Auto or Built-in.")

    spec = PROVIDER_REGISTRY.get(pid)
    if spec is not None and spec["protocol"] == "ollama":
        model = tail_model or providers.get("ollama", {}).get(
            "model") or config.get("model") or spec["default_model"]
        return _validate_ollama(mode, config, model, timeout)

    # Effective settings + model, mirroring connfail's pre-flight:
    # a mode label's model wins over the stored one.
    settings = dict(providers.get(pid, {}))
    if tail_model:
        settings["model"] = tail_model
    if custom is not None:
        model = tail_model or custom.get("model") or ""
        api_key = custom.get("api_key") or ""
        key_required = False  # customs are built key-optional
    else:
        model = settings.get("model") or spec["default_model"]
        api_key = settings.get("api_key") or ""
        key_required = not spec["key_optional"]

    # (d) key missing — decided locally, no network.
    if key_required and not api_key:
        return _key_payload(mode, config, model, has_key=False)

    try:
        planner = make_provider_planner(pid, settings, customs=customs)
    except Exception:
        # Construction itself failed (e.g. a hand-edited custom
        # record).  Not one of the definitive classes — let the
        # existing paths surface it rather than guessing.
        return None

    if spec is not None and spec.get("local"):
        return _validate_lmstudio(mode, config, planner, model,
                                  timeout)

    outcome, value = _probe_models(planner, timeout)
    if outcome == "rejected":
        # (d) key rejected by the provider's own answer.
        return _key_payload(mode, config, model,
                            has_key=bool(api_key), raw=value)
    if outcome == "ok" and value is not None and model not in value:
        # (b) the provider's own list doesn't carry the model.
        label = _label(pid, config)
        return _payload(
            "model_unsupported", mode, config, model,
            f"That model isn't on {label}'s list",
            f"The planner is set to "
            f"{connfail.provider_display(mode, config)}, but "
            f"{label}'s own model list doesn't include “{model}”. "
            "The run didn't start, and the configured planner was "
            "not switched.",
            "Pick one of the models below, or fix the model in "
            "provider settings.",
            models=value)
    return None
