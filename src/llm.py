"""LLM layer with online/offline circuit-breaker.

Online: Groq (llama-3.3-70b). Offline: the breaker opens after N consecutive
failures and LLM calls raise OfflineError immediately (zero latency), so the
agent runtime switches to RAG-only synthesis. A timed probe re-closes the
breaker when the provider is reachable again.

Same degradation philosophy as hybrid-log-classifier's tiering: never crash,
fall back, keep serving.
"""
import os
import time

from src.config import LLM_MODEL, LLM_TIMEOUT_S, LLM_MAX_CONSECUTIVE_FAILURES, LLM_PROBE_INTERVAL_S
from src import store


class OfflineError(Exception):
    """Raised when the LLM tier is breaker-open (provider unreachable)."""


class _Breaker:
    def __init__(self):
        self.failures = 0
        self.opened_at = 0.0
        self.next_probe = 0.0

    @property
    def is_open(self) -> bool:
        if self.failures < LLM_MAX_CONSECUTIVE_FAILURES:
            return False
        # Time to probe again? Probe window reopens every LLM_PROBE_INTERVAL_S.
        return time.time() < self.next_probe

    def record_failure(self):
        self.failures += 1
        if self.failures >= LLM_MAX_CONSECUTIVE_FAILURES:
            self.opened_at = time.time()
            self.next_probe = self.opened_at + LLM_PROBE_INTERVAL_S

    def record_success(self):
        self.failures = 0
        self.opened_at = 0.0
        self.next_probe = 0.0

    def allow_probe(self) -> bool:
        """True when breaker is open but the probe interval elapsed (let one call through)."""
        return self.failures >= LLM_MAX_CONSECUTIVE_FAILURES and time.time() >= self.next_probe


_breaker = _Breaker()


def _client():
    from groq import Groq  # lazy import
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise OfflineError("GROQ_API_KEY not configured")
    return Groq(api_key=key), key


def _raw_call(messages, purpose, conv_id):
    start = time.time()
    try:
        client, _ = _client()
        resp = client.chat.completions.create(
            model=LLM_MODEL, messages=messages, temperature=0.2, timeout=LLM_TIMEOUT_S,
        )
        latency = (time.time() - start) * 1000
        _breaker.record_success()
        usage = getattr(resp, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        store.log_llm_call(conv_id, purpose, LLM_MODEL, "ok", latency, pt, ct)
        return resp.choices[0].message.content
    except OfflineError:
        raise
    except Exception as e:  # provider/network errors -> breaker
        latency = (time.time() - start) * 1000
        _breaker.record_failure()
        store.log_llm_call(conv_id, purpose, LLM_MODEL, "error", latency, error=f"{e.__class__.__name__}: {e}"[:200])
        raise OfflineError(str(e))


def llm_complete(messages: list, purpose: str = "respond", conv_id: str = None) -> str:
    """Chat completion. Raises OfflineError when offline/breaker-open."""
    if _breaker.is_open and not _breaker.allow_probe():
        store.log_llm_call(conv_id, purpose, LLM_MODEL, "offline", 0.0)
        raise OfflineError("LLM offline (circuit breaker open)")
    if not os.environ.get("GROQ_API_KEY"):
        store.log_llm_call(conv_id, purpose, LLM_MODEL, "offline", 0.0)
        raise OfflineError("GROQ_API_KEY not configured")
    return _raw_call(messages, purpose, conv_id)


def llm_status() -> dict:
    """UI-facing health snapshot."""
    key = bool(os.environ.get("GROQ_API_KEY"))
    return {
        "online": key and not _breaker.is_open,
        "consecutive_failures": _breaker.failures,
        "model": LLM_MODEL,
        "key_configured": key,
    }
