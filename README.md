# MegaProject — Sandboxed Tool-Calling Agent

An AI agent that reads files, searches code and runs read-only commands inside a
sandboxed workspace, backed by a local ChromaDB knowledge base.

**Python · FastAPI · ChromaDB · sentence-transformers · AI Agents · MCP-style tools**

---

## The agent loop, in full

```python
for round in range(MAX_ROUNDS):
    message = llm.complete(system, history, tools=TOOL_SPECS)
    if not message.tool_calls:
        return message.content          # done
    for call in message.tool_calls:
        result = dispatch(call)        # sandboxed, redacted
        history.append(result)
```

That is the whole design. Keeping it this short is deliberate: a framework would
hide the two things worth explaining, which are the sandbox and the round cap.

Three modes, returned in `mode` so the caller can see which ran:

| Mode | When |
|---|---|
| `agent` | online, writes enabled |
| `agent_readonly` | online, writes off (the default) |
| `agent_offline` | breaker open — answers from retrieval, trace intact |
| `agent_max_rounds` | hit the cap without a final answer |

---

## The trust boundary

**The model is untrusted. The sandbox is trusted.**

```python
resolved = os.path.realpath(os.path.join(root, rel))
if resolved != root and not resolved.startswith(root + os.sep):
    raise SandboxError(...)
```

`realpath` runs *before* the check, so a symlink pointing outside the root is
rejected rather than followed. Absolute paths and `~` are refused outright.

**State the limit yourself:** this is a *path* sandbox, not a *privilege* one.
It stops the agent reading outside the workspace. It does not stop a subprocess
spawned by `run_cmd` from doing so — that needs a container, which is what a
production deployment would use. Saying this before you are asked is the
difference between a security control and a security claim.

Command execution is an allowlist of fixed read-only invocations: `git status`, `git diff`, `git log`, `git show`, `ls`, and `dir`. Command arguments are disabled, so the shell tool cannot be turned into a general-purpose process launcher. Python execution, pytest execution, branch mutation, and `git push` are refused.

Env values whose names look like credentials are redacted from all tool output,
so a child process echoing the environment cannot leak a key into the trace.

---

## The bug worth telling

The previous version of this project pinned a model the provider had retired.
Every call returned 404. The circuit breaker caught that and reported
`{"online": false}` — **which looks exactly like the offline-degradation path
working as designed.** The project appeared healthy while its entire online tier
was dead.

The fix is not just the model id. `llm.status()` now reports *why* the last
failure happened: a 404 (wrong or retired model) and a timeout both open the
breaker and need opposite fixes, and reporting only "offline" hides which
happened.

---

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env            # optional: add GROQ_API_KEY
uvicorn src.api:app --reload    # docs at /docs
streamlit run app.py
```

Without a key it still runs — `agent_offline` answers from the knowledge base
alone. That is the degradation path, and it is the same code path a real outage
takes.

`GET /health` reports key state, breaker state, chunk count and the workspace
root.

---

## Layout

| File | What it does |
|---|---|
| `src/workspace.py` | The sandbox. `safe_join` is the whole trust boundary |
| `src/tools.py` | Seven tools, fixed read-only command allowlist, secret redaction |
| `src/agent.py` | The loop, the round cap, the three degradation modes |
| `src/llm.py` | Provider call + circuit breaker that records *why* |
| `src/rag.py` | ChromaDB + local embeddings |
| `src/api.py` | FastAPI service |
| `src/models.py` | Model ids and capabilities, in one file |

---

## Measured

```
status:  online true, model openai/gpt-oss-120b
mode: agent_readonly | rounds: 3 | tools: ['read_file'] | 2602 ms
  CALL read_file({"path": "src/config.py"})
  CALL read_file({"path": "src/models.py"})
ANSWER: The project defaults to openai/gpt-oss-120b
```

It read two files and followed the import chain rather than guessing from the
first one.

---

## Interview Q&A

`docs/INTERVIEW_QA.md` — the pitch, the trust decisions, and the questions an
interviewer will actually ask, with answers grounded in this code.

## Known limits

- **Path sandbox, not process isolation.** The workspace path checks constrain the file tools; the fixed command surface removes the previous interpreter-launch escape route, but this is still not an OS-level sandbox.
- **TOCTOU**: `safe_join` resolves, then `open()` uses. A symlink swapped in
  between would bypass it. Production answers are `openat` with `O_NOFOLLOW`,
  or a container.
- **The knowledge base is one small markdown file.** Retrieval quality on a real
  corpus is unmeasured.
- **No write audit trail.** Writes are gated, but nothing records who asked for
  what. A production version would log every mutation.

## License

MIT.
