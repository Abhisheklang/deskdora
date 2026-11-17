"""MCP server (v3.1): protocol handling + tool plumbing.

The server is a thin client of the DeskDora HTTP API, so these tests
use a fake service client for the tool calls, and a real subprocess
pair (uvicorn service + stdio MCP server) for the wire protocol.
The full desktop run + confirmation flow is verified live in the
reference desktop session (see REPORT-V3.1.md).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from deskdora.mcp_server import McpServer, ServiceError, TOOLS


class FakeClient:
    def __init__(self):
        self.calls = []
        self.pending = {"id": "abc123", "step": {"action": "click"},
                        "description": "Submit the form"}

    def call(self, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        if path == "/api/status":
            return {"state": {"running": True, "goal": "Fill the form",
                              "planner_mode": "builtin", "result": None},
                    "kill_tripped": False,
                    "pending_confirmation": self.pending}
        if path == "/api/runs":
            return [{"id": "r1", "goal": "Open terminal",
                     "status": "success", "planner_mode": "builtin",
                     "events": []}]
        if path == "/api/runs/r1":
            return {"id": "r1", "events": [{"kind": "run_start"}]}
        return {"ok": True}


def rpc(server, method, params=None, mid=1):
    msg = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        msg["params"] = params
    return server.handle(msg)


def test_initialize_negotiates_version():
    server = McpServer(FakeClient())
    res = rpc(server, "initialize",
              {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "test", "version": "0"}})
    assert res["result"]["protocolVersion"] == "2025-03-26"
    assert res["result"]["serverInfo"]["name"] == "deskdora"
    res = rpc(server, "initialize", {"protocolVersion": "1999-01-01"})
    assert res["result"]["protocolVersion"] == "2025-03-26"


def test_notifications_and_ping():
    server = McpServer(FakeClient())
    assert server.handle({"jsonrpc": "2.0",
                          "method": "notifications/initialized"}) is None
    assert rpc(server, "ping")["result"] == {}
    res = rpc(server, "no/such/method")
    assert res["error"]["code"] == -32601


def test_tools_list():
    server = McpServer(FakeClient())
    tools = rpc(server, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"run_task", "get_status", "list_runs",
                     "get_run_log", "approve_confirmation",
                     "deny_confirmation", "stop"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]


def test_tool_calls_go_through_the_service_api():
    client = FakeClient()
    server = McpServer(client)
    res = rpc(server, "tools/call",
              {"name": "run_task", "arguments": {"goal": "Open terminal"}})
    assert "Run started" in res["result"]["content"][0]["text"]
    assert ("POST", "/api/run", {"goal": "Open terminal"}) in client.calls

    res = rpc(server, "tools/call", {"name": "get_status"})
    payload = json.loads(res["result"]["content"][0]["text"])
    assert payload["pending_confirmation"]["id"] == "abc123"

    # Approving with no id resolves the pending one from the service.
    res = rpc(server, "tools/call", {"name": "approve_confirmation"})
    assert ("POST", "/api/confirmations/abc123",
            {"approved": True}) in client.calls
    res = rpc(server, "tools/call",
              {"name": "deny_confirmation",
               "arguments": {"confirmation_id": "zzz"}})
    assert ("POST", "/api/confirmations/zzz",
            {"approved": False}) in client.calls

    res = rpc(server, "tools/call", {"name": "stop"})
    assert ("POST", "/api/kill", None) in client.calls

    res = rpc(server, "tools/call", {"name": "list_runs"})
    runs = json.loads(res["result"]["content"][0]["text"])
    assert runs[0]["id"] == "r1" and "events" not in runs[0]

    res = rpc(server, "tools/call",
              {"name": "get_run_log", "arguments": {"run_id": "r1"}})
    log = json.loads(res["result"]["content"][0]["text"])
    assert log["events_total"] == 1


def test_tool_errors_are_iserror_results():
    class DownClient:
        def call(self, *a, **k):
            raise ServiceError("cannot reach the DeskDora service")

    server = McpServer(DownClient())
    res = rpc(server, "tools/call", {"name": "get_status"})
    assert res["result"]["isError"] is True
    assert "cannot reach" in res["result"]["content"][0]["text"]
    res = rpc(server, "tools/call", {"name": "rm_rf"})
    assert res["error"]["code"] == -32602
    res = rpc(server, "tools/call",
              {"name": "run_task", "arguments": {}})
    assert res["result"]["isError"] is True


def test_tool_definitions_match_handlers():
    for tool in TOOLS:
        assert callable(tool["handler"])


# ---------------------------------------------------------------------------
# Wire level: a real uvicorn service + the MCP server as a subprocess
# ---------------------------------------------------------------------------

def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.skipif(os.environ.get("DESKDORA_NO_SUBPROCESS_TESTS"),
                    reason="subprocess tests disabled")
def test_stdio_server_against_real_service(tmp_path):
    port = _free_port()
    root = os.path.join(os.path.dirname(__file__), "..")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(root, "src")
    env["DESKDORA_DATA_DIR"] = str(tmp_path)
    service = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "deskdora.server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "/api/status", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            pytest.fail("service did not come up")

        env["DESKDORA_URL"] = url
        proc = subprocess.Popen(
            [sys.executable, "-m", "deskdora.mcp_server"], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True)

        def ask(msg):
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        res = ask({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-03-26",
                              "capabilities": {},
                              "clientInfo": {"name": "pytest",
                                             "version": "0"}}})
        assert res["result"]["serverInfo"]["name"] == "deskdora"
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ) + "\n")
        proc.stdin.flush()
        res = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert len(res["result"]["tools"]) == 7
        res = ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "get_status", "arguments": {}}})
        status = json.loads(res["result"]["content"][0]["text"])
        assert status["running"] is False
        res = ask({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "stop", "arguments": {}}})
        assert "Kill switch" in res["result"]["content"][0]["text"]
        proc.stdin.close()
        proc.wait(timeout=10)
    finally:
        service.terminate()
        service.wait(timeout=10)
