"""The LLM tier, with a circuit breaker and an honest health report.

Why a breaker: an unreachable provider turns every call into a 20-second
timeout. After MAX_FAILURES the breaker opens and calls fail instantly, so the
system degrades to retrieval-only instead of hanging.

Why the breaker records *why* it opened: a 404 (the model id is wrong or was
retired) and a timeout both trip it, and they need opposite fixes. Reporting
only "offline" hides which one happened -- and that is exactly how the previous
version of this project looked healthy while its entire online tier was dead.
"""
import os
import time

from src.config import LLM_MODEL, MAX_FAILURES, PROBE_INTERVAL_S


class OfflineError(Exception):
    """The breaker is open, or no API key is configured."""


class Breaker:
    """Open after MAX_FAILURES failures; one probe allowed per interval."""

    def __init__(self):
        self.failures = 0
        self.next_probe = 0.0
        self.last_error = None

    @property
    def is_open(self) -> bool:
        return self.failures >= MAX_FAILURES and time.time() < self.next_probe

    def record_failure(self, error: str):
        self.failures += 1
        self.last_error = error
        if self.failures >= MAX_FAILURES:
            self.next_probe = time.time() + PROBE_INTERVAL_S

    def record_success(self):
        self.failures = 0
        self.next_probe = 0.0
        self.last_error = None

    def allow_probe(self) -> bool:
        return self.failures >= MAX_FAILURES and time.time() >= self.next_probe


_breaker = Breaker()


def key_configured() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def complete(messages: list, tools: list = None, model_id: str = None,
             temperature: float = 0.2) -> dict:
    """One completion. Raises OfflineError rather than hanging or 500-ing."""
    if not key_configured():
        raise OfflineError("GROQ_API_KEY is not configured")
    if _breaker.is_open and not _breaker.allow_probe():
        raise OfflineError(f"provider unreachable: {_breaker.last_error}")

    from src import models
    resolved = models.resolve(model_id or LLM_MODEL)
    if tools and not models.supports_tools(resolved):
        raise OfflineError(f"{resolved} does not support tool calling")

    from groq import Groq
    payload = {"model": resolved, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        resp = Groq(api_key=os.environ["GROQ_API_KEY"]).chat.completions.create(**payload)
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"[:200]
        _breaker.record_failure(detail)
        raise OfflineError(detail) from e

    _breaker.record_success()
    return resp.choices[0].message


def status() -> dict:
    """Health, with the reason the last failure happened."""
    return {
        "online": key_configured() and not _breaker.is_open,
        "key_configured": key_configured(),
        "model": LLM_MODEL,
        "consecutive_failures": _breaker.failures,
        "breaker_open": _breaker.is_open,
        "last_error": _breaker.last_error,
    }
