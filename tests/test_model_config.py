"""Model-configuration guards.

This project previously defaulted to `llama-3.3-70b-versatile`, retired by Groq
on 2026-07-17. The failure was silent in the worst way: the circuit breaker
opened, the UI reported "offline", and offline degradation looked like it was
working as designed.

These tests exist so that failure mode cannot come back quietly.

Run from repo root:  python -m pytest tests/ -q
"""
import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, llm  # noqa: E402

# Groq retirements relevant to this project, with shutdown dates.
RETIRED = {
    "llama-3.3-70b-versatile": "2026-07-17",
    "llama-4-scout-17b-16e-instruct": "2026-07-17",
    "llama-3.1-8b-instant": "2026-07-17",
    "qwen/qwen3-32b": "2026-07-17",
    "qwen/qwen3.6-27b": "2026-09-14",
}


class TestModelConfig:
    def test_default_model_is_not_retired(self):
        assert config.LLM_MODEL not in RETIRED, (
            f"{config.LLM_MODEL} was retired by Groq on "
            f"{RETIRED.get(config.LLM_MODEL, 'unknown')}"
        )

    def test_default_model_is_known_live(self):
        """Pinned against the account's live model list, checked 2026-10-02."""
        live = {
            "qwen/qwen3.8-27b",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-safeguard-20b",
            "allam-2-7b",
            "whisper-large-v3",
            "whisper-large-v3-turbo",
        }
        assert config.LLM_MODEL in live

    def test_no_retired_model_pinned_in_source(self):
        """Walk the AST; docstrings are exempt because documenting the
        migration is the point, but a retired id as a *value* is a bug."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in {".git", "__pycache__", "chroma_db", "tests"}]
            for name in filenames:
                if not name.endswith(".py") or name == "config.py":
                    continue
                with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                    tree = ast.parse(f.read())

                docstrings = set()
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                        body = getattr(node, "body", None)
                        if (body and isinstance(body[0], ast.Expr)
                                and isinstance(body[0].value, ast.Constant)
                                and isinstance(body[0].value.value, str)):
                            docstrings.add(id(body[0].value))

                for node in ast.walk(tree):
                    if (isinstance(node, ast.Constant)
                            and isinstance(node.value, str)
                            and id(node) not in docstrings):
                        for dead in RETIRED:
                            if dead in node.value:
                                offenders.append(f"{name}:{node.lineno} -> {dead}")

        assert not offenders, f"retired model pinned in code: {offenders}"

    def test_env_example_does_not_advertise_a_dead_model(self):
        """A commented-out example is how dead ids come back."""
        # config.ROOT is already the repo root, so .env.example sits directly
        # inside it. (Joining ROOT with ".env.example" a second time looked for
        # a file one directory too high, and the test silently skipped.)
        path = os.path.join(config.ROOT, ".env.example")
        if not os.path.exists(path):
            pytest.skip("no .env.example")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for dead in RETIRED:
            assert dead not in text, f".env.example still mentions {dead}"


class TestBreakerReportsWhy:
    """The whole point: an operator must be able to tell a dead model apart
    from a network outage."""

    def test_status_separates_configured_from_online(self):
        status = llm.llm_status()
        assert "key_configured" in status
        assert "online" in status
        assert "last_error" in status

    def test_failure_reason_is_recorded(self):
        breaker = llm._Breaker()
        breaker.record_failure("NotFoundError: model does not exist")
        assert breaker.failures == 1
        assert "NotFoundError" in breaker.last_error

    def test_success_clears_the_reason(self):
        breaker = llm._Breaker()
        breaker.record_failure("boom")
        breaker.record_success()
        assert breaker.last_error is None
        assert breaker.failures == 0

    def test_breaker_opens_after_threshold(self, monkeypatch):
        import time

        breaker = llm._Breaker()
        for _ in range(config.LLM_MAX_CONSECUTIVE_FAILURES):
            breaker.record_failure("err")
        assert breaker.is_open is True
        # Far in the future, the probe window has elapsed and it closes again.
        monkeypatch.setattr(time, "time", lambda: breaker.opened_at + 10_000)
        assert breaker.is_open is False
        assert breaker.allow_probe() is True
