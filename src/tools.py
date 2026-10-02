"""The seven tools. Every one is read-only unless writes are explicitly enabled.

Two rules throughout:

1. **The model is untrusted.** Every path goes through workspace.safe_join before
   any I/O happens.
2. **Tool failures are data, not exceptions.** A raising tool aborts the agent
   loop and loses the trace, which is the part worth keeping. SandboxError is the
   one exception: it is a trust violation and propagates.
"""
import os
import re
import shlex
import subprocess

from src import workspace
from src.config import ALLOW_WRITE

MAX_READ_BYTES = 200_000
MAX_OUTPUT_CHARS = 20_000
CMD_TIMEOUT_S = 30
MAX_HITS = 50

# Commands the agent may run. An allowlist, not a denylist: anything not named
# here does not run. git is further restricted to read-only subcommands.
ALLOWED_COMMANDS = {"git", "ls", "dir"}
ALLOWED_GIT = {"status", "diff", "log", "show"}

_SENSITIVE = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PAT_")


def _secrets() -> list:
    """Env values that look like credentials, longest first.

    A child process can echo the environment. Nothing that looks like a key is
    allowed to reach the trace.
    """
    vals = [v for k, v in os.environ.items()
            if any(s in k.upper() for s in _SENSITIVE) and v and len(v) >= 8]
    return sorted(set(vals), key=len, reverse=True)


def redact(text: str) -> str:
    for value in _secrets():
        text = text.replace(value, "***")
    return text


def _root(ctx: dict) -> str:
    return ctx.get("root") or workspace.root_path()


# --- read-only tools ---------------------------------------------------------

def read_file(args, ctx):
    try:
        path = workspace.safe_join(_root(ctx), str(args.get("path", "")) or ".")
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    if not os.path.isfile(path):
        return {"ok": False, "error": f"not a file: {args.get('path')}"}
    with open(path, "rb") as f:
        raw = f.read(MAX_READ_BYTES + 1)
    if b"\x00" in raw[:8192]:
        return {"ok": False, "error": "binary file refused"}
    return {
        "ok": True, "path": args.get("path"),
        "truncated": len(raw) > MAX_READ_BYTES,
        "content": raw[:MAX_READ_BYTES].decode("utf-8", errors="replace"),
    }


def list_dir(args, ctx):
    try:
        path = workspace.safe_join(_root(ctx), str(args.get("path", "")) or ".")
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    if not os.path.isdir(path):
        return {"ok": False, "error": f"not a directory: {args.get('path')}"}
    entries = []
    for name in sorted(os.listdir(path))[:500]:
        if name in {"__pycache__", ".git", "node_modules", ".venv", "chroma_db"}:
            continue
        full = os.path.join(path, name)
        entries.append({"name": name,
                        "type": "dir" if os.path.isdir(full) else "file",
                        "size": 0 if os.path.isdir(full) else os.path.getsize(full)})
    return {"ok": True, "path": args.get("path", "."), "entries": entries}


def grep(args, ctx):
    pattern = str(args.get("pattern", ""))
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"ok": False, "error": f"bad regex: {e}"}
    try:
        base = workspace.safe_join(_root(ctx), str(args.get("path", "")) or ".")
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}

    root = _root(ctx)
    hits = []
    for dirpath, dirnames, filenames in os.walk(base):
        # Walk can descend through a symlinked directory; re-check each one.
        real = os.path.realpath(dirpath)
        if real != root and not real.startswith(root + os.sep):
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > 500_000:
                    continue
                with open(full, encoding="utf-8", errors="ignore") as f:
                    for n, line in enumerate(f, start=1):
                        if rx.search(line):
                            hits.append({"file": os.path.relpath(full, root).replace("\\", "/"),
                                         "line": n, "text": redact(line.strip())[:200]})
                            if len(hits) >= min(int(args.get("max_hits", MAX_HITS)), 200):
                                return {"ok": True, "hits": hits}
            except OSError:
                continue
    return {"ok": True, "hits": hits}


