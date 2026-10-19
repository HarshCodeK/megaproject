"""Repeatable HTTP load probe. Reports client-observed behavior only."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
import httpx
from urllib.parse import urlsplit, urlunsplit

DEFAULT_PAYLOAD = {"steps": [{"tool": "read", "args": {"path": "demo.txt"}}]}

def safe_url(url: str) -> str:
    """Return a report-safe URL without credentials, query parameters or fragments."""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return "<invalid URL>"



def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return round(ordered[index], 2)


async def run(args: argparse.Namespace) -> dict:
    headers: dict[str, str] = {}
    for item in args.header:
        if "=" not in item:
            raise ValueError(f"invalid --header {item!r}; expected NAME=VALUE")
        name, value = item.split("=", 1)
        headers[name.strip()] = value
    payload = json.loads(args.payload) if args.payload else None
    limits = httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=min(args.concurrency, 100))
    latencies: list[float] = []
    statuses: dict[str, int] = {}
    transport_errors: dict[str, int] = {}
    success = errors = response_bytes = 0
    semaphore = asyncio.Semaphore(args.concurrency)
    total_started = perf_counter()
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        async def request_one() -> None:
            nonlocal success, errors, response_bytes
            async with semaphore:
                started = perf_counter()
                try:
                    response = await client.request(args.method, f"{args.base_url.rstrip('/')}{args.path}", headers=headers, json=payload if args.method in {"POST", "PUT", "PATCH"} else None)
                    latencies.append((perf_counter() - started) * 1000)
                    code = str(response.status_code)
                    statuses[code] = statuses.get(code, 0) + 1
                    response_bytes += len(response.content)
                    if 200 <= response.status_code < 300:
                        success += 1
                    else:
                        errors += 1
                except (httpx.HTTPError, OSError) as exc:
                    latencies.append((perf_counter() - started) * 1000)
                    key = type(exc).__name__
                    transport_errors[key] = transport_errors.get(key, 0) + 1
                    errors += 1
        # Use a bounded worker pool instead of allocating one task per request.
        next_request = 0
        next_request_lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal next_request
            while True:
                async with next_request_lock:
                    if next_request >= args.requests:
                        return
                    next_request += 1
                await request_one()

        await asyncio.gather(*(worker() for _ in range(min(args.concurrency, args.requests))))
    duration = max(perf_counter() - total_started, 1e-9)
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "client_python": platform.python_version(),
        "scope": "client-side HTTP behavior; no claim about server CPU or memory",
        "base_url": safe_url(args.base_url.rstrip("/")), "method": args.method, "path": args.path,
        "requested_requests": args.requests, "configured_concurrency": args.concurrency,
        "duration_seconds": round(duration, 3), "throughput_requests_per_second": round(success / duration, 2),
        "attempted_requests_per_second": round(args.requests / duration, 2),
        "success_count": success, "error_count": errors, "error_rate": round(errors / args.requests, 5),
        "latency_ms": {"p50": percentile(latencies, 0.50), "p95": percentile(latencies, 0.95), "p99": percentile(latencies, 0.99), "max": round(max(latencies), 2) if latencies else None},
        "status_codes": statuses, "transport_errors": transport_errors, "response_bytes_received": response_bytes,
        "notes": ["Concurrency is in-flight requests from this generator, not human users or sessions.", "Collect server CPU, memory, restarts and logs separately from the host.", "Record commit, hosting plan, region, cold/warm state and request mix."],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", ""))
    parser.add_argument("--path", default="/run")
    parser.add_argument("--method", choices=["GET", "POST", "PUT", "PATCH"], default="POST")
    parser.add_argument("--payload", default=json.dumps(DEFAULT_PAYLOAD))
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--header", action="append", default=[], help="HTTP header as NAME=VALUE")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    if not args.base_url:
        parser.error("pass --base-url or set BASE_URL")
    if args.requests < 1 or args.concurrency < 1 or args.timeout <= 0:
        parser.error("--requests, --concurrency and --timeout must be positive")
    if args.requests > 100_000:
        parser.error("--requests is capped at 100000 to bound the test run")
    if args.concurrency > 10_000:
        parser.error("--concurrency is capped at 10000; start lower and ramp gradually")
    if args.concurrency > 200:
        print("WARNING: high concurrency can overload a free instance; Quick Tunnels are documented for up to 200 in-flight requests.", file=sys.stderr)
    try:
        report = asyncio.run(run(args))
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"load test could not complete: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved client-side report to {target}", file=sys.stderr)
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
