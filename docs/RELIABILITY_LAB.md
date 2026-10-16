# Reliability lab

This application exposes both client-side load results and process-local request metrics. They help answer different questions: the load generator captures the caller-visible result; /metrics measures requests seen by the running application process.

## Local setup

~~~bash
pip install -e .
uvicorn megaproject.app:app --host 127.0.0.1 --port 8000
~~~

Run a gradual read-only load ramp:

~~~bash
python scripts/load_test.py --base-url http://127.0.0.1:8000 --requests 100 --concurrency 10 --output artifacts/load-10.json
python scripts/load_test.py --base-url http://127.0.0.1:8000 --requests 500 --concurrency 25 --output artifacts/load-25.json
curl http://127.0.0.1:8000/metrics
~~~

The harness records p50/p95/p99/max latency, throughput, status codes, transport errors and response bytes. The generator redacts URL credentials, query strings and fragments from saved reports; pass secrets with `--header`, not in the URL. It uses a bounded worker pool, caps a run at 100,000 requests and 10,000 configured in-flight requests, and treats only 2xx responses as successful. These are client-side safety bounds, not target-capacity claims. It never reports a user count or estimates server CPU/memory.

## How to investigate a failure

1. Find the first load level where latency jumps or errors begin.
2. Correlate client reports with /metrics and the host's memory/CPU/restart logs.
3. Distinguish invalid request (422), rejected path (400), unexpected application failure (5xx), connection exhaustion, and host resource exhaustion.
4. Change one thing, rerun the same request mix, and retain the failed and retest JSON files.
5. Record the exact commit, plan, region, cold/warm state, requests and concurrency. Repeat each level at least three times.

## Boundary and threat model

The API only accepts list/read tools, at most four steps per request, paths of at most 240 characters, reads up to 100 KB, and directory listings up to 200 visible entries. Hidden paths are rejected. The configured workspace defaults to the dedicated workspace/ directory rather than the repository or process working directory.

This is an application-level path boundary, not OS-level isolation. It has no write or shell-execution tools. A hostile workload still belongs inside a properly configured container/VM with OS permissions and resource limits. The public demo workspace must never contain credentials or private documents.

## Capacity honesty

The `/run` endpoint has a shared process-local request ceiling of 120/minute by default (`API_REQUESTS_PER_MINUTE`). This is a basic demo guard, not authentication or distributed/per-user rate limiting.

A concurrency setting is in-flight requests from the load generator, not human users or sessions. Do not claim a service supports 10,000 concurrent users from a 10,000-request test. Test a ramp, publish the configuration and measured outcome, and identify the first failure point. Free Render services sleep after inactivity, use ephemeral storage, and cannot scale to multiple instances; the blueprint is for a demo, not an uptime promise.
