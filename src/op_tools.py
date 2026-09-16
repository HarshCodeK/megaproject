"""Operator tools — real I/O behind the workspace sandbox.

Every tool is a pure-ish function: (args: dict, ctx: dict) -> dict.
Results are JSON-serializable and size-capped so they fit LLM context.
Secret values are redacted from anything a child process prints.

ctx keys:
  root         absolute workspace root (from workspace.resolve_root)
  conv_id      conversation id for tool-call logging (may be None)
  allow_write  bool — write_file/edit_file refuse unless True

Tool-call failures are DATA ({ok: False, error}), never exceptions —
except SandboxError, which is a trust violation and propagates.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

from src import config, rag, store, workspace

# ---------------------------------------------------------------------------
# Secret redaction — child output may echo env; never let keys reach the trace
# ---------------------------------------------------------------------------

_SENSITIVE_NAMES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PAT_")


def _secret_values() -> list[str]:
    vals = []
    for k, v in os.environ.items():
        if any(s in k.upper() for s in _SENSITIVE_NAMES) and v and len(v) >= 8:
            vals.append(v)
    # longest first so overlapping values redact cleanly
    return sorted(set(vals), key=len, reverse=True)


def redact(text: str) -> str:
    for v in _secret_values():
        text = text.replace(v, "***")
    return text


def _log(ctx: dict, tool: str, args: dict, result_summary: str, latency_ms: float):
    if ctx.get("conv_id"):
        try:
            store.log_tool_call(ctx["conv_id"], tool, args, result_summary[:500], latency_ms)
        except Exception:
            pass  # logging must never break tool execution


# ---------------------------------------------------------------------------
# read-only tools
# ---------------------------------------------------------------------------

def tool_read_file(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    try:
        path = workspace.safe_join(ctx["root"], str(args.get("path", "")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    if not os.path.isfile(path):
        return {"ok": False, "error": f"not a file: {args.get('path')}"}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            raw = f.read(config.MAX_READ_BYTES + 1)
    except OSError as e:
        return {"ok": False, "error": f"read failed: {e}"}
    if b"\x00" in raw[:8192]:
        return {"ok": False, "error": "binary file refused"}
    truncated = len(raw) > config.MAX_READ_BYTES
    text = raw[: config.MAX_READ_BYTES].decode("utf-8", errors="replace")
    lines = text.count("\n") + 1
    _log(ctx, "read_file", args, f"{lines} lines", (time.time() - t0) * 1000)
    return {"ok": True, "path": args.get("path"), "lines": lines,
            "truncated": truncated, "content": text}


def tool_list_dir(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    try:
        path = workspace.safe_join(ctx["root"], str(args.get("path", ".")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    if not os.path.isdir(path):
        return {"ok": False, "error": f"not a directory: {args.get('path')}"}
    try:
        names = sorted(os.listdir(path))[:500]
    except OSError as e:
        return {"ok": False, "error": f"list failed: {e}"}
    entries = []
    for n in names:
        if n in ("__pycache__", ".git", "node_modules", ".venv"):
            continue
        full = os.path.join(path, n)
        entries.append({"name": n,
                        "type": "dir" if os.path.isdir(full) else "file",
                        "size": 0 if os.path.isdir(full) else os.path.getsize(full)})
    _log(ctx, "list_dir", args, f"{len(entries)} entries", (time.time() - t0) * 1000)
    return {"ok": True, "path": args.get("path", "."), "entries": entries}


def tool_grep(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    pattern = str(args.get("pattern", ""))
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad regex: {e}"}
    try:
        base = workspace.safe_join(ctx["root"], str(args.get("path", ".")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    include = str(args.get("include", "*.py"))
    suffix = include.lstrip("*")  # "*.py" -> ".py", "*" -> ""
    max_hits = min(int(args.get("max_hits", 50)), 200)
    hits = []
    for dp, _dn, fn in os.walk(base):
        # stay inside the sandbox even with odd symlinked dirs
        if os.path.realpath(dp) != ctx["root"] and not os.path.realpath(dp).startswith(ctx["root"] + os.sep):
            continue
        for f in sorted(fn):
            if suffix and not f.endswith(suffix):
                continue
            if f.startswith("."):
                continue
            fp = os.path.join(dp, f)
            try:
                if os.path.getsize(fp) > 500_000:
                    continue
                with open(fp, encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, start=1):
                        if rx.search(line):
                            rel = os.path.relpath(fp, ctx["root"]).replace("\\", "/")
                            hits.append({"file": rel, "line": i,
                                         "snippet": redact(line.strip())[:200]})
                            if len(hits) >= max_hits:
                                break
            except OSError:
                continue
            if len(hits) >= max_hits:
                break
        if len(hits) >= max_hits:
            break
    _log(ctx, "grep", args, f"{len(hits)} hits", (time.time() - t0) * 1000)
    return {"ok": True, "pattern": pattern, "hits": hits}


_ALLOWED_COMMANDS = {"python", "python3", "pytest", "git", "ls"}
_ALLOWED_GIT = {"status", "diff", "log", "show", "branch"}


def tool_run_cmd(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    raw = str(args.get("command", "")).strip()
    if not raw:
        return {"ok": False, "error": "empty command"}
    try:
        parts = shlex.split(raw, posix=os.name != "nt")
    except ValueError as e:
        return {"ok": False, "error": f"cannot parse command: {e}"}
    if not parts or parts[0] not in _ALLOWED_COMMANDS:
        return {"ok": False, "error": f"command not allowlisted: {parts[0] if parts else ''!r} "
                                      f"(allowed: {sorted(_ALLOWED_COMMANDS)})"}
    if parts[0] == "git" and (len(parts) < 2 or parts[1] not in _ALLOWED_GIT):
        return {"ok": False, "error": f"git subcommand not allowlisted (allowed: {sorted(_ALLOWED_GIT)})"}
    timeout = min(float(args.get("timeout_s", config.CMD_TIMEOUT_S)), 120)
    try:
        proc = subprocess.run(parts, cwd=ctx["root"], capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "error": f"executable not found: {parts[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {timeout}s"}
    out = redact((proc.stdout or "") + (proc.stderr or ""))[: config.MAX_TOOL_OUTPUT_CHARS]
    _log(ctx, "run_cmd", args, f"rc={proc.returncode}", (time.time() - t0) * 1000)
    return {"ok": True, "returncode": proc.returncode, "output": out}


# ---------------------------------------------------------------------------
# gated write tools
# ---------------------------------------------------------------------------

def _need_write(ctx: dict):
    if not ctx.get("allow_write"):
        return {"ok": False, "error": "writes disabled — restart with MP_ALLOW_WRITE=1 to enable"}
    return None


def tool_write_file(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    denied = _need_write(ctx)
    if denied:
        return denied
    content = str(args.get("content", ""))
    if len(content) > 500_000:
        return {"ok": False, "error": "content exceeds 500k chars"}
    try:
        path = workspace.safe_join(ctx["root"], str(args.get("path", "")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    try:
        os.makedirs(os.path.dirname(path) or ctx["root"], exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}"}
    _log(ctx, "write_file", args, f"{len(content)} chars", (time.time() - t0) * 1000)
    return {"ok": True, "path": args.get("path"), "chars": len(content)}


def tool_edit_file(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    denied = _need_write(ctx)
    if denied:
        return denied
    old, new = str(args.get("old", "")), str(args.get("new", ""))
    if not old or old == new:
        return {"ok": False, "error": "old/new must differ and old must be non-empty"}
    try:
        path = workspace.safe_join(ctx["root"], str(args.get("path", "")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return {"ok": False, "error": f"read failed: {e}"}
    count = text.count(old)
    if count == 0:
        return {"ok": False, "error": "oldString not found"}
    if count > 1:
        return {"ok": False, "error": f"oldString matches {count}x — not unique, add context"}
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    _log(ctx, "edit_file", args, "1 replacement", (time.time() - t0) * 1000)
    return {"ok": True, "path": args.get("path")}


def tool_rag_search(args: dict, ctx: dict) -> dict:
    t0 = time.time()
    chunks = rag.retrieve(str(args.get("query", "")), conv_id=ctx.get("conv_id"))
    _log(ctx, "rag_search", args, f"{len(chunks)} chunks", (time.time() - t0) * 1000)
    return {"ok": True, "chunks": chunks}


# ---------------------------------------------------------------------------
# Registry — the single source of truth for LLM specs, dispatcher, and MCP
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "read_file",
     "description": "Read a workspace-relative file. Refuses binaries and sandbox escapes. Args: {path, max_bytes?}",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]}},
    {"name": "list_dir",
     "description": "List a workspace-relative directory (skips caches). Args: {path?}",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "grep",
     "description": "Regex-search files under a directory. Returns file:line + snippet. Args: {pattern, path?, include?, max_hits?}",
     "parameters": {"type": "object",
                    "properties": {"pattern": {"type": "string"},
                                   "path": {"type": "string"},
                                   "include": {"type": "string"}},
                    "required": ["pattern"]}},
    {"name": "run_cmd",
     "description": "Run an allowlisted command (python/pytest/git status|diff|log|show|branch/ls) in the workspace. Secrets redacted. Args: {command, timeout_s?}",
     "parameters": {"type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]}},
    {"name": "write_file",
     "description": "Create/overwrite a workspace file. REFUSED unless writes enabled. Args: {path, content}",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "edit_file",
     "description": "Exact unique-string replacement. REFUSED unless writes enabled. Args: {path, old, new}",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "old": {"type": "string"},
                                   "new": {"type": "string"}},
                    "required": ["path", "old", "new"]}},
    {"name": "rag_search",
     "description": "Search the knowledge base for relevant chunks. Args: {query}",
     "parameters": {"type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]}},
]

_DISPATCH = {
    "read_file": tool_read_file,
    "list_dir": tool_list_dir,
    "grep": tool_grep,
    "run_cmd": tool_run_cmd,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "rag_search": tool_rag_search,
}


def call_tool(name: str, args: dict, ctx: dict) -> dict:
    fn = _DISPATCH.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {name!r}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object"}
    return fn(args, ctx)
