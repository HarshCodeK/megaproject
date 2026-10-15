# MegaProject — Sandboxed Tool-Agent Prototype

A small tool-calling boundary where the requested tool and arguments are treated as untrusted input and filesystem access is constrained to a resolved workspace.

**Stack:** Python, FastAPI, Pydantic, pytest.

## Problem and design goal

An agent that can read files needs a narrow tool boundary. This project demonstrates an allowlisted set of filesystem operations, resolved-path validation, bounded reads, and a maximum number of tool rounds. The key design principle is that prompt instructions are not a security boundary: filesystem access must be constrained by code.

## Architecture

`POST /run → request validation → bounded step loop → tool allowlist/dispatch → Workspace path validation → result trace`

- `Workspace.path()` rejects absolute paths, parent traversal, home expansion, and resolved paths outside the workspace.
- `Workspace.read()` caps file reads at 100,000 bytes by default and decodes invalid UTF-8 with replacement characters.
- `Workspace.list()` lists names inside a validated directory.
- `Agent.run()` executes at most four submitted steps by default.
- The FastAPI layer exposes `GET /health` and `POST /run`.

The HTTP request supplies the steps directly; this repository does **not** currently call an LLM to plan tools. It is a tool-execution boundary prototype rather than a complete autonomous agent.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn megaproject.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for the request schema. Run tests with:

```bash
pytest -q
```

## Security boundary: what is and is not guaranteed

Path resolution blocks common traversal and symlink escapes from the configured workspace. This is a **path sandbox only**, not OS-level isolation. The application currently constructs `Workspace(".")`, so the effective workspace depends on the process working directory. The service has no authentication or authorization, no request-size policy beyond framework defaults, no audit log, and no OS/container boundary. The tool interface must not be exposed publicly with access to sensitive files.

The `/run` endpoint accepts arbitrary step lists but the agent truncates execution to four steps; unknown tools depend on dispatch behavior. The workspace root should be explicitly configured rather than implicitly tied to the launch directory before deployment. Hostile workloads require a separate OS user and container/VM restrictions, read-only mounts where possible, resource limits, and network restrictions.

## Capacity and load testing

No measured concurrency, throughput, or availability claim is made. A reproducible test should identify the exact commit, runtime, host/plan, process worker count, request size, number of steps, file sizes, test duration, and concurrency level. Measure throughput, p50/p95/p99 latency, HTTP errors, CPU/RAM, and the latency distribution by tool type. Include successful reads, missing files, invalid paths, oversized files, and malformed requests.

Test security invariants under concurrency as well as happy-path performance. Do not benchmark against real secrets or sensitive files. Simulated clients are load-test traffic, not real users. A passing load test cannot establish that a path sandbox is secure against arbitrary hostile workloads.

## Known limitations

- Tool steps are supplied by the client; there is no LLM planning loop.
- Workspace root is currently the process working directory.
- Path validation is not OS-level isolation.
- No authentication, rate limiting, durable audit trail, or resource quotas are implemented.
- Only a small filesystem tool surface is intended.
- No production security, availability, or maximum-concurrency guarantee is claimed.

## Interview discussion

A strong next step is to add adversarial tests for traversal and symlink behavior, define a fixed workspace root, introduce explicit tool schemas and request limits, and measure the system before adding more tools. Keep authorization and OS-level isolation separate from the model's instructions.

## License

MIT.
