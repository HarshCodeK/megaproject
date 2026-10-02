"""Offline tests. No network, no API key.

Run:  python -m pytest tests/ -q
"""
import ast
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import models, workspace  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# The trust boundary. This is the part worth defending, so it is tested hardest.
# ---------------------------------------------------------------------------
class TestSandbox:
    @pytest.mark.parametrize("path", [
        "../../../../Windows/System32/drivers/etc/hosts",
        "..\\..\\secrets.txt",
        "C:/Windows/win.ini",
        "C:\\Windows\\win.ini",
        "/etc/passwd",
        "//server/share/file",
        "~/.ssh/id_rsa",
    ])
    def test_escapes_refused(self, path):
        with pytest.raises(workspace.SandboxError):
            workspace.safe_join(ROOT, path)

    def test_absolute_detection_is_platform_independent(self):
        """The check must not change meaning with the host OS.

        Caught in CI on Linux: os.path.isabs("C:/Windows/win.ini") is False
        there, so a Windows path was resolved *inside* the workspace. On
        Windows the inverse holds -- isabs("/etc/passwd") is False. Relying on
        the stdlib made the sandbox weaker on whichever OS it was not written on.
        """
        for p in ("C:/Windows/win.ini", "c:\\temp\\x", "/etc/passwd",
                  "\\\\server\\share", "//server/share"):
            assert workspace._looks_absolute(p), f"{p!r} should be absolute"
        for p in ("src/config.py", "src/../src/config.py", "notes.txt"):
            assert not workspace._looks_absolute(p), f"{p!r} should be relative"

    def test_empty_and_nul_refused(self):
        for bad in ("", "x\x00y"):
            with pytest.raises(workspace.SandboxError):
                workspace.safe_join(ROOT, bad)

    def test_inside_path_allowed(self):
        resolved = workspace.safe_join(ROOT, os.path.join("src", "agent.py"))
        assert os.path.isfile(resolved)

    def test_traversal_that_stays_inside_is_allowed(self):
        # "src/../src/config.py" resolves inside the workspace, so it is fine.
        assert workspace.safe_join(ROOT, "src/../src/config.py").endswith("config.py")

    def test_prefix_confusion_is_refused(self):
        """A sibling directory sharing the root's name prefix must not pass."""
        with pytest.raises(workspace.SandboxError):
            workspace.safe_join(ROOT, os.path.join("..", ROOT + "_evil", "x"))


class TestToolsRespectTheSandbox:
    def _ctx(self, **kw):
        return {"root": ROOT, "allow_write": False, **kw}

    def test_read_file_refuses_an_escape(self):
        from src.tools import read_file
        out = read_file({"path": "../../../etc/passwd"}, self._ctx())
        assert out["ok"] is False and "sandbox" in out["error"]

    def test_grep_refuses_an_escape(self):
        from src.tools import grep
        out = grep({"pattern": ".", "path": "../.."}, self._ctx())
        assert out["ok"] is False and "sandbox" in out["error"]

    def test_write_refused_when_writes_disabled(self):
        from src.tools import edit_file, write_file
        ctx = self._ctx()
        assert "disabled" in write_file({"path": "x.txt", "content": "y"}, ctx)["error"]
        assert "disabled" in edit_file({"path": "x.txt", "old": "a", "new": "b"}, ctx)["error"]

    def test_bad_regex_is_data(self):
        from src.tools import grep
        out = grep({"pattern": "[unclosed"}, self._ctx())
        assert out["ok"] is False and "bad regex" in out["error"]

    def test_binary_file_refused(self, tmp_path):
        from src.tools import read_file
        target = tmp_path / "b.bin"
        target.write_bytes(b"\x00\x01\x02")
        out = read_file({"path": str(target)}, {"root": str(tmp_path), "allow_write": False})
        assert out["ok"] is False

    def test_unknown_tool_refused(self):
        from src.tools import call
        assert call("rm_rf", {}, {"root": ROOT})["ok"] is False


class TestCommandAllowlist:
    def _ctx(self):
        return {"root": ROOT, "allow_write": False}

    def test_allowed_command_runs(self):
        from src.tools import run_cmd
        out = run_cmd({"command": "python3 --version"}, self._ctx())
        assert out["ok"] is True

    def test_unlisted_executable_refused(self):
        from src.tools import run_cmd
        out = run_cmd({"command": "curl https://example.com"}, self._ctx())
        assert out["ok"] is False and "not allowlisted" in out["error"]

    def test_git_subcommand_allowlist(self):
        from src.tools import run_cmd
        assert run_cmd({"command": "git status"}, self._ctx())["ok"] is True
        # git push is a write; the agent must not be able to run it.
        assert run_cmd({"command": "git push origin main"}, self._ctx())["ok"] is False

    def test_empty_command_refused(self):
        from src.tools import run_cmd
        assert run_cmd({"command": "  "}, self._ctx())["ok"] is False


class TestSecretRedaction:
    def test_env_secrets_are_redacted(self, monkeypatch):
        from src.tools import redact
        monkeypatch.setenv("MY_TEST_API_KEY", "super-secret-value-1234")
        assert "super-secret-value-1234" not in redact("key is super-secret-value-1234")
        assert "***" in redact("key is super-secret-value-1234")

    def test_short_values_are_not_redacted(self):
        # Redacting every short env value would mangle ordinary output.
        from src.tools import redact
        assert redact("value is abc") == "value is abc"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class TestModels:
    def test_default_is_live(self):
        assert models.resolve() in models.MODELS
        assert models.supports_tools(models.DEFAULT_MODEL)

    def test_unknown_model_raises(self):
        for dead in ("llama-3.3-70b-versatile", "qwen/qwen3.6-27b"):
            with pytest.raises(models.UnknownModel):
                models.resolve(dead)

    def test_no_retired_model_pinned_in_source(self):
        retired = ("llama-3.3-70b-versatile", "llama-4-scout-17b-16e-instruct",
                   "qwen/qwen3-32b", "qwen/qwen3.6-27b")
        offenders = []
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in {"__pycache__", ".git", "chroma_db", "tests", ".venv"}]
            for name in filenames:
                if not name.endswith(".py") or name == "models.py":
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
                        for dead in retired:
                            if dead in node.value:
                                offenders.append(f"{name}:{node.lineno}")
        assert not offenders, f"retired model pinned as a value: {offenders}"


