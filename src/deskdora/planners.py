"""Planners: turn a goal + observation into a validated list of steps.

The implementations share one step schema and one validator:

* ``OllamaPlanner`` — a local LLM (via Ollama) writes the plan as JSON.
  Output is never trusted: every plan passes the same allow-list
  validation before a single action runs.
* Cloud planners (v3.1) — ``OpenAICompatiblePlanner`` (OpenAI and
  DeepSeek) and ``GeminiPlanner`` use the user's own API key, only
  when the user opts in and selects one.  Off by default; DeskDora
  stays local-first.  Same prompts, same validator, same badging.
* ``BuiltinPlanner`` — a deterministic, rule-based planner with a small
  documented command grammar plus task skills.  It needs no model at
  all, which keeps DeskDora fully testable offline and on modest
  hardware.  The active planner is recorded in every run log and shown
  in the UI — no silent substitution.

Step schema (all steps are dicts):
  {"action": "launch", "command": "thunar /home/user/Downloads"}
  {"action": "click", "element": 12}
  {"action": "click", "target": {"role": "push button",
                                 "name_contains": "Save", "nth": 0}}
  {"action": "type", "text": "hello", "target": {...optional...}}
  {"action": "key", "key": "ctrl+s", "context": "files"?}
  {"action": "scroll", "direction": "down", "target": {...optional...}}
  {"action": "wait", "seconds": 1.0}
  {"action": "extract", "pattern": "capital is (\\w+)", "name": "capital"}
  {"action": "read_clipboard", "name": "clipboard"}
  {"action": "build_sheet", "source": "clipboard", "save_as": "report"}
  {"action": "done", "answer": "..."} / {"action": "fail", "reason": "..."}

``target`` queries are resolved against a *fresh* observation at execution
time, so plans stay valid while windows move and dialogs appear.
``build_sheet`` is a documented macro: it expands, at execution time,
into primitive click/type/key sub-steps (each one logged) that enter a
clipboard table into a spreadsheet grid — the table contents cannot be
known at plan time, so the expansion has to happen in the executor.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error

ALLOWED_ACTIONS = {
    "launch", "click", "double_click", "click_at", "type", "key", "scroll",
    "wait", "extract", "read_clipboard", "build_sheet", "done", "fail",
}
TARGET_KEYS = {"role", "name_contains", "exact_name", "label_contains",
               "app_contains", "nth"}


class PlanError(ValueError):
    pass


def _check_target(target, where):
    if not isinstance(target, dict):
        raise PlanError(f"{where}: target must be an object")
    unknown = set(target) - TARGET_KEYS
    if unknown:
        raise PlanError(f"{where}: unknown target keys {sorted(unknown)}")
    if "nth" in target and not isinstance(target["nth"], int):
        raise PlanError(f"{where}: target.nth must be an int")


def validate_plan(plan, element_count=None):
    """Raise PlanError unless the plan is a list of well-formed steps
    using only allow-listed actions.  Returns the plan unchanged."""
    if not isinstance(plan, list) or not plan:
        raise PlanError("plan must be a non-empty list of steps")
    if len(plan) > 1000:
        raise PlanError("plan too long (>1000 steps)")
    for i, step in enumerate(plan):
        where = f"step {i}"
        if not isinstance(step, dict):
            raise PlanError(f"{where}: step must be an object")
        action = step.get("action")
        if action not in ALLOWED_ACTIONS:
            raise PlanError(f"{where}: action {action!r} is not allow-listed")
        if action == "launch":
            if not isinstance(step.get("command"), str) or not step["command"].strip():
                raise PlanError(f"{where}: launch needs a non-empty command")
        elif action in ("click", "double_click"):
            if "element" in step:
                idx = step["element"]
                if not isinstance(idx, int) or idx < 0:
                    raise PlanError(f"{where}: element must be a non-negative int")
                if element_count is not None and idx >= element_count:
                    raise PlanError(f"{where}: element {idx} out of range")
            elif "target" in step:
                _check_target(step["target"], where)
            else:
                raise PlanError(f"{where}: click needs element or target")
        elif action == "click_at":
            for axis in ("x", "y"):
                v = step.get(axis)
                if not isinstance(v, int) or not (0 <= v <= 10000):
                    raise PlanError(f"{where}: click_at needs int {axis} in 0..10000")
        elif action == "type":
            if not isinstance(step.get("text"), str):
                raise PlanError(f"{where}: type needs text (string)")
            if "target" in step:
                _check_target(step["target"], where)
        elif action == "key":
            if not isinstance(step.get("key"), str) or not step["key"]:
                raise PlanError(f"{where}: key needs a key combo string")
        elif action == "scroll":
            if step.get("direction", "down") not in ("up", "down"):
                raise PlanError(f"{where}: scroll direction must be up|down")
            if "target" in step:
                _check_target(step["target"], where)
        elif action == "wait":
            secs = step.get("seconds", 1)
            if not isinstance(secs, (int, float)) or not (0 <= secs <= 30):
                raise PlanError(f"{where}: wait seconds must be 0..30")
        elif action == "extract":
            pattern = step.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise PlanError(f"{where}: extract needs a pattern")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise PlanError(f"{where}: bad regex: {exc}")
        elif action == "build_sheet":
            if step.get("source") != "clipboard":
                raise PlanError(f"{where}: build_sheet source must be 'clipboard'")
            if not isinstance(step.get("save_as"), str) or not step["save_as"]:
                raise PlanError(f"{where}: build_sheet needs save_as")
        elif action == "fail":
            if not isinstance(step.get("reason"), str):
                raise PlanError(f"{where}: fail needs a reason")
    return plan


# --------------------------------------------------------------------------
# Ollama planner
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the planner of DeskDora, a desktop agent.
Given a goal and a numbered list of on-screen elements, reply with ONLY a
JSON array of step objects — no prose, no markdown. Each step is a JSON
object with an "action" field, using exactly these shapes:
{"action": "launch", "command": "xfce4-terminal"}
{"action": "click", "element": 12}            (the element's [number])
{"action": "click_at", "x": 100, "y": 200}    (only when no element fits)
{"action": "type", "text": "hello"}
{"action": "key", "key": "Return"}
{"action": "scroll", "direction": "down"}
{"action": "wait", "seconds": 1}
{"action": "extract", "pattern": "Capital: ([A-Za-z]+)", "name": "capital"}
{"action": "done", "answer": "what was accomplished"}
{"action": "fail", "reason": "why the goal cannot be done"}
Keep plans short. Always end with a done or fail step.

Example — goal "Open the terminal":
[{"action": "launch", "command": "xfce4-terminal"}, {"action": "wait", "seconds": 2}, {"action": "done", "answer": "Terminal opened"}]"""


