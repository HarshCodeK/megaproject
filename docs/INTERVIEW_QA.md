# Interview Q&A — MegaProject (Sandboxed Tool-Calling Agent)

## The 30-second pitch

"MegaProject is a coding agent that reads files, greps code, and runs
read-only commands inside a sandboxed workspace, with a local ChromaDB
knowledge base. The important decisions are the sandbox boundary, the
round cap, and a circuit breaker that degrades to retrieval-only answers
instead of dying when the model is unreachable."

## Q: Walk me through the agent loop.

A: It is deliberately tiny: for round in range(MAX_ROUNDS), call the model
with the tool specs; if it returns no tool calls, that is the final answer;
otherwise run each tool, append `tool` messages, and loop. A framework would
hide the two things worth explaining — the sandbox and the round cap — so the
loop is hand-written.

## Q: What is the sandbox boundary exactly?

A: `workspace.safe_join(root, rel)` resolves the requested path with
`os.path.realpath` and rejects anything that does not start with the root.
Resolving before the check means a symlink pointing outside the root is
rejected rather than followed. Absolute paths, `~`, and NUL bytes are refused
outright. The stated limit: this is a *path* sandbox, not a privilege one — a
subprocess the agent spawns is not contained. Production would use a container.

## Q: The TOCTOU note in the README — explain it.

A: safe_join resolves the path, and later `open()` uses it. Between those two
calls, a symlink could be swapped in, so the check and the open are not atomic.
That is the classic time-of-check/time-of-use flaw. The real fix is opening
with `openat()` + `O_NOFOLLOW` on Unix, or a container that removes the
boundary entirely. For a local single-user agent the residual risk is
acceptable — and I state it rather than claiming the sandbox is "secure".

## Q: Why is command execution an allowlist?

A: A denylist always misses something. The agent may only run python, python3,
pytest, git (read-only subcommands), ls, dir. `git push` is refused because a
read-only agent should not mutate remotes. Output is truncated and redacted.

## Q: What does "redaction" cover?

A: Any environment variable whose name looks like a credential (KEY, TOKEN,
SECRET, PASSWORD) is masked in all tool output. A child process can echo its
environment, so without redaction an agent run could leak a secret into the
trace log. The trace is the thing you keep, so it must be safe to keep.

## Q: Three agent modes — what decides them?

A: `agent_readonly` is the default: online model, writes disabled. `agent`
enables writes when MP_ALLOW_WRITE=1. `agent_offline` means the circuit breaker
is open — the answer comes from ChromaDB retrieval with the trace intact. The
caller always sees which mode ran, because a degraded answer presented as a
model answer is a lie.

## Q: What is the circuit breaker and why does it record *why*?

A: When the provider call fails, the breaker opens and calls short-circuit to
`agent_offline`. Recording only "offline" once hid the real bug: a retired
model id returned 404, which *looked* exactly like the offline path working as
designed. The project appeared healthy while its online tier was dead. Now
`llm.status()` reports whether the last failure was a 404 (wrong/retired
model), a timeout, or a transport error — opposite problems, opposite fixes.

## Q: Why is the offline path the same code a real outage takes?

A: Because a degradation path that is only exercised by a test is not a
degradation path. With no GROQ_API_KEY, or a provider outage, the exact same
retrieval-only branch runs. Proving the fallback by *using* it beats simulating
it.

## Q: How does the knowledge base fit in?

A: `rag.py` builds a ChromaDB collection from markdown docs with the same
local sentence-transformer embeddings as the financial assistant
(all-MiniLM-L6-v2, 22M params, CPU). Retrieval uses filenames as metadata so
offline answers cite a source.

## Q: Tool failures are data, not exceptions — why?

A: An exception would abort the loop and lose the trace. The sandbox violation
(SandboxError) is the one exception: it is a trust violation, not a normal
failure, so it propagates. Everything the model might try that fails — bad
regex, missing file, non-allowlisted command — is returned as a dict the model
can read and adapt to.

## Q: What is capped and why?

A: MAX_ROUNDS on the loop, 200KB per file read, 20KB of command output, 30s
command timeout, 50 grep hits. Each cap exists so a single tool call cannot
flood the context or hang the service. Those numbers are product choices, not
measurements — they are stated plainly.

## Q: What is not in this project?

A: No streaming, no auth on the API, no persistence of sessions, one shared
workspace per server. It is a backend + Streamlit UI demo, not a multi-tenant
service. Multi-tenancy would need per-user sandboxes and a real isolation
boundary.

## Q: How is this tested?

A: 38 tests, all offline. Sandbox path escapes (including Windows-style
absolute paths on Linux and vice versa — a real CI bug), symlink rejection,
allowlist enforcement, git subcommand restriction, redaction, write gating,
agent loop modes, and the circuit breaker's failure classification. The bug
about the retired model and the breaker hiding it now has a regression test.