# ---------------------------------------------------------------------------
# The breaker: this is the failure that hid a dead model for months
# ---------------------------------------------------------------------------
class TestBreaker:
    def test_status_separates_configured_from_online(self):
        from src import llm
        s = llm.status()
        for key in ("online", "key_configured", "last_error", "breaker_open"):
            assert key in s

    def test_reason_is_recorded_and_cleared(self):
        from src.llm import Breaker
        b = Breaker()
        b.record_failure("NotFoundError: model does not exist")
        assert "NotFoundError" in b.last_error
        b.record_success()
        assert b.last_error is None and b.failures == 0

    def test_opens_after_threshold(self, monkeypatch):
        import time
        from src.config import MAX_FAILURES
        from src.llm import Breaker
        b = Breaker()
        for _ in range(MAX_FAILURES):
            b.record_failure("err")
        assert b.is_open is True
        monkeypatch.setattr(time, "time", lambda: b.next_probe + 1)
        assert b.is_open is False

    def test_offline_error_when_no_key(self, monkeypatch):
        from src import llm
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with pytest.raises(llm.OfflineError, match="not configured"):
            llm.complete([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# The agent loop, LLM stubbed
# ---------------------------------------------------------------------------
class _Msg:
    def __init__(self, content=None, calls=None):
        self.content = content
        self.tool_calls = calls


def _final(text):
    return _Msg(content=text)


def _call(name, args):
    tc = type("TC", (), {"id": "c1", "function": type(
        "F", (), {"name": name, "arguments": json_dumps(args)})()})()
    return _Msg(content=None, calls=[tc])


def json_dumps(obj):
    import json
    return json.dumps(obj)


class TestAgentLoop:
    def test_answers_without_a_tool(self, monkeypatch):
        from src import agent as agent_mod
        monkeypatch.setattr(agent_mod.llm, "complete",
                            lambda *a, **k: _final("The default is qwen/qwen3.8-27b."))
        r = agent_mod.run("which model?", allow_write=False)
        assert r["rounds"] == 1 and "qwen" in r["answer"]

    def test_uses_a_tool_then_answers(self, monkeypatch):
        from src import agent as agent_mod
        seq = [_call("read_file", {"path": "src/config.py"}),
               _final("It reads the model from MP_MODEL.")]
        monkeypatch.setattr(agent_mod.llm, "complete", lambda *a, **k: seq.pop(0))
        r = agent_mod.run("what is the default model?", allow_write=False)
        assert r["tools_used"] == ["read_file"]
        assert r["rounds"] == 2

    def test_round_cap_terminates(self, monkeypatch):
        from src import agent as agent_mod
        monkeypatch.setattr(agent_mod.llm, "complete",
                            lambda *a, **k: _call("list_dir", {"path": "."}))
        r = agent_mod.run("loop", max_rounds=3, allow_write=False)
        assert r["rounds"] == 3 and r["mode"] == "agent_max_rounds"

    def test_offline_falls_back_to_retrieval(self, monkeypatch):
        from src import agent as agent_mod, llm
        monkeypatch.setattr(agent_mod.llm, "complete",
                            lambda *a, **k: (_ for _ in ()).throw(
                                llm.OfflineError("provider unreachable")))
        r = agent_mod.run("what is the refund window?", allow_write=False)
        assert r["mode"] == "agent_offline"
        assert r["answer"]

    def test_readonly_mode_reported(self, monkeypatch):
        from src import agent as agent_mod
        monkeypatch.setattr(agent_mod.llm, "complete", lambda *a, **k: _final("done"))
        assert agent_mod.run("x", allow_write=False)["mode"] == "agent_readonly"

    def test_tools_are_offered_to_the_model(self, monkeypatch):
        from src import agent as agent_mod
        seen = {}

        def capture(messages, tools=None, **k):
            seen["tools"] = tools
            return _final("ok")

        monkeypatch.setattr(agent_mod.llm, "complete", capture)
        agent_mod.run("x", allow_write=False)
        assert len(seen["tools"]) == 7


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
class TestRetrieval:
    def test_ingest_then_search(self, tmp_path, monkeypatch):
        from src import rag
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "refunds.md").write_text(
            "All digital purchases are refundable within 14 days of the charge date. " * 10,
            encoding="utf-8")
        (docs / "support.md").write_text(
            "Tier 1 tickets receive a first response within 4 business hours. " * 10,
            encoding="utf-8")
        monkeypatch.setattr(rag, "CHROMA_DIR", str(tmp_path / "chroma"))
        monkeypatch.setattr(rag, "_client", None)
        monkeypatch.setattr(rag, "_embedder", None)
        try:
            assert rag.ingest(str(docs)) > 0
            hits = rag.search("how long do I have to get a refund")
            assert hits and hits[0]["source"] == "refunds.md"
        finally:
            monkeypatch.setattr(rag, "_client", None)
            monkeypatch.setattr(rag, "_embedder", None)
