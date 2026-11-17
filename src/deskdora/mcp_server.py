"""DeskDora MCP server (v3.1): exposes DeskDora to MCP clients
(Claude Desktop, IDEs, other agents) over stdio.

The server is a thin client of DeskDora's own HTTP API — every tool
call goes through the running DeskDora service, so the mandatory
confirmations, the per-category autonomy policy and the kill switch
apply to MCP clients exactly as they do to the UI.  There is no
bypass: a destructive step an MCP client starts still blocks until
a human approves it (via any surface: UI, companion, or the
approve_confirmation tool below — which is itself a human-driven
client action).

Protocol: MCP over stdio (newline-delimited JSON-RPC 2.0),
implemented on the standard library only — DeskDora's install stays
dependency-light and fully offline.

Configuration (environment):
  DESKDORA_URL      service base URL (default http://127.0.0.1:8765)
  DESKDORA_API_KEY  API key to present (needed only when the service
                    is reached over a non-loopback address)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from . import __version__

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
DEFAULT_PROTOCOL = "2025-03-26"


class ServiceError(Exception):
    pass


class DeskDoraClient:
    def __init__(self, base_url=None, api_key=None):
        self.base = (base_url or os.environ.get(
            "DESKDORA_URL", "http://127.0.0.1:8765")).rstrip("/")
        self.api_key = api_key or os.environ.get("DESKDORA_API_KEY", "")

    def call(self, method, path, body=None, timeout=30):
        headers = {"Content-Type": "application/json",
                   "X-DeskDora-Client": "mcp"}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(
                    exc.read().decode("utf-8", "replace")).get(
                        "detail", "")
            except Exception:
                pass
            raise ServiceError(
                f"DeskDora service: HTTP {exc.code}"
                + (f" — {detail}" if detail else ""))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ServiceError(
                f"cannot reach the DeskDora service at {self.base}: "
                f"{exc}. Is DeskDora running?")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _text(payload):
    return {"content": [{"type": "text", "text": payload}]}


def _json_text(obj):
    return _text(json.dumps(obj, indent=2))


def tool_run_task(client, args):
    goal = (args.get("goal") or "").strip()
    if not goal:
        raise ServiceError("goal is required")
    body = {"goal": goal}
    if args.get("planner"):
        body["planner"] = args["planner"]
    client.call("POST", "/api/run", body)
    return _text(f"Run started: {goal}\n"
                 "Poll get_status for progress; if the run needs a "
                 "confirmation it will block until one is given.")


def tool_get_status(client, args):
    st = client.call("GET", "/api/status")
    out = {
        "running": st["state"]["running"],
        "goal": st["state"]["goal"],
        "planner_mode": st["state"]["planner_mode"],
        "result": st["state"]["result"],
        "kill_tripped": st["kill_tripped"],
        "pending_confirmation": st["pending_confirmation"],
    }
    return _json_text(out)


def tool_list_runs(client, args):
    limit = int(args.get("limit") or 10)
    runs = client.call("GET", "/api/runs")
    trimmed = [{"id": r["id"], "goal": r["goal"], "status": r["status"],
                "planner_mode": r["planner_mode"]}
               for r in runs[:limit]]
    return _json_text(trimmed)


def tool_get_run_log(client, args):
    run_id = (args.get("run_id") or "").strip()
    if not run_id:
        raise ServiceError("run_id is required")
    detail = client.call("GET", f"/api/runs/{run_id}")
    events = detail["events"]
    return _json_text({"id": detail["id"], "events_total": len(events),
                       "events": events[-50:]})


def _answer_confirmation(client, args, approved):
    cid = (args.get("confirmation_id") or "").strip()
    if not cid:
        st = client.call("GET", "/api/status")
        pending = st.get("pending_confirmation")
        if not pending:
            raise ServiceError("no confirmation is pending")
        cid = pending["id"]
    client.call("POST", f"/api/confirmations/{cid}",
                {"approved": approved})
    return _text(f"Confirmation {cid} "
                 f"{'approved' if approved else 'denied'}.")


def tool_approve_confirmation(client, args):
    return _answer_confirmation(client, args, True)


def tool_deny_confirmation(client, args):
    return _answer_confirmation(client, args, False)


def tool_stop(client, args):
    client.call("POST", "/api/kill")
    return _text("Kill switch tripped: the agent stops before its "
                 "next step.")


TOOLS = [
    {"name": "run_task",
     "description": "Give DeskDora a desktop task in plain words. "
                    "The run goes through DeskDora's full safety "
                    "layer: confirmations, autonomy policy, kill "
                    "switch. Returns immediately; poll get_status.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "goal": {"type": "string",
                                  "description": "The task, in plain words"},
                         "planner": {"type": "string",
                                     "description": "Optional planner "
                                     "override (auto, builtin, ollama, "
                                     "or a provider id)"}},
                     "required": ["goal"]},
     "handler": tool_run_task},
    {"name": "get_status",
     "description": "Current agent state: running or not, the goal, "
                    "the active planner, the last result, and any "
                    "pending confirmation.",
     "inputSchema": {"type": "object", "properties": {}},
     "handler": tool_get_status},
    {"name": "list_runs",
     "description": "Recent runs (newest first) with their goals "
                    "and outcomes.",
     "inputSchema": {"type": "object",
                     "properties": {"limit": {"type": "integer",
                                              "default": 10}}},
     "handler": tool_list_runs},
    {"name": "get_run_log",
     "description": "The audit log events of one run (last 50).",
     "inputSchema": {"type": "object",
                     "properties": {"run_id": {"type": "string"}},
                     "required": ["run_id"]},
     "handler": tool_get_run_log},
    {"name": "approve_confirmation",
     "description": "Approve the pending confirmation so the run "
                    "continues. With no confirmation_id, approves "
                    "the currently pending one.",
     "inputSchema": {"type": "object",
                     "properties": {"confirmation_id":
                                    {"type": "string"}}},
     "handler": tool_approve_confirmation},
    {"name": "deny_confirmation",
     "description": "Deny the pending confirmation; the run stops.",
     "inputSchema": {"type": "object",
                     "properties": {"confirmation_id":
                                    {"type": "string"}}},
     "handler": tool_deny_confirmation},
    {"name": "stop",
     "description": "Trip DeskDora's kill switch: the agent halts "
                    "before its next step.",
     "inputSchema": {"type": "object", "properties": {}},
     "handler": tool_stop},
]

_TOOL_MAP = {t["name"]: t for t in TOOLS}


# ---------------------------------------------------------------------------
# JSON-RPC over stdio
# ---------------------------------------------------------------------------

class McpServer:
    def __init__(self, client=None):
        self.client = client or DeskDoraClient()

    def handle(self, message):
        """One parsed JSON-RPC message -> response dict or None."""
        if not isinstance(message, dict):
            return self._error(None, -32600, "invalid request")
        method = message.get("method")
        mid = message.get("id")
        if method == "initialize":
            requested = (message.get("params") or {}).get(
                "protocolVersion")
            version = (requested if requested in PROTOCOL_VERSIONS
                       else DEFAULT_PROTOCOL)
            return self._result(mid, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "deskdora",
                               "version": __version__},
            })
        if method is None:
            return self._error(mid, -32600, "missing method")
        if mid is None:
            return None  # notification: nothing to answer
        if method == "ping":
            return self._result(mid, {})
        if method == "tools/list":
            tools = [{k: t[k] for k in ("name", "description",
                                        "inputSchema")}
                     for t in TOOLS]
            return self._result(mid, {"tools": tools})
        if method == "tools/call":
            params = message.get("params") or {}
            tool = _TOOL_MAP.get(params.get("name") or "")
            if tool is None:
                return self._error(
                    mid, -32602,
                    f"unknown tool: {params.get('name')!r}")
            try:
                result = tool["handler"](
                    self.client, params.get("arguments") or {})
            except ServiceError as exc:
                result = {"content": [{"type": "text",
                                       "text": str(exc)}],
                          "isError": True}
            except Exception as exc:  # a tool bug must not kill the server
                result = {"content": [{"type": "text",
                                       "text": f"tool error: {exc}"}],
                          "isError": True}
            return self._result(mid, result)
        return self._error(mid, -32601, f"method not found: {method}")

    @staticmethod
    def _result(mid, result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid, code, message):
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": code, "message": message}}


def main():
    server = McpServer()
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue  # a malformed line has no id to answer to
        response = server.handle(message)
        if response is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
