"""Offline test suite — no network, no API key.

Tests the operator (tool-calling loop), MCP server, tools, and integration.
Run: python -m pytest tests/ -v
"""
import os
import sys
import json
import importlib
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fixtures — fresh isolated env per test
# ---------------------------------------------------------------------------

def _fresh(tmp_path, monkeypatch):
    """Set up a clean isolated workspace for each test."""
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    monkeypatch.setenv("MP_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MP_CHROMA", str(tmp_path / "chroma"))
    monkeypatch.setenv("MP_DOCS", str(tmp_path / "docs"))
    monkeypatch.setenv("MP_WORKSPACE", str(workspace_dir))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("MP_ALLOW_WRITE", raising=False)

    for m in ("src.config", "src.store", "src.rag", "src.llm", "src.agent",
              "src.workspace", "src.op_tools", "src.operator", "src.mcp_server", "src.api"):
        sys.modules.pop(m, None)

    import src.config as config
    importlib.reload(config)
    os.makedirs(config.DOCS_DIR, exist_ok=True)

    import src.store as store
    importlib.reload(store)
    store.init_db()

    import src.rag as rag
    importlib.reload(rag)

    import src.llm as llm
    importlib.reload(llm)

    import src.workspace as ws
    importlib.reload(ws)

    import src.op_tools as op
    importlib.reload(op)

    return config, store, rag, llm, ws, op, workspace_dir


# ---------------------------------------------------------------------------
# Workspace sandbox tests
# ---------------------------------------------------------------------------