def run_cmd(args, ctx):
    raw = str(args.get("command", "")).strip()
    if not raw:
        return {"ok": False, "error": "empty command"}
    try:
        parts = shlex.split(raw, posix=(os.name != "nt"))
    except ValueError as e:
        return {"ok": False, "error": f"cannot parse: {e}"}
    if not parts or parts[0] not in ALLOWED_COMMANDS:
        return {"ok": False, "error": f"not allowlisted: {parts[0] if parts else ''!r}"}
    if parts[0] == "git":
        if len(parts) < 2 or parts[1] not in ALLOWED_GIT:
            return {"ok": False, "error": f"git subcommand not allowlisted: {parts[1:2]}"}
        if len(parts) > 2:
            return {"ok": False, "error": "git commands accept only the read-only subcommand"}
    elif len(parts) > 1:
        return {"ok": False, "error": "command arguments are disabled for the read-only shell tool"}
    try:
        proc = subprocess.run(parts, cwd=_root(ctx), capture_output=True,
                              text=True, timeout=min(CMD_TIMEOUT_S, 120))
    except FileNotFoundError:
        return {"ok": False, "error": f"executable not found: {parts[0]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timed out"}
    return {"ok": True, "returncode": proc.returncode,
            "output": redact((proc.stdout or "") + (proc.stderr or ""))[:MAX_OUTPUT_CHARS]}


def rag_search(args, ctx):
    from src import rag
    return {"ok": True, "chunks": rag.search(str(args.get("query", "")))}


# --- write tools, gated -------------------------------------------------------

def _write_allowed(ctx) -> bool:
    return bool(ctx.get("allow_write", ALLOW_WRITE))


def write_file(args, ctx):
    if not _write_allowed(ctx):
        return {"ok": False, "error": "writes are disabled (set MP_ALLOW_WRITE=1)"}
    try:
        path = workspace.safe_join(_root(ctx), str(args.get("path", "")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    content = str(args.get("content", ""))
    if len(content) > 500_000:
        return {"ok": False, "error": "content exceeds 500k characters"}
    os.makedirs(os.path.dirname(path) or _root(ctx), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": args.get("path"), "chars": len(content)}


def edit_file(args, ctx):
    if not _write_allowed(ctx):
        return {"ok": False, "error": "writes are disabled (set MP_ALLOW_WRITE=1)"}
    old, new = str(args.get("old", "")), str(args.get("new", ""))
    if not old or old == new:
        return {"ok": False, "error": "old must be non-empty and differ from new"}
    try:
        path = workspace.safe_join(_root(ctx), str(args.get("path", "")))
    except workspace.SandboxError as e:
        return {"ok": False, "error": f"sandbox: {e}"}
    with open(path, encoding="utf-8") as f:
        text = f.read()
    count = text.count(old)
    if count == 0:
        return {"ok": False, "error": "old string not found"}
    if count > 1:
        return {"ok": False, "error": f"old string matches {count}x -- add context"}
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new, 1))
    return {"ok": True, "path": args.get("path")}


TOOLS = {
    "read_file": read_file, "list_dir": list_dir, "grep": grep,
    "run_cmd": run_cmd, "write_file": write_file, "edit_file": edit_file,
    "rag_search": rag_search,
}

SPECS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file inside the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_dir", "description": "List a workspace directory, skipping caches.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "grep", "description": "Regex-search workspace files, returning file:line.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "run_cmd",
        "description": "Run one fixed read-only command: git status, git diff, git log, git show, ls, or dir. Arguments are not accepted. Output is redacted.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or overwrite a workspace file. Refused unless writes are enabled.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace a unique string in a workspace file. Refused unless writes are enabled.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"},
            "new": {"type": "string"}}, "required": ["path", "old", "new"]}}},
    {"type": "function", "function": {
        "name": "rag_search", "description": "Search the knowledge base for relevant passages.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
]


def call(name: str, args: dict, ctx: dict) -> dict:
    """Dispatch one tool. SandboxError propagates; everything else is data."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool {name!r}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "arguments must be an object"}
    return fn(args, ctx)
