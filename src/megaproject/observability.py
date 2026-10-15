from collections import defaultdict, deque
from math import ceil
from threading import Lock
from time import perf_counter
from typing import Any


class ProcessMetrics:
    """Bounded, process-local HTTP metrics; not a cross-instance telemetry system."""

    def __init__(self, sample_window: int = 2048) -> None:
        self._lock = Lock()
        self._sample_window = sample_window
        self._in_flight = 0
        self._routes: dict[str, dict[str, Any]] = {}

    def start(self) -> float:
        with self._lock:
            self._in_flight += 1
        return perf_counter()

    def finish(self, route: str, status_code: int, started: float) -> None:
        elapsed_ms = (perf_counter() - started) * 1000
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            record = self._routes.setdefault(route, {"requests": 0, "errors": 0, "status_codes": defaultdict(int), "latency_samples_ms": deque(maxlen=self._sample_window)})
            record["requests"] += 1
            record["errors"] += int(status_code >= 400)
            record["status_codes"][str(status_code)] += 1
            record["latency_samples_ms"].append(elapsed_ms)

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, ceil(percentile * len(ordered)) - 1))
        return round(ordered[index], 2)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            routes = {
                name: {
                    "requests_since_start": record["requests"],
                    "errors_since_start": record["errors"],
                    "status_codes_since_start": dict(record["status_codes"]),
                    "latency_sample_count": len(record["latency_samples_ms"]),
                    "latency_window_ms": {
                        "p50": self._percentile(list(record["latency_samples_ms"]), 0.50),
                        "p95": self._percentile(list(record["latency_samples_ms"]), 0.95),
                        "p99": self._percentile(list(record["latency_samples_ms"]), 0.99),
                    },
                }
                for name, record in self._routes.items()
            }
            return {"scope": "single process; counters reset on restart", "in_flight": self._in_flight, "latency_percentiles": "recent bounded sample window, not all-time", "routes": routes}
