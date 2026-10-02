"""A bounded tool-calling agent over a sandboxed workspace.

The loop, in full:

    for round in range(MAX_ROUNDS):
        message = llm.complete(system, history, tools=SPECS)
        if no tool_calls: return message.content      # done
        run each tool, append results as "tool" messages

That is the whole design, and keeping it this short is the point: a framework
would obscure the two things worth explaining, which are the sandbox and the
round cap.

Three modes, returned in `mode` so the caller can see which one ran:
  agent            -- online, tools available
  agent_readonly   -- online, writes disabled
  agent_offline    -- breaker open; retrieval-only answer, trace intact
"""
import json
import time

from src import llm, rag, workspace
from src.config import ALLOW_WRITE, MAX_ROUNDS
from src.tools import SPECS, call, redact

SYSTEM = """You are a coding assistant working inside a sandboxed workspace.

You have tools for reading files, searching, and running read-only commands.
Use them to understand the code before describing it. Never claim what you have
not read or run. If a tool returns an error, read it and adapt rather than
retrying blindly. When you have the answer, reply in prose with no tool calls."""

OFFLINE_SYSTEM = (
    "You are offline. Answer only from the knowledge base passages provided, "
    "cite the source file, and say plainly when they do not cover the question."
)


def _stitch(chunks, question: str) -> str:
    """Retrieval-only answer. Used when the model is unreachable."""
    if not chunks:
        return ("I cannot reach the model right now, and the knowledge base has "
                "nothing relevant to that question.")
    out = [f"Offline — answering from the knowledge base ({len(chunks)} passage(s)):\n"]
    for c in chunks:
        out.append(f"From [{c['source']}]:\n{c['text']}\n")
    return "\n".join(out)


def run(task: str, model_id: str = None, max_rounds: int = MAX_ROUNDS,
        allow_write: bool = None) -> dict:
    """Run one agent turn. Never raises; returns mode and a full trace."""
    started = time.time()
    root = workspace.root_path()
    ctx = {"root": root, "allow_write": ALLOW_WRITE if allow_write is None else allow_write}
    mode = "agent" if ctx["allow_write"] else "agent_readonly"

    try:
        rag.ensure_index()
    except Exception:
        pass    # retrieval is optional; the tools still work without it

    trace: list = []
    messages: list = [
        {"role": "system",
         "content": SYSTEM + ("" if ctx["allow_write"] else
                              "\n\nWrites are DISABLED. Read, search and run commands only.")},
        {"role": "user", "content": f"Workspace root: {root}\n\nTask: {task}"},
    ]

    answer = ""
    rounds = 0
    used: set = set()      # declared here: the model may answer on round 1

    for round_num in range(1, max_rounds + 1):
        t0 = time.time()
        try:
            message = llm.complete(messages, tools=SPECS, model_id=model_id)
        except llm.OfflineError as e:
            chunks = rag.search(task)
            mode = "agent_offline"
            trace.append({"round": round_num, "phase": "offline", "detail": str(e)})
            return {"answer": _stitch(chunks, task), "mode": mode, "rounds": rounds,
                    "trace": trace, "tools_used": [], "sources": [c["source"] for c in chunks],
                    "total_ms": round((time.time() - started) * 1000)}

        rounds = round_num
        calls = message.tool_calls or []

        if not calls:
            answer = message.content or ""
            trace.append({"round": round_num, "phase": "final",
                          "latency_ms": round((time.time() - t0) * 1000)})
            break

        messages.append({"role": "assistant", "content": message.content or "",
                         "tool_calls": calls})
        steps = []
        for tc in calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            used.add(name)
            result = call(name, args, ctx)
            steps.append({"tool": name, "args": args,
                          "result": redact(json.dumps(result, default=str))[:500]})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)[:8000]})
        trace.append({"round": round_num, "phase": "act", "tool_calls": steps,
                      "latency_ms": round((time.time() - t0) * 1000)})
    else:
        answer = ("Reached the maximum number of rounds without a final answer. "
                  "The trace shows what was tried.")
        mode = "agent_max_rounds"

    return {"answer": answer, "mode": mode, "rounds": rounds, "trace": trace,
            "tools_used": sorted(used), "sources": [],
            "total_ms": round((time.time() - started) * 1000)}