class TestWorkspaceSandbox:
    def test_safe_join_normal(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        root = ws.resolve_root()
        result = ws.safe_join(root, "src/main.py")
        assert result.startswith(str(wd))
        assert "main.py" in result

    def test_safe_join_rejects_absolute(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        root = ws.resolve_root()
        import pytest
        with pytest.raises(ws.SandboxError, match="escapes|absolute"):
            ws.safe_join(root, "/etc/passwd")

    def test_safe_join_rejects_dotdot(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        root = ws.resolve_root()
        import pytest
        with pytest.raises(ws.SandboxError, match="escapes"):
            ws.safe_join(root, "../etc/passwd")

    def test_safe_join_rejects_empty(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        root = ws.resolve_root()
        import pytest
        with pytest.raises(ws.SandboxError, match="empty"):
            ws.safe_join(root, "")

    def test_safe_join_rejects_tilde(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        root = ws.resolve_root()
        import pytest
        with pytest.raises(ws.SandboxError, match="absolute"):
            ws.safe_join(root, "~/secret.txt")


# ---------------------------------------------------------------------------
# Tool tests
# ---------------------------------------------------------------------------

class TestTools:
    def test_read_file(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        # Create a test file
        test_file = wd / "hello.py"
        test_file.write_text("print('hello')")

        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_read_file({"path": "hello.py"}, ctx)
        assert result["ok"] is True
        assert "hello" in result["content"]

    def test_read_file_rejects_outside_sandbox(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_read_file({"path": "../../etc/passwd"}, ctx)
        assert result["ok"] is False
        assert "sandbox" in result["error"].lower()

    def test_read_file_missing(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_read_file({"path": "nonexistent.py"}, ctx)
        assert result["ok"] is False

    def test_list_dir(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "a.txt").write_text("a")
        (wd / "b.py").write_text("b")
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_list_dir({"path": "."}, ctx)
        assert result["ok"] is True
        names = [e["name"] for e in result["entries"]]
        assert "a.txt" in names
        assert "b.py" in names

    def test_grep(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "test.py").write_text("def foo():\n    pass\ndef bar():\n    pass")
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_grep({"pattern": "def \\w+", "include": "*.py"}, ctx)
        assert result["ok"] is True
        assert len(result["hits"]) >= 2

    def test_grep_rejects_bad_regex(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_grep({"pattern": "[invalid"}, ctx)
        assert result["ok"] is False
        assert "regex" in result["error"].lower()

    def test_run_cmd_allowlisted(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_run_cmd({"command": "python --version"}, ctx)
        assert result["ok"] is True

    def test_run_cmd_rejects_forbidden(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_run_cmd({"command": "rm -rf /"}, ctx)
        assert result["ok"] is False
        assert "not allowlisted" in result["error"]

    def test_write_file_refused_when_disabled(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_write_file({"path": "new.py", "content": "x=1"}, ctx)
        assert result["ok"] is False
        assert "disabled" in result["error"].lower()

    def test_write_file_works_when_enabled(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        ctx = {"root": str(wd), "allow_write": True, "conv_id": None}
        result = op.tool_write_file({"path": "new.py", "content": "x=1"}, ctx)
        assert result["ok"] is True
        assert (wd / "new.py").read_text() == "x=1"

    def test_edit_file_works_when_enabled(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "test.py").write_text("x = 1\nprint(x)")
        ctx = {"root": str(wd), "allow_write": True, "conv_id": None}
        result = op.tool_edit_file({"path": "test.py", "old": "x = 1", "new": "x = 2"}, ctx)
        assert result["ok"] is True
        assert "x = 2" in (wd / "test.py").read_text()

    def test_edit_file_refused_when_disabled(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "test.py").write_text("x = 1")
        ctx = {"root": str(wd), "allow_write": False, "conv_id": None}
        result = op.tool_edit_file({"path": "test.py", "old": "x = 1", "new": "x = 2"}, ctx)
        assert result["ok"] is False
        assert "disabled" in result["error"].lower()

    def test_edit_file_nonunique_rejection(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "test.py").write_text("x = 1\nx = 1")
        ctx = {"root": str(wd), "allow_write": True, "conv_id": None}
        result = op.tool_edit_file({"path": "test.py", "old": "x = 1", "new": "x = 2"}, ctx)
        assert result["ok"] is False
        assert "unique" in result["error"].lower()

    def test_redact_removes_secrets(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        monkeypatch.setenv("TEST_SECRET_TOKEN", "supersecret123")
        text = "Token is supersecret123 here"
        redacted = op.redact(text)
        assert "supersecret123" not in redacted
        assert "***" in redacted


# ---------------------------------------------------------------------------
# Operator store tests
# ---------------------------------------------------------------------------

class TestOperatorStore:
    def test_log_and_get_operator_run(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        rid = store.log_operator_run(
            conv_id="c_test",
            task="fix the bug",
            mode="operator",
            rounds=3,
            total_ms=1500.0,
            tools_used=["read_file", "grep"],
            sources=["src/main.py"],
            answer="Fixed the bug by changing line 5.",
            trace=[{"round": 1, "phase": "act"}],
        )
        assert rid.startswith("op_")

        runs = store.get_operator_runs()
        assert len(runs) == 1
        assert runs[0]["task"] == "fix the bug"
        assert runs[0]["tools_used"] == ["read_file", "grep"]

        run = store.get_operator_run(rid)
        assert run is not None
        assert run["trace"] == [{"round": 1, "phase": "act"}]


# ---------------------------------------------------------------------------
# MCP protocol tests (mocked stdio)
# ---------------------------------------------------------------------------

class TestMCPProtocol:
    def test_tools_list(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        import src.mcp_server as mcp
        importlib.reload(mcp)

        # Force re-init of context
        mcp._ctx.clear()
        mcp._ctx["root"] = str(wd)
        mcp._ctx["allow_write"] = False
        mcp._ctx["conv_id"] = None

        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        resp = mcp._dispatch(req)
        assert resp is not None
        tool_names = [t["name"] for t in resp["result"]["tools"]]
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "grep" in tool_names

    def test_tools_call_read_file(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "test.py").write_text("print('hello')")
        import src.mcp_server as mcp
        importlib.reload(mcp)
        mcp._ctx.clear()
        mcp._ctx["root"] = str(wd)
        mcp._ctx["allow_write"] = False
        mcp._ctx["conv_id"] = None

        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "read_file", "arguments": {"path": "test.py"}}}
        resp = mcp._dispatch(req)
        assert resp is not None
        content_text = resp["result"]["content"][0]["text"]
        result = json.loads(content_text)
        assert result["ok"] is True
        assert "hello" in result["content"]

    def test_initialize(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        import src.mcp_server as mcp
        importlib.reload(mcp)
        mcp._ctx.clear()

        req = {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}}
        resp = mcp._dispatch(req)
        assert resp is not None
        assert resp["result"]["serverInfo"]["name"] == "megaproject"

    def test_unknown_method(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        import src.mcp_server as mcp
        importlib.reload(mcp)

        req = {"jsonrpc": "2.0", "id": 4, "method": "foo/bar", "params": {}}
        resp = mcp._dispatch(req)
        assert resp is not None
        assert "error" in resp


# ---------------------------------------------------------------------------
# Operator API tests (mocked LLM — no real calls)
# ---------------------------------------------------------------------------

class TestOperatorAPI:
    def test_operator_readonly_mocked(self, tmp_path, monkeypatch):
        """Test the full operator flow with mocked LLM."""
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        (wd / "main.py").write_text("def add(a, b):\n    return a + b\n")

        from unittest.mock import MagicMock

        call_count = [0]

        def mock_call_llm_with_tools(messages, conv_id, round_num):
            call_count[0] += 1
            resp = MagicMock()
            msg = MagicMock()

            if call_count[0] == 1:
                tc = MagicMock()
                tc.id = "call_1"
                fn = MagicMock()
                fn.name = "read_file"
                fn.arguments = json.dumps({"path": "main.py"})
                tc.function = fn
                msg.tool_calls = [tc]
                msg.content = "Let me read the file."
            else:
                msg.tool_calls = []
                msg.content = "The file defines an add(a, b) function that returns a + b."

            resp.choices = [MagicMock(message=msg)]
            return resp

        # Reload first (fresh namespace), THEN set mock
        import src.operator as operator
        importlib.reload(operator)
        operator._call_llm_with_tools = mock_call_llm_with_tools

        from src.operator import run_operator
        result = run_operator(task="read main.py and describe it", allow_write=False)

        assert result["rounds"] >= 1
        assert "add" in result["answer"].lower()
        assert result["mode"] in ("operator", "operator_readonly")
        assert "read_file" in result["tools_used"]

    def test_operator_refuses_writes_when_disabled(self, tmp_path, monkeypatch):
        """Operator loop should not call write_file when allow_write=False."""
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)

        from unittest.mock import MagicMock

        def mock_call_llm_with_tools(messages, conv_id, round_num):
            resp = MagicMock()
            msg = MagicMock()
            tc = MagicMock()
            tc.id = "call_1"
            fn = MagicMock()
            fn.name = "write_file"
            fn.arguments = json.dumps({"path": "evil.py", "content": "import os; os.system('rm -rf /')"})
            tc.function = fn
            msg.tool_calls = [tc]
            msg.content = "I'll create a file."
            resp.choices = [MagicMock(message=msg)]
            return resp

        import src.operator as operator
        importlib.reload(operator)
        operator._call_llm_with_tools = mock_call_llm_with_tools

        from src.operator import run_operator
        result = run_operator(task="write a file", allow_write=False)

        # The tool should have been called but returned disabled error
        assert not (wd / "evil.py").exists()

    def test_api_tools_endpoint(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        import src.api as api
        importlib.reload(api)
        from fastapi.testclient import TestClient
        client = TestClient(api.app)

        r = client.get("/tools")
        assert r.status_code == 200
        tools = r.json()["tools"]
        assert len(tools) == 7  # 7 tools
        tool_names = [t["name"] for t in tools]
        assert "read_file" in tool_names
        assert "run_cmd" in tool_names

    def test_api_operator_runs_endpoint(self, tmp_path, monkeypatch):
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)
        import src.api as api
        importlib.reload(api)
        from fastapi.testclient import TestClient
        client = TestClient(api.app)

        r = client.get("/operator/runs")
        assert r.status_code == 200
        assert "runs" in r.json()


# ---------------------------------------------------------------------------
# Integration: operator + store + tools (no LLM)
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_readonly_pipeline(self, tmp_path, monkeypatch):
        """End-to-end: create workspace → operator reads → trace stored → dashboard returns."""
        config, store, rag, llm, ws, op, wd = _fresh(tmp_path, monkeypatch)

        # Create workspace content
        (wd / "src").mkdir()
        (wd / "src" / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        (wd / "tests").mkdir()
        (wd / "tests" / "test_app.py").write_text("from src.app import app\ndef test_app():\n    assert app is not None\n")
        (wd / "README.md").write_text("# My App\nA test application.\n")

        from unittest.mock import MagicMock

        def mock_call_llm_with_tools(messages, conv_id, round_num):
            resp = MagicMock()
            msg = MagicMock()

            if round_num == 1:
                tc = MagicMock()
                tc.id = "call_1"
                fn = MagicMock()
                fn.name = "list_dir"
                fn.arguments = json.dumps({"path": "."})
                tc.function = fn
                msg.tool_calls = [tc]
                msg.content = "Let me explore the workspace."
            elif round_num == 2:
                tc = MagicMock()
                tc.id = "call_2"
                fn = MagicMock()
                fn.name = "read_file"
                fn.arguments = json.dumps({"path": "src/app.py"})
                tc.function = fn
                msg.tool_calls = [tc]
                msg.content = "Found src/app.py, let me read it."
            else:
                msg.tool_calls = []
                msg.content = "The workspace contains a Flask app in src/app.py with tests in tests/test_app.py."

            resp.choices = [MagicMock(message=msg)]
            return resp

        # Reload first, THEN set mock
        import src.operator as operator
        importlib.reload(operator)
        operator._call_llm_with_tools = mock_call_llm_with_tools

        from src.operator import run_operator
        result = run_operator(task="explore the workspace", allow_write=False)

        assert result["rounds"] >= 2
        assert "Flask" in result["answer"]
        assert "list_dir" in result["tools_used"] or "read_file" in result["tools_used"]
        assert len(result["sources"]) >= 1

        # Store it
        rid = store.log_operator_run(
            conv_id="c_integration",
            task="explore the workspace",
            mode=result["mode"],
            rounds=result["rounds"],
            total_ms=result["total_ms"],
            tools_used=result["tools_used"],
            sources=result["sources"],
            answer=result["answer"],
            trace=result["trace"],
        )

        # Retrieve from dashboard
        run = store.get_operator_run(rid)
        assert run is not None
        assert run["answer"] == result["answer"]
        assert len(run["trace"]) >= 2
