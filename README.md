# megaproject

> **A real agent OS with tool-calling, MCP support, and a workspace sandbox.**
> Not a chatbot with RAG. A real tool-calling agent that reads files, greps code, runs commands, edits files, and surfaces its thinking — all behind a trust boundary.

![python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![fastapi](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![streamlit](https://img.shields.io/badge/Streamlit-1.38+-FF4B4B?logo=streamlit&logoColor=white)
![chromadb](https://img.shields.io/badge/ChromaDB-0.5+-purple)
![license](https://img.shields.io/badge/license-MIT-lightgrey)
![tier](https://img.shields.io/badge/tier-S-4CC38A)

---

## What this is

Megaproject is a **real tool-calling coding assistant** — the kind of system that powers opencode, Claude Code, and Hermes. Hand-rolled. No LangChain. No LangGraph. ~120 lines of agent loop doing what LangChain does in 10k.

The agent thinks, calls tools (read files, grep, run commands), observes results, reflects, and continues until the task is done. Every step is traced. Writes are gated behind explicit opt-in. The workspace is sandboxed.

---

## Architecture

```
User task
   │
   ▼
Operator loop (plan → act → observe → reflect)
   │
   ├── LLM (Groq llama-3.3-70b) with function calling
   │
   ├── 7 tools behind the workspace sandbox:
   │   read_file    — read any file in the workspace
   │   list_dir     — list directories
   │   grep         — regex search across files
   │   run_cmd      — allowlisted commands (python, pytest, git, ls)
   │   write_file   — create/overwrite files (WRITE-GATED)
   │   edit_file    — exact unique-string replacement (WRITE-GATED)
   │   rag_search   — search the knowledge base
   │
   └── Full trace logged to SQLite
```

Three modes:
- **`operator`** — full online: multi-round tool-calling with thinking
- **`operator_readonly`** — writes disabled, read + search + run only
- **`operator_offline`** — circuit breaker opened → partial trace returned

---

## The workspace sandbox

Every file the operator touches resolves inside the workspace root. Absolute paths, `..` escapes, and symlinks pointing outside are rejected with `SandboxError` before any I/O happens.

```
The model is untrusted. The sandbox is trusted.
```

Writes require TWO locks:
1. `MP_ALLOW_WRITE=1` env var (default: off)
2. `MP_ALLOW_OPERATOR_WRITE_VIA_API=1` for the API endpoint

Secrets are redacted from all tool output — child processes may echo env vars, but keys never reach the trace.

---

## MCP server

Megaproject exposes all 7 tools as a **Model Context Protocol (MCP)** server. Any AI coding assistant can connect:

```bash
# stdio mode (for local integration)
python -m src.mcp_server

# HTTP mode (for remote access)
python -m src.mcp_server --http 8765
```

Protocol: JSON-RPC 2.0 over newline-delimited stdin/stdout (or HTTP POST).

---

## Quickstart

```bash
pip install -r requirements.txt

# optional — enables agent mode (without it, read-only mode works)
export GROQ_API_KEY=...

# UI
python -m streamlit run app.py

# REST API
python -m uvicorn src.api:app --reload
# open http://localhost:8000/docs
```

---

## API

| Endpoint | Method | What |
|---|---|---|
| `/chat` | POST | One agent turn — `{message, conv_id?}` → answer + mode + sources |
| `/operator` | POST | Run operator — `{task, conv_id?, allow_write?, max_rounds?}` → trace |
| `/operator/runs` | GET | Recent operator runs |
| `/operator/runs/{id}` | GET | Single run with full trace |
| `/conversations` | GET | List conversations |
| `/conversations/{id}/messages` | GET | Full transcript |
| `/ingest` | POST | Re-chunk + re-embed `knowledge_docs/` |
| `/tools` | GET | List available operator tools |
| `/health` | GET | LLM status + KB size + operator config |

---

## Operator tools

| Tool | Args | Gated? | What |
|---|---|---|---|
| `read_file` | `{path}` | no | Read file contents |
| `list_dir` | `{path?}` | no | List directory entries |
| `grep` | `{pattern, path?, include?, max_hits?}` | no | Regex search files |
| `run_cmd` | `{command, timeout_s?}` | no | Run allowlisted command |
| `write_file` | `{path, content}` | **YES** | Create/overwrite file |
| `edit_file` | `{path, old, new}` | **YES** | Unique-string replacement |
| `rag_search` | `{query}` | no | Search knowledge base |

---

## Evidence

| Metric | Value | Reproduce |
|---|---|---|
| source modules | 9 | `ls src/` |
| tests | 34 offline | `python -m pytest tests/ -v` |
| operator tools | 7 | `grep TOOLS src/op_tools.py` |
| MCP tools | 7 | `python -m src.mcp_server` then `tools/list` |
| trust boundaries | 3 | sandbox + write-gate + secret-redaction |
| degradation modes | 4 | operator / operator_readline / operator_offline / rag_only |
| circuit breaker | yes | 2 failures → probe every 60s |
| everything logged | ✓ | conversations, messages, tool/LLM/RAG/operator calls in SQLite |

---

## What this is NOT

- No autonomous background execution — bounded rounds, explicit stop
- No framework dependencies — hand-rolled agent loop, real function calling
- No real-money operations — demo-scope tools, production-grade architecture
- No plugin hot-loading — tools are code modules, not dynamic

---

## License

MIT
