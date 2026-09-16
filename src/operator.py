"""Operator loop — real tool-calling agent for repo work.

LLM decides which tools to call (read_file, grep, run_cmd, write_file, edit_file,
rag_search). Each iteration: think → call → observe → reflect. The loop writes a
full trace so the user can see the model's reasoning at every step.

Design invariants:
  1. The model is untrusted. The sandbox (workspace.py) enforces boundary.
  2. Writes are OFF by default. Even when enabled, the API layer applies a
     second gate (MP_ALLOW_OPERATOR_WRITE_VIA_API). Two locks, one key.
  3. Secrets are redacted from all tool output (op_tools.redact).
  4. Circuit breaker: if LLM goes offline mid-loop, we don't crash — we
     return whatever progress the trace holds.
  5. No framework. This is ~120 lines doing what LangChain does in 10k.
"""
from __future__ import annotations

import json
import time

from src import op_tools, rag, store, workspace
from src.llm import llm_complete, OfflineError, llm_status
from src.config import MAX_OPERATOR_ROUNDS, ALLOW_WRITE, MAX_TOOL_OUTPUT_CHARS

# ---------------------------------------------------------------------------
# System prompt — the operator knows what it can do and what it can't
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are Megaproject's operator — a real tool-calling coding assistant.

You have filesystem and command tools. Use them to understand and modify the
workspace. Think step by step. Every tool call must have a clear purpose.

Available tools:
- read_file(path): read a file's contents
- list_dir(path?): list a directory
- grep(pattern, path?, include?, max_hits?): regex search files
- run_cmd(command): run allowlisted commands (python, pytest, git, ls)
- write_file(path, content): create/overwrite a file (only when writes enabled)
- edit_file(path, old, new): exact unique-string replacement (only when writes enabled)
- rag_search(query): search the knowledge base

Rules:
1. Read before you write. Understand the codebase first.
2. Use grep to find relevant code, then read_file to get full context.
3. When editing, be precise — one targeted edit beats five shotgun changes.
4. Run tests after changes: run_cmd("pytest tests/ -q").
5. If a tool returns an error, read the error and adapt. Don't retry blindly.
6. When done, provide a final summary of what you did.

