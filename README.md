# MegaProject — bounded read-only tool agent

MegaProject is a small Python/FastAPI tool-execution boundary for demonstrating the security and reliability decisions around file tools. It accepts a short sequence of explicit tool calls and restricts reads/listing to a dedicated workspace.

Important: the current implementation does **not** call an LLM or autonomously plan. The request supplies the tool steps. This isolates the deterministic tool boundary so it can be tested without model cost or nondeterminism. It is an application-level path sandbox, not OS-level isolation.

## What it demonstrates

- Tool allowlisting: only `list` and `read` are accepted.
- Workspace-root confinement after path resolution, including symlink escapes.
- Hidden paths rejected to reduce accidental secret exposure.
- At most four steps per request.
- Read cap of 100 KB, path cap of 240 characters, and directory listing cap of 200 visible entries.
- Consistent client errors for rejected paths rather than unhandled server exceptions.
- Small browser UI, OpenAPI docs and process-local request metrics.
- A bounded load-test harness for latency, throughput, HTTP status codes and errors.

## Quickstart

Requires Python 3.11+.

~~~bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
uvicorn megaproject.app:app --reload
~~~

Open the root page for the UI, /docs for OpenAPI, /health for liveness, /ready for workspace readiness, and /metrics for per-process observations.

## API example

~~~bash
curl -s http://127.0.0.1:8000/run \
  -H 'Content-Type: application/json' \
  -d '{"steps":[{"tool":"read","args":{"path":"demo.txt"}}]}'
~~~

A rejected workspace path returns HTTP 400. Invalid tools, unknown fields and more than four steps are rejected by request validation. No file-write or shell tool is exposed.

## Workspace boundary

The default root is the repository's dedicated workspace/ directory. Set WORKSPACE_ROOT to a dedicated directory when deploying. Do not place credentials, private data, Git metadata or environment files inside it. Hidden files are not listed or readable. Reads stream only up to the configured byte limit before failing; the entire file is not loaded before checking its size.

This is not a real OS sandbox. It does not protect against a vulnerability in the Python process or its dependencies. If executing hostile third-party workloads is ever added, use container/VM isolation, non-root permissions, network policy, CPU/memory limits and an external audit boundary. Do not add shell execution casually.

## Tests

~~~bash
pytest -q
python -m compileall -q src scripts
~~~

The tests cover path traversal, symlink escapes, hidden files, oversized reads, oversized listings, API input validation and metrics accounting.

## Reproducible reliability testing

See docs/RELIABILITY_LAB.md. Start small and increase concurrency in controlled steps:

~~~bash
python scripts/load_test.py --base-url http://127.0.0.1:8000 --requests 100 --concurrency 10 --output artifacts/load-10.json
python scripts/load_test.py --base-url http://127.0.0.1:8000 --requests 500 --concurrency 25 --output artifacts/load-25.json
curl http://127.0.0.1:8000/metrics
~~~

Reports include client-observed throughput, p50/p95/p99/max latency, error rate, HTTP status distribution, transport errors and response bytes. They do not assert real users or server capacity. Record the exact commit, host plan, region, cold/warm state and request mix, and retain failed and retest reports together.

## Deployment

The root render.yaml declares a free Render API deployment and confines its workspace to a dedicated directory. Free instances sleep after inactivity, have ephemeral storage and cannot scale to multiple instances. This is a public demo configuration, not production uptime or scale.

## Known limitations

There is no LLM-based planning, authentication, file-write support, tenant-specific workspace separation, rate limiting, OS-level sandbox, persistent job queue or cross-instance metrics. The endpoint is read-only, but a public instance can still use CPU and connections; limit exposure and load-test only your own deployment.

## License

MIT. See LICENSE.
