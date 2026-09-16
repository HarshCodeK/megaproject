"""MCP server for megaproject — exposes 7 tools over stdio JSON-RPC 2.0.

Any AI coding assistant (opencode, Hermes, Claude Code) can connect to this
server and call megaproject's operator tools: read_file, list_dir, grep,
run_cmd, write_file, edit_file, rag_search.

Protocol: JSON-RPC 2.0 over newline-delimited stdin/stdout.
Methods:   initialize, tools/list, tools/call.
Transport: stdio (for local integration) or HTTP (for remote).

Security:
  - Writes are gated by MP_ALLOW_WRITE env var (default: off).
  - Workspace sandbox enforced by workspace.safe_join.
  - Secrets redacted from all output by op_tools.redact.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import threading
from pathlib import Path

from src import op_tools, workspace

# ---------------------------------------------------------------------------
# JSON-RPC plumbing (same pattern as recoup's mcp_server.py)
# ---------------------------------------------------------------------------


def _read_message() -> dict | None:
    """Read one JSON-RPC message from stdin (newline-delimited)."""
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _send_message(msg: dict):
    """Write one JSON-RPC message to stdout."""
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ---------------------------------------------------------------------------
# Context — workspace root + write permission
# ---------------------------------------------------------------------------

_ctx: dict = {}


def _init_ctx():
    global _ctx
    if _ctx:
        return
    root = workspace.resolve_root()
    _ctx = {
        "root": root,
        "allow_write": os.environ.get("MP_ALLOW_WRITE", "0") == "1",
        "conv_id": None,  # MCP calls are stateless per-request
    }


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_initialize(req: dict) -> dict:
    _init_ctx()
    return _ok(req["id"], {
        "protocolVersion": "2024-11-05",
        "serverInfo": {
            "name": "megaproject",
            "version": "0.2.0",
        },
        "capabilities": {
            "tools": {
                "listChanged": False,
            },
            "logging": {},
        },
    })


def _handle_tools_list(req: dict) -> dict:
    _init_ctx()
    return _ok(req["id"], {
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t.get("parameters", {"type": "object"}),
            }
            for t in op_tools.TOOLS
        ],
    })


def _handle_tools_call(req: dict) -> dict:
    _init_ctx()
    params = req.get("params", {})
    name = params.get("name", "")
    args = params.get("arguments", {})

    if not name:
        return _err(req["id"], -32602, "missing tool name")

    # Run the tool — sandbox errors propagate as trust violations
    try:
        result = op_tools.call_tool(name, args, _ctx)
    except workspace.SandboxError as e:
        result = {"ok": False, "error": f"TRUST VIOLATION: {e}"}
    except Exception as e:
        result = {"ok": False, "error": f"tool crashed: {e.__class__.__name__}: {e}"}

    # MCP expects content blocks, not raw dicts
    content = json.dumps(result, indent=2)
    return _ok(req["id"], {
        "content": [{"type": "text", "text": content}],
        "isError": not result.get("ok", False),
    })


# ---------------------------------------------------------------------------
# Request router
# ---------------------------------------------------------------------------

_HANDLERS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    # Required by MCP but we don't need anything — return empty capabilities
    "notifications/initialized": lambda req: None,
    "ping": lambda req: _ok(req.get("id"), {}),
}


def _dispatch(req: dict):
    method = req.get("method", "")
    handler = _HANDLERS.get(method)
    if handler is None:
        return _err(req.get("id"), -32601, f"method not found: {method}")
    result = handler(req)
    return result  # None for notifications (no response sent)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run_mcp_stdio():
    """Run the MCP server on stdio. Blocks until EOF."""
    _init_ctx()
    while True:
        msg = _read_message()
        if msg is None:
            break  # EOF
        resp = _dispatch(msg)
        if resp is not None:
            _send_message(resp)


def run_mcp_http(host: str = "127.0.0.1", port: int = 8765):
    """Run the MCP server as an HTTP endpoint (for remote access)."""
    import http.server
    import threading

    _init_ctx()

    class MCPHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "invalid JSON"}')
                return

            resp = _dispatch(req)
            if resp is None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"")
            else:
                payload = json.dumps(resp).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                body = json.dumps({
                    "status": "ok",
                    "server": "megaproject",
                    "version": "0.2.0",
                    "tools": [t["name"] for t in op_tools.TOOLS],
                }).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # silence request logging

    server = http.server.HTTPServer((host, port), MCPHandler)
    print(f"MCP HTTP server listening on {host}:{port}")
    server.serve_forever()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
        run_mcp_http(port=port)
    else:
        run_mcp_stdio()