{write_instruction}
{workspace_info}
"""

_WRITE_ENABLED = "Writes are ENABLED. You may create and edit files."
_WRITE_DISABLED = "Writes are DISABLED (read-only mode). You can read, search, and run commands but NOT modify files. Tell the user what you would change."


def _build_system(ctx: dict) -> str:
    write_inst = _WRITE_ENABLED if ctx.get("allow_write") else _WRITE_DISABLED
    return _SYSTEM.format(
        write_instruction=write_inst,
        workspace_info=f"Workspace root: {ctx['root']}",
    )


# ---------------------------------------------------------------------------
# Tool call parsing — Groq returns tool_calls in the OpenAI format
# ---------------------------------------------------------------------------


def _parse_tool_calls(message) -> list[dict]:
    """Extract tool calls from an OpenAI-style message object."""
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return []
    calls = []
    for tc in raw:
        fn = getattr(tc, "function", None)
        if fn is None:
            continue
        name = getattr(fn, "name", "")
        args_raw = getattr(fn, "arguments", "{}")
        try:
            args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except json.JSONDecodeError:
            args = {}
        calls.append({"id": getattr(tc, "id", ""), "name": name, "arguments": args})
    return calls


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

# Tool spec sent to the LLM — mirrors op_tools.TOOLS but without descriptions
# the model doesn't need (keeping context lean).
_TOOL_SPECS = [
    {"type": "function", "function": t} for t in op_tools.TOOLS
]


def run_operator(task: str, conv_id: str = None, ctx: dict = None,
                 max_rounds: int = None, allow_write: bool = None) -> dict:
    """Run a multi-round tool-calling operator session.

    Returns:
        {
          "answer": str,          — final summary
          "trace": list[dict],    — per-step reasoning + tool calls + outputs
          "rounds": int,          — how many LLM calls were made
          "mode": "operator"|"operator_offline"|"operator_readonly",
          "sources": list[str],   — files touched
        }
    """
    t_start = time.time()
    max_rounds = max_rounds or MAX_OPERATOR_ROUNDS

    if ctx is None:
        ctx = {}
    root = workspace.resolve_root()
    ctx.setdefault("root", root)
    ctx.setdefault("conv_id", conv_id)

    # Write permission: explicit arg > config default
    if allow_write is not None:
        ctx["allow_write"] = allow_write
    else:
        ctx["allow_write"] = ALLOW_WRITE

    mode = "operator"
    if not ctx["allow_write"]:
        mode = "operator_readonly"

    system_msg = _build_system(ctx)
    messages = [{"role": "system", "content": system_msg},
                {"role": "user", "content": task}]

    trace: list[dict] = []
    sources: list[str] = []
    tool_names_used: set[str] = set()
    answer = ""

    for round_num in range(1, max_rounds + 1):
        step_start = time.time()

        # --- think: call LLM with tools ---
        try:
            raw = _call_llm_with_tools(messages, conv_id, round_num)
        except OfflineError:
            mode = "operator_offline"
            trace.append({"round": round_num, "phase": "offline",
                          "detail": "LLM circuit breaker opened — returning partial trace"})
            break

        # --- extract assistant message for trace ---
        assistant_msg = raw.choices[0].message
        content = getattr(assistant_msg, "content", "") or ""
        tool_calls = _parse_tool_calls(assistant_msg)

        # Record thinking if present
        thinking = content if content else None

        # --- no tool calls → done ---
        if not tool_calls:
            answer = content
            trace.append({"round": round_num, "phase": "final",
                          "thinking": thinking, "detail": "no tool calls — task complete"})
            break

        # --- act: execute each tool call ---
        tool_results = []
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            tool_names_used.add(name)

            # Sandbox check
            try:
                result = op_tools.call_tool(name, args, ctx)
            except workspace.SandboxError as e:
                result = {"ok": False, "error": f"TRUST VIOLATION: {e}"}

            # Track sources
            if name == "read_file" and result.get("ok"):
                sources.append(args.get("path", ""))
            elif name == "grep" and result.get("ok"):
                for h in result.get("hits", []):
                    if h.get("file") not in sources:
                        sources.append(h["file"])

            # Truncate tool output for context
            result_str = json.dumps(result)[:MAX_TOOL_OUTPUT_CHARS]
            tool_results.append({"id": tc["id"], "name": name, "args": args,
                                 "result_summary": result_str[:500]})

            # Append tool result to messages for the next LLM call
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_str,
            })

        trace.append({
            "round": round_num,
            "phase": "act",
            "thinking": thinking,
            "tool_calls": tool_results,
            "latency_ms": round((time.time() - step_start) * 1000),
        })

    else:
        # Exhausted rounds without a "final" step
        answer = content if content else "Reached maximum rounds without completing."
        trace.append({"round": max_rounds, "phase": "max_rounds",
                      "detail": "loop exhausted"})

    # Deduplicate sources
    seen = set()
    unique_sources = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique_sources.append(s)

    total_ms = round((time.time() - t_start) * 1000)
    return {
        "answer": answer,
        "trace": trace,
        "rounds": len([t for t in trace if t.get("phase") != "offline"]),
        "mode": mode,
        "sources": unique_sources,
        "total_ms": total_ms,
        "tools_used": sorted(tool_names_used),
    }


# ---------------------------------------------------------------------------
# LLM call with tool support — wraps llm_complete to return the raw response
# ---------------------------------------------------------------------------


def _call_llm_with_tools(messages: list, conv_id: str, round_num: int):
    """Call LLM with function calling enabled. Returns raw ChatCompletion."""
    from groq import Groq
    import os
    from src.config import LLM_MODEL, LLM_TIMEOUT_S

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise OfflineError("GROQ_API_KEY not configured")

    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        tools=_TOOL_SPECS,
        tool_choice="auto",
        temperature=0.2,
        timeout=LLM_TIMEOUT_S,
    )

    # Log the call
    usage = getattr(resp, "usage", None)
    pt = getattr(usage, "prompt_tokens", 0) if usage else 0
    ct = getattr(usage, "completion_tokens", 0) if usage else 0
    store.log_llm_call(conv_id, f"operator_round_{round_num}", LLM_MODEL,
                       "ok", 0.0, pt, ct)

    return resp