class OllamaPlanner:
    name = "ollama"

    def __init__(self, model="qwen2.5:1.5b", host="http://127.0.0.1:11434",
                 timeout=300, memory=None):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.memory = memory

    def available(self):
        try:
            req = urllib.request.Request(self.host + "/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.load(resp)
            return any(m.get("name", "").split(":")[0] == self.model.split(":")[0]
                       for m in data.get("models", []))
        except Exception:
            return False

    def complete_text(self, system, user):
        """Free-form completion via the local model (ask/describe
        helpers).  Raises PlanError when Ollama is unreachable."""
        body = {"model": self.model, "stream": False,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        req = urllib.request.Request(
            self.host + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            return data["message"]["content"]
        except Exception as exc:
            raise PlanError(f"Ollama completion failed: {exc}")

    def plan(self, goal, observation, history=None):
        user = _user_prompt(goal, observation, self.memory, history)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "format": "json",
            # A step plan never needs thousands of tokens; capping
            # generation keeps a small model from rambling past the
            # request timeout instead of finishing its JSON.
            "options": {"temperature": 0.1, "num_predict": 1024},
        }
        req = urllib.request.Request(
            self.host + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PlanError(f"Ollama unreachable or timed out: {exc}")
        content = data.get("message", {}).get("content", "")
        plan = _extract_json_plan(content)
        return validate_plan(plan, element_count=len(observation.elements))

    def pull(self, model=None):
        """Stream a model download (Ollama POST /api/pull).

        Yields Ollama's progress events as dicts, in order: status
        lines ("pulling manifest", "downloading", ...), layer
        lines carrying total/completed byte counts, and a final
        {"status": "success"}.  Raises PlanError when Ollama is
        unreachable or answers with an error, so the caller can
        show the failure instead of hanging on a dead stream.
        """
        name = (model or self.model or "").strip()
        if not name:
            raise PlanError("No model named for the download.")
        body = {"model": name, "name": name, "stream": True}
        req = urllib.request.Request(
            self.host + "/api/pull",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            # The timeout bounds socket inactivity, not the whole
            # download: progress lines keep arriving while bytes
            # flow, and a stalled server gives up within 2 minutes.
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(
                    exc.read().decode("utf-8", "replace")).get(
                        "error", "")
            except Exception:
                pass
            raise PlanError(
                f"Ollama refused the download of {name}: "
                f"{detail or exc.reason or exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PlanError(
                f"Ollama is not reachable at {self.host} — start "
                f"Ollama first, then retry the download. ({exc})"
            ) from exc
        with resp:
            for raw in resp:
                line = raw.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("error"):
                    raise PlanError(
                        f"Ollama download failed: {event['error']}")
                yield event


def _user_prompt(goal, observation, memory=None, history=None):
    """The user message every LLM planner (local or cloud) sends:
    the goal, the numbered on-screen elements, any taught places,
    and notes from a previous attempt."""
    user = (f"Goal: {goal}\n\nOn-screen elements:\n"
            f"{observation.summary_for_planner()}")
    if memory is not None:
        places = memory.snapshot()["places"]
        if places:
            listing = ", ".join(
                f"{alias}={entry['path']}"
                for alias, entry in places.items())
            user += (f"\n\nPlaces the user has taught DeskDora "
                     f"(name=path): {listing}")
    if history:
        user += f"\n\nPrevious attempt notes: {history}"
    return user


# --------------------------------------------------------------------------
# Cloud planners (v3.1) — opt-in, the user's own API key
# --------------------------------------------------------------------------

def _preset(label, protocol, base_url, default_model, models,
            key_optional=False, local=False):
    return {"label": label, "protocol": protocol, "base_url": base_url,
            "default_model": default_model, "models": list(models),
            "key_optional": key_optional, "local": local,
            "env_base": None}


# The provider registry (v3.1): named presets over three wire
# protocols — OpenAI-compatible chat completions, Gemini
# generateContent, Anthropic messages — plus the two local servers.
# Model lists are curated starting points only: the user can always
# type any model ID, and providers with a models endpoint can refresh
# the list live.  A preset's base URL can be overridden per user
# (Azure deployments, proxies, self-hosted gateways).
PROVIDER_REGISTRY = {
    "ollama": _preset(
        "Ollama (local)", "ollama", "http://127.0.0.1:11434",
        "qwen2.5:1.5b",
        ["qwen2.5:1.5b", "qwen2.5:7b", "llama3.1:8b", "mistral:7b"],
        key_optional=True, local=True),
    "lmstudio": _preset(
        "LM Studio (local)", "openai", "http://127.0.0.1:1234/v1",
        "qwen2.5-7b-instruct",
        ["qwen2.5-7b-instruct", "llama-3.1-8b-instruct"],
        key_optional=True, local=True),
    "openai": _preset(
        "OpenAI", "openai", "https://api.openai.com/v1",
        "gpt-4o-mini",
        ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "o4-mini"]),
    "gemini": _preset(
        "Google Gemini", "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini-2.0-flash",
        ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-2.5-pro",
         "gemini-2.0-flash-lite"]),
    "anthropic": _preset(
        "Anthropic Claude", "anthropic", "https://api.anthropic.com/v1",
        "claude-sonnet-4-5",
        ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5"]),
    "deepseek": _preset(
        "DeepSeek", "openai", "https://api.deepseek.com/v1",
        "deepseek-chat", ["deepseek-chat", "deepseek-reasoner"]),
    "qwen": _preset(
        "Qwen (Alibaba)", "openai",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus", ["qwen-plus", "qwen-max", "qwen-turbo"]),
    "moonshot": _preset(
        "Moonshot Kimi", "openai", "https://api.moonshot.ai/v1",
        "moonshot-v1-8k",
        ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
         "kimi-k2-0711-preview"]),
    "mistral": _preset(
        "Mistral AI", "openai", "https://api.mistral.ai/v1",
        "mistral-small-latest",
        ["mistral-small-latest", "mistral-medium-latest",
         "mistral-large-latest", "codestral-latest"]),
    "groq": _preset(
        "Groq", "openai", "https://api.groq.com/openai/v1",
        "llama-3.3-70b-versatile",
        ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
         "openai/gpt-oss-120b"]),
    "together": _preset(
        "Together AI", "openai", "https://api.together.xyz/v1",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        ["meta-llama/Llama-3.3-70B-Instruct-Turbo",
         "Qwen/Qwen2.5-72B-Instruct-Turbo", "deepseek-ai/DeepSeek-V3"]),
    "openrouter": _preset(
        "OpenRouter", "openai", "https://openrouter.ai/api/v1",
        "openai/gpt-4o-mini",
        ["openai/gpt-4o-mini", "anthropic/claude-sonnet-4.5",
         "google/gemini-2.5-flash", "deepseek/deepseek-chat"]),
    "xai": _preset(
        "xAI Grok", "openai", "https://api.x.ai/v1",
        "grok-4", ["grok-4", "grok-3", "grok-3-mini"]),
    "cohere": _preset(
        "Cohere", "openai", "https://api.cohere.ai/compatibility/v1",
        "command-a-03-2025",
        ["command-a-03-2025", "command-r-plus", "command-r"]),
    "perplexity": _preset(
        "Perplexity", "openai", "https://api.perplexity.ai",
        "sonar", ["sonar", "sonar-pro", "sonar-reasoning-pro"]),
    "fireworks": _preset(
        "Fireworks AI", "openai", "https://api.fireworks.ai/inference/v1",
        "accounts/fireworks/models/llama-v3p3-70b-instruct",
        ["accounts/fireworks/models/llama-v3p3-70b-instruct",
         "accounts/fireworks/models/qwen2p5-72b-instruct",
         "accounts/fireworks/models/deepseek-v3"]),
    "cerebras": _preset(
        "Cerebras", "openai", "https://api.cerebras.ai/v1",
        "llama-3.3-70b", ["llama-3.3-70b", "llama3.1-8b", "qwen-3-32b"]),
    "sambanova": _preset(
        "SambaNova", "openai", "https://api.sambanova.ai/v1",
        "Meta-Llama-3.3-70B-Instruct",
        ["Meta-Llama-3.3-70B-Instruct", "DeepSeek-V3.1", "Qwen3-32B"]),
    "nvidia": _preset(
        "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1",
        "meta/llama-3.3-70b-instruct",
        ["meta/llama-3.3-70b-instruct", "deepseek-ai/deepseek-r1",
         "qwen/qwen2.5-72b-instruct"]),
    "github": _preset(
        "GitHub Models", "openai", "https://models.github.ai/inference",
        "openai/gpt-4o-mini",
        ["openai/gpt-4o-mini", "openai/gpt-4o",
         "meta/Llama-3.3-70B-Instruct"]),
    "azure": _preset(
        "Azure OpenAI", "openai",
        "https://YOUR-RESOURCE.openai.azure.com/openai/v1",
        "gpt-4o-mini", ["gpt-4o-mini", "gpt-4o"]),
}

# Base-URL override env vars (used by the test-suite's mock servers,
# and handy for proxies): DESKDORA_<PRESET_ID>_BASE_URL.
for _pid, _spec in PROVIDER_REGISTRY.items():
    _spec["env_base"] = f"DESKDORA_{_pid.upper()}_BASE_URL"

# Backwards-compatible alias: the cloud presets are the registry
# minus the local servers.
PROVIDERS = {pid: spec for pid, spec in PROVIDER_REGISTRY.items()
             if not spec["local"]}


def _http_json(url, body, headers, timeout, provider_label):
    """POST JSON and parse the JSON reply, raising PlanError with the
    provider's own error message on failure.  The API key travels in
    a header and is never included in any error text."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            err = payload.get("error") or {}
            detail = err.get("message") or payload.get("message") or ""
        except Exception:
            pass
        raise PlanError(
            f"{provider_label} request failed (HTTP {exc.code})"
            + (f": {detail}" if detail else ""))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PlanError(f"{provider_label} unreachable or timed out: {exc}")


def _http_get_json(url, headers, timeout, provider_label):
    """GET JSON with the same error semantics as ``_http_json``."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            err = payload.get("error") or {}
            detail = err.get("message") or payload.get("message") or ""
        except Exception:
            pass
        raise PlanError(
            f"{provider_label} request failed (HTTP {exc.code})"
            + (f": {detail}" if detail else ""))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PlanError(f"{provider_label} unreachable or timed out: {exc}")


class _CloudPlannerBase:
    """Shared behaviour for the opt-in cloud planners: same system
    prompt, same user prompt, same plan extraction + validation as
    the local Ollama planner.  Subclasses implement ``_complete``
    (and optionally ``fetch_models``) for one wire protocol."""

    cloud = True

    def __init__(self, provider, label, api_key="", model="",
                 base_url="", default_model="", key_optional=False,
                 timeout=120, memory=None):
        self.provider = provider
        self.label = label
        self.name = provider
        self.api_key = api_key or ""
        self.model = model or default_model
        self.base_url = (base_url or "").rstrip("/")
        self.key_optional = key_optional
        self.timeout = timeout
        self.memory = memory

    def available(self):
        """A cloud planner is 'available' when a key is configured
        (or none is needed); reachability is what the Test-connection
        button checks."""
        return bool(self.api_key) or self.key_optional

    def _require_key(self):
        if not self.api_key and not self.key_optional:
            raise PlanError(
                f"no API key configured for {self.label} — add one in "
                "Settings → AI Providers, or pick a local planner.")

    def fetch_models(self):
        """Live model list from the provider's models endpoint, or
        None when the protocol has none.  Bonus, never required: the
        curated registry list and free-text model IDs always work."""
        return None

    def plan(self, goal, observation, history=None):
        self._require_key()
        user = _user_prompt(goal, observation, self.memory, history)
        content = self._complete(SYSTEM_PROMPT, user)
        plan = _extract_json_plan(content)
        return validate_plan(plan, element_count=len(observation.elements))

    def test_connection(self):
        """One minimal completion to prove key + endpoint + model.
        Returns (ok, detail).  Costs a handful of tokens."""
        try:
            self._require_key()
            text = self._complete(
                "You are a connection test. Reply with exactly: OK",
                "ping", max_tokens=16)
        except PlanError as exc:
            return False, str(exc)
        return True, f"{self.label} answered via {self.model}: {text[:60]!r}"

    def _complete(self, system, user, max_tokens=1024):
        raise NotImplementedError

    def complete_text(self, system, user):
        """Free-form completion for read-only helpers (ask about
        screen, plain-words routines).  Not used for planning."""
        return self._complete(system, user)


class OpenAICompatiblePlanner(_CloudPlannerBase):
    """The OpenAI chat-completions protocol.  Most providers speak it
    on their own base URL (DeepSeek, Groq, Mistral, OpenRouter, …),
    and local servers like LM Studio do too — one class serves all."""

    def _auth_headers(self):
        return ({"Authorization": f"Bearer {self.api_key}"}
                if self.api_key else {})

    def _complete(self, system, user, max_tokens=1024):
        data = _http_json(
            self.base_url + "/chat/completions",
            {"model": self.model,
             "messages": [{"role": "system", "content": system},
                          {"role": "user", "content": user}],
             "temperature": 0.1, "max_tokens": max_tokens},
            self._auth_headers(), self.timeout, self.label)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise PlanError(
                f"{self.label} returned an unexpected response shape")

    def fetch_models(self):
        data = _http_get_json(self.base_url + "/models",
                              self._auth_headers(), 30, self.label)
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return sorted(ids) or None


class GeminiPlanner(_CloudPlannerBase):
    """Google Gemini generateContent protocol."""

    def _complete(self, system, user, max_tokens=1024):
        data = _http_json(
            f"{self.base_url}/models/{self.model}:generateContent",
            {"system_instruction": {"parts": [{"text": system}]},
             "contents": [{"role": "user", "parts": [{"text": user}]}],
             "generationConfig": {"temperature": 0.1,
                                  "maxOutputTokens": max_tokens,
                                  "responseMimeType": "application/json"}},
            {"x-goog-api-key": self.api_key},
            self.timeout, self.label)
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise PlanError(
                f"{self.label} returned no candidates"
                + (f": {feedback.get('blockReason')}" if feedback else ""))
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
        if not text:
            raise PlanError(f"{self.label} returned an empty response")
        return text

    def fetch_models(self):
        data = _http_get_json(
            self.base_url + "/models",
            {"x-goog-api-key": self.api_key}, 30, self.label)
        names = []
        for entry in data.get("models", []):
            name = entry.get("name") or ""
            names.append(name.removeprefix("models/"))
        return sorted(n for n in names if n) or None


class AnthropicPlanner(_CloudPlannerBase):
    """Anthropic's messages protocol."""

    def _headers(self):
        return {"x-api-key": self.api_key,
                "anthropic-version": "2023-06-01"}

    def _complete(self, system, user, max_tokens=1024):
        data = _http_json(
            self.base_url + "/messages",
            {"model": self.model, "max_tokens": max_tokens,
             "system": system, "temperature": 0.1,
             "messages": [{"role": "user", "content": user}]},
            self._headers(), self.timeout, self.label)
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks
                       if b.get("type") == "text")
        if not text:
            raise PlanError(f"{self.label} returned an empty response")
        return text

    def fetch_models(self):
        data = _http_get_json(self.base_url + "/models",
                              self._headers(), 30, self.label)
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return sorted(ids) or None


_PROTOCOL_CLASSES = {"openai": OpenAICompatiblePlanner,
                     "gemini": GeminiPlanner,
                     "anthropic": AnthropicPlanner}

CUSTOM_PROTOCOLS = tuple(_PROTOCOL_CLASSES)


def validate_custom_provider(data):
    """Validate a user-defined provider definition (Settings → AI
    Providers → Custom).  This is endpoint *configuration* only —
    a name, a base URL, a protocol, a model, a key.  DeskDora never
    executes user-supplied code to reach a provider."""
    name = str(data.get("name") or "").strip()
    protocol = str(data.get("protocol") or "").strip()
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    model = str(data.get("model") or "").strip()
    if not name:
        raise ValueError("custom provider needs a name")
    if protocol not in CUSTOM_PROTOCOLS:
        raise ValueError(
            f"protocol must be one of {sorted(CUSTOM_PROTOCOLS)}")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    if not model:
        raise ValueError("custom provider needs a model ID")
    return {"name": name, "protocol": protocol, "base_url": base_url,
            "model": model, "api_key": str(data.get("api_key") or "")}


def make_provider_planner(provider_id, settings=None, memory=None,
                          customs=None):
    """Build the planner for a registry preset id or a user-defined
    custom provider id, from stored settings.  A preset's base URL
    may be overridden by its stored setting or by the
    DESKDORA_<ID>_BASE_URL env var (used by the test-suite's local
    mock servers)."""
    settings = settings or {}
    if provider_id in PROVIDER_REGISTRY:
        spec = PROVIDER_REGISTRY[provider_id]
        if spec["protocol"] == "ollama":
            return OllamaPlanner(
                model=settings.get("model") or spec["default_model"],
                memory=memory)
        base_url = (os.environ.get(spec["env_base"])
                    or settings.get("base_url") or spec["base_url"])
        cls = _PROTOCOL_CLASSES[spec["protocol"]]
        return cls(provider_id, spec["label"],
                   api_key=settings.get("api_key", ""),
                   model=settings.get("model", ""),
                   base_url=base_url,
                   default_model=spec["default_model"],
                   key_optional=spec["key_optional"], memory=memory)
    for custom in customs or []:
        if custom.get("id") == provider_id:
            cls = _PROTOCOL_CLASSES[custom["protocol"]]
            return cls(provider_id, custom.get("name") or provider_id,
                       api_key=custom.get("api_key", ""),
                       model=custom.get("model", ""),
                       base_url=custom.get("base_url", ""),
                       default_model="", key_optional=True,
                       memory=memory)
    raise PlanError(f"unknown planner provider: {provider_id!r}")


def make_cloud_planner(provider, settings=None, memory=None):
    """Alias kept for the original three-provider call sites."""
    return make_provider_planner(provider, settings, memory=memory)


def _extract_json_plan(content):
    content = content.strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("["), content.rfind("]")
        if start < 0 or end <= start:
            raise PlanError("planner did not return a JSON plan")
        try:
            parsed = json.loads(content[start:end + 1])
        except json.JSONDecodeError as exc:
            raise PlanError(f"planner returned invalid JSON: {exc}")
    if isinstance(parsed, dict) and "steps" in parsed:
        parsed = parsed["steps"]
    if isinstance(parsed, dict) and "action" in parsed:
        # Small models often emit a single step object instead of an
        # array (Ollama's JSON mode pushes them toward one object).
        # Wrapping it is a shape normalisation, not an invention.
        parsed = [parsed]
    if not isinstance(parsed, list):
        raise PlanError("planner output is not a step list")
    return parsed


# --------------------------------------------------------------------------
# Built-in deterministic planner
# --------------------------------------------------------------------------

FILE_TYPE_GROUPS = {
    "Images": ["png", "jpg", "jpeg", "gif", "bmp", "svg", "webp"],
    "Documents": ["pdf", "txt", "md", "doc", "docx", "odt", "rtf"],
    "Audio": ["mp3", "wav", "ogg", "flac", "m4a"],
    "Video": ["mp4", "mkv", "webm", "mov", "avi"],
    "Archives": ["zip", "tar", "gz", "bz2", "xz", "7z", "rar"],
}


class BuiltinPlanner:
    """Rule-based planner with a small, documented grammar.

    Supported goal shapes (case-insensitive):
      * "Organise folder <path> by type"
      * "Organise folder my <place> by type" / "Organise my <place>
        folder" — resolved through resident memory (v2.0)
      * "Set the appearance theme to <ThemeName>" / "Change theme to <X>"
      * "Enable/Disable event sounds in Appearance settings"
      * "Fill the form: name=..., email=..., city=... and submit"
      * "Open the article about <topic> and report its <thing>"
      * "Create a spreadsheet from <file.pdf> saved as <name>"
      * "Open <app>" / "Click <name>" / chains joined with " then "

    Parsing is *strict* (v1.1): a rule must consume the entire goal.
    A goal that is only partly understood raises PlanError ("I can't
    parse this goal…") and nothing is executed — the agent never
    partially carries out a goal and then reports success.
    """

    name = "builtin"

    def __init__(self, known_apps=None, memory=None):
        # Resident memory (deskdora.memory.MemoryStore) or None.  When
        # present, a folder token that is not a literal path is
        # resolved through remembered places ("my messy folder").
        self.memory = memory
        self.apps = {
            "files": "thunar",
            "file manager": "thunar",
            "terminal": "xfce4-terminal",
            "editor": "mousepad",
            "text editor": "mousepad",
            "settings": "xfce4-settings-manager",
            "appearance": "xfce4-appearance-settings",
            "browser": "chromium",
            "spreadsheet": "gnumeric",
        }
        if known_apps:
            self.apps.update(known_apps)

    def _resolve_folder(self, token):
        """A folder token from a goal: a literal path if it exists,
        else a remembered place (resident memory).  Unresolvable
        tokens refuse honestly — the run must not organise the wrong
        folder or silently do nothing."""
        import os
        path = os.path.expanduser(token)
        if os.path.isdir(path):
            return path
        if self.memory is not None:
            resolved = self.memory.resolve_place(token)
            if resolved and os.path.isdir(resolved):
                return resolved
            raise PlanError(
                f"folder not found: {token} — and no remembered place "
                "matches that name. Nothing was executed.")
        return path

    # -- skills ---------------------------------------------------------
    def _organise(self, path):
        import os
        if not os.path.isdir(path):
            raise PlanError(f"folder not found: {path}")
        present = {}
        files_for = {}
        for entry in os.listdir(path):
            full = os.path.join(path, entry)
            if os.path.isfile(full) and "." in entry:
                ext = entry.rsplit(".", 1)[1].lower()
                for folder, exts in FILE_TYPE_GROUPS.items():
                    if ext in exts:
                        present.setdefault(folder, set()).add(ext)
                        files_for.setdefault((folder, ext),
                                             []).append(entry)
        if not present:
            raise PlanError(f"no organisable files found in {path}")
        steps = [{"action": "launch", "command": f"thunar {path}"},
                 {"action": "wait", "seconds": 2}]
        for folder in present:
            steps += [
                {"action": "key", "key": "ctrl+shift+n"},
                {"action": "wait", "seconds": 0.7},
                {"action": "type", "text": folder},
                {"action": "key", "key": "Return"},
                {"action": "wait", "seconds": 0.7},
            ]
        for folder, exts in present.items():
            for ext in sorted(exts):
                # The paste step completes a batch of file moves;
                # it carries file_op metadata so the agent can
                # journal exactly which files went where (undo).
                paste = {"action": "key", "key": "ctrl+v",
                         "file_op": {
                             "op": "move",
                             "files": [
                                 {"src": f"{path}/{name}",
                                  "dst": f"{path}/{folder}/{name}"}
                                 for name in
                                 files_for.get((folder, ext), [])]}}
                steps += [
                    {"action": "key", "key": "ctrl+s"},
                    {"action": "wait", "seconds": 0.7},
                    {"action": "type", "text": f"*.{ext}"},
                    {"action": "key", "key": "Return"},
                    {"action": "wait", "seconds": 0.5},
                    {"action": "key", "key": "ctrl+x"},
                    {"action": "key", "key": "ctrl+l"},
                    {"action": "type", "text": f"{path}/{folder}"},
                    {"action": "key", "key": "Return"},
                    {"action": "wait", "seconds": 0.5},
                    paste,
                    {"action": "wait", "seconds": 0.5},
                    {"action": "key", "key": "alt+Up"},
                    {"action": "wait", "seconds": 0.5},
                ]
        steps.append({"action": "done", "answer": f"Folder {path} organised by type"})
        return steps

    def _theme(self, theme):
        return [
            {"action": "launch", "command": self.apps["appearance"]},
            {"action": "wait", "seconds": 2},
            {"action": "click", "target": {"name_contains": theme}},
            {"action": "wait", "seconds": 1},
            {"action": "click", "target": {"role": "push button",
                                           "name_contains": "Close"}},
            {"action": "done", "answer": f"Theme set to {theme}"},
        ]

    def _sounds(self, checkbox="Enable event sounds"):
        return [
            {"action": "launch", "command": self.apps["appearance"]},
            {"action": "wait", "seconds": 2},
            {"action": "click", "target": {"role": "page tab",
                                           "name_contains": "Settings"}},
            {"action": "wait", "seconds": 1},
            {"action": "click", "target": {"role": "check box",
                                           "name_contains": checkbox}},
            {"action": "wait", "seconds": 0.5},
            {"action": "click", "target": {"role": "push button",
                                           "name_contains": "Close"}},
            {"action": "done", "answer": f"{checkbox} toggled"},
        ]

    def _form(self, pairs, submit):
        steps = [{"action": "launch", "command": self.apps["form"]},
                 {"action": "wait", "seconds": 2}]
        for field_name, value in pairs:
            steps.append({"action": "type", "text": value,
                          "target": {"label_contains": field_name}})
            steps.append({"action": "wait", "seconds": 0.3})
        if submit:
            steps.append({"action": "click", "target": {
                "role": "push button", "name_contains": "Submit"}})
            steps.append({"action": "wait", "seconds": 1})
            answer = "Form submitted"
        else:
            # Never claim a submission that was not requested/performed.
            answer = "Form filled (not submitted)"
        steps.append({"action": "done", "answer": answer})
        return steps

    def _parse_form_spec(self, spec):
        """Parse 'name=Ada, city=Pune' into (pairs, submit).

        Strict on purpose (the v1.1 parse fix): every character of the
        spec must be consumed by a field=value pair, a separator, or
        the trailing submit marker.  Anything left over — a second
        instruction glued onto the goal, an 'and'-separated field, a
        truncated value — refuses the whole goal instead of silently
        filling part of the form and reporting success.
        """
        if re.search(r"\s+then\s+", spec, flags=re.I):
            raise PlanError(
                "I can't parse this goal: a form specification cannot "
                "contain a 'then' chain — run the steps as separate "
                "goals. Nothing was executed.")
        submit = False
        m = re.search(r"(?:\s+and)?\s+submit\s*$", spec, flags=re.I)
        if m:
            submit = True
            spec = spec[:m.start()]
        pairs = []
        spans = []
        for match in re.finditer(r"(\w+)\s*=\s*([^,]+)", spec):
            field_name, value = match.group(1), match.group(2).strip()
            if not value:
                raise PlanError(
                    f"I can't parse this goal: field {field_name!r} has "
                    "no value. Nothing was executed.")
            if re.search(r"\w+\s*=", value):
                raise PlanError(
                    f"I can't parse this goal: the value for "
                    f"{field_name!r} contains another 'field=' — fields "
                    "must be separated by commas. Nothing was executed.")
            if re.search(r"(?i)\b(?:and|then)\s+(?:delete|remove|erase|"
                         r"format|send|submit|open|click|press|type|"
                         r"launch|install|uninstall|empty|move|copy|"
                         r"run)\b", value):
                raise PlanError(
                    f"I can't parse this goal: the value for "
                    f"{field_name!r} appears to contain a second "
                    "instruction. Nothing was executed.")
            pairs.append((field_name, value))
            spans.append(match.span())
        if not pairs:
            raise PlanError(
                "I can't parse this goal: a form goal needs "
                "field=value pairs. Nothing was executed.")
        residue = []
        pos = 0
        for start, end in spans:
            residue.append(spec[pos:start])
            pos = end
        residue.append(spec[pos:])
        leftover = "".join(residue).strip(" \t,;")
        if leftover:
            raise PlanError(
                f"I can't parse this goal: {leftover!r} is not a "
                "field=value pair. Nothing was executed.")
        return pairs, submit

    def _article(self, topic, thing):
        patterns = {
            "capital": r"capital of [A-Za-z ]+? is ([A-Za-z ]+?)[,\.\n]",
            "river": r"river is (?:the )?([A-Za-z ]+?)[,\.\n]",
        }
        pattern = patterns.get(thing.lower(),
                               rf"{re.escape(thing)}[:\s]+([A-Za-z0-9 ,\-]+?)[\.\n]")
        return [
            {"action": "launch",
             "command": f"{self.apps['browser']} {self.apps['site_url']}"},
            {"action": "wait", "seconds": 5},
            {"action": "click", "target": {"role": "link",
                                           "name_contains": topic}},
            {"action": "wait", "seconds": 2},
            {"action": "extract", "pattern": pattern, "name": thing},
            {"action": "done"},
        ]

    def _sheet_from_pdf(self, pdf_path, save_as):
        return [
            {"action": "launch",
             "command": f"{self.apps['browser']} file://{pdf_path}"},
            {"action": "wait", "seconds": 9},
            {"action": "click_at", "x": 640, "y": 450},
            {"action": "wait", "seconds": 1},
            {"action": "key", "key": "ctrl+a"},
            {"action": "key", "key": "ctrl+c"},
            {"action": "read_clipboard", "name": "table"},
            {"action": "launch", "command": self.apps["spreadsheet"]},
            {"action": "wait", "seconds": 2},
            {"action": "build_sheet", "source": "clipboard",
             "save_as": save_as},
            {"action": "done", "answer": f"Spreadsheet saved as {save_as}"},
        ]

    # -- dispatch ---------------------------------------------------------
    def plan(self, goal, observation, history=None):
        """Plan a goal, or refuse it.

        v1.1 parse fix: every grammar rule must consume the *entire*
        goal (full-string matches, and a residue check for the form
        grammar).  v1.0 matched prefixes — a truncated or compound
        goal could be partially executed and then reported as success
        (e.g. extra instructions glued after a form spec were silently
        dropped).  A goal that is not fully understood now raises
        PlanError("I can't parse this goal…") and nothing executes.
        """
        g = goal.strip()
        cleaned = _clean_goal(g)
        low = cleaned.lower()
        m = re.fullmatch(r"organi[sz]e (?:the )?folder (?:my )?(\S+)"
                         r"(?: by type)?", cleaned, re.I) or re.fullmatch(
                         r"organi[sz]e my (\S+) folder(?: by type)?",
                         cleaned, re.I)
        if m:
            return validate_plan(self._organise(
                self._resolve_folder(m.group(1))))
        m = re.fullmatch(
            r"(?:enable|disable|turn on|turn off) (?:the )?"
            r"(event sounds|input feedback sounds)"
            r"(?: in (?:the )?appearance(?: settings)?)?", low)
        if m:
            checkbox = ("Enable event sounds" if m.group(1) == "event sounds"
                        else "Enable input feedback sounds")
            return validate_plan(self._sounds(checkbox))
        if " then " not in low:
            m = re.fullmatch(
                r"(?:set|change)(?: the)?(?: appearance)? theme to "
                r"([A-Za-z0-9 _\-]+)", cleaned, re.I)
            if m:
                theme = m.group(1).strip()
                if " and " in theme.lower():
                    raise PlanError(
                        f"I can't parse this goal: the theme name "
                        f"{theme!r} looks like it contains a second "
                        "instruction. Nothing was executed.")
                return validate_plan(self._theme(theme))
        m = re.fullmatch(r"fill (?:out )?(?:the )?form[:\s]+(.+)", cleaned,
                         re.I)
        if m:
            pairs, submit = self._parse_form_spec(m.group(1))
            return validate_plan(self._form(pairs, submit))
        m = re.fullmatch(
            r"open the article about ([\w ]+?) and report (?:its|the) (\w+)",
            cleaned, re.I)
        if m:
            return validate_plan(self._article(m.group(1).strip(),
                                               m.group(2)))
        m = re.fullmatch(
            r"(?:create|build|make) a spreadsheet from (\S+\.pdf)"
            r"(?: saved as (\S+))?", cleaned, re.I)
        if m:
            save_as = m.group(2) or "spreadsheet.gnumeric"
            return validate_plan(self._sheet_from_pdf(m.group(1), save_as))
        if " then " in low:
            steps = []
            for part in re.split(r"\s+then\s+", g, flags=re.I):
                sub = self.plan(part, observation)
                steps.extend(sub[:-1] if sub and sub[-1].get("action") == "done" else sub)
            steps.append({"action": "done"})
            return validate_plan(steps)
        m = re.fullmatch(r"open (?:the )?([a-z0-9 _\-]+)", low)
        if m:
            phrase = m.group(1).strip()
            words = phrase.split()
            # An app name is a noun phrase of at most three words.
            # Anything longer — or containing a conjunction — is a
            # sentence this rule would otherwise swallow whole and
            # "open" as if it were an application (a v1.0 trap: the
            # article grammar's leftovers landed here).
            if len(words) <= 3 and "and" not in words:
                app = self.apps.get(phrase, phrase)
                return validate_plan([
                    {"action": "launch", "command": app},
                    {"action": "wait", "seconds": 2},
                    {"action": "done", "answer": f"Opened {phrase}"}])
        m = re.fullmatch(r"click (?:on )?(.+)", g, re.I)
        if m:
            return validate_plan([
                {"action": "click", "target": {"name_contains": m.group(1).strip()}},
                {"action": "done"}])
        m = re.fullmatch(r"press ([a-z0-9_+\-]+)", g, re.I)
        if m:
            return validate_plan([
                {"action": "key", "key": m.group(1)},
                {"action": "done"}])
        m = re.match(r"type (.+)$", g, re.I)
        if m:
            return validate_plan([
                {"action": "type", "text": m.group(1)},
                {"action": "done"}])
        raise PlanError(
            f"I can't parse this goal with the built-in planner: "
            f"{goal.strip()!r}. Nothing was executed. The built-in "
            "grammar covers organising a folder, appearance settings, "
            "form fills, article facts, PDF-to-spreadsheet, and "
            "open/click/type/press steps joined with 'then'. Try the "
            "Ollama planner for free-form goals.")


def _clean_goal(goal):
    """Strip trailing punctuation and politeness fillers so grammar
    rules can anchor on the full string ('…by type.' or '…please'
    still parse; a second instruction after them does not)."""
    g = goal.strip()
    g = re.sub(r"(?i)[\s.!]+$", "", g)
    g = re.sub(r"(?i)\s+(please|thanks|thank you)$", "", g)
    return g.strip()


def get_planner(mode="auto", model="qwen2.5:1.5b", known_apps=None,
                memory=None, providers=None, customs=None):
    """Resolve a planner by mode.  'auto' prefers Ollama when a server
    with the model is actually reachable, else the built-in planner.
    A registry preset id ('openai', 'gemini', 'groq', …) or a custom
    provider id builds that provider's planner from stored settings.
    Returns (planner, mode_label)."""
    if mode == "builtin":
        return BuiltinPlanner(known_apps, memory=memory), "builtin"
    if mode == "ollama" or (mode in PROVIDER_REGISTRY
                            and PROVIDER_REGISTRY[mode]["protocol"]
                            == "ollama"):
        omodel = (providers or {}).get("ollama", {}).get("model") or model
        return (OllamaPlanner(model=omodel, memory=memory),
                f"ollama:{omodel}")
    if mode in PROVIDER_REGISTRY or any(
            c.get("id") == mode for c in customs or []):
        planner = make_provider_planner(
            mode, (providers or {}).get(mode, {}), memory=memory,
            customs=customs)
        return planner, f"{mode}:{planner.model}"
    ollama = OllamaPlanner(model=model, memory=memory)
    if ollama.available():
        return ollama, f"ollama:{model}"
    return BuiltinPlanner(known_apps, memory=memory), "builtin"
