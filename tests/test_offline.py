"""Offline test suite — no network, no API key. Run: python -m pytest tests/ -q"""
import os
import sys
import importlib
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("MP_DB", str(tmp_path / "t.db"))
    monkeypatch.setenv("MP_CHROMA", str(tmp_path / "chroma"))
    monkeypatch.setenv("MP_DOCS", str(tmp_path / "docs"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    for m in ("src.config", "src.store", "src.rag", "src.llm", "src.agent", "src.api"):
        sys.modules.pop(m, None)
    import src.config as config
    importlib.reload(config)
    os.makedirs(config.DOCS_DIR, exist_ok=True)
    import src.store as store
    importlib.reload(store)
    import src.rag as rag
    importlib.reload(rag)
    import src.llm as llm
    importlib.reload(llm)
    import src.agent as agent
    importlib.reload(agent)
    return config, store, rag, llm, agent


def _write_docs(config, text="Refunds are processed within 14 days."):
    with open(os.path.join(config.DOCS_DIR, "doc.md"), "w") as f:
        f.write(text)


def test_rag_ingest_and_retrieve(tmp_path, monkeypatch):
    config, store, rag, llm, agent = _fresh(tmp_path, monkeypatch)
    _write_docs(config, "Refund policy: all purchases refundable within 14 days of charge.")
    n = rag.ingest_dir()
    assert n >= 1
    assert rag.knowledge_size() == n
    hits = rag.retrieve("how long do refunds take?")
    assert hits and "14 days" in hits[0]["text"]
    assert hits[0]["source"] == "doc.md"


def test_offline_agent_rag_only(tmp_path, monkeypatch):
    config, store, rag, llm, agent = _fresh(tmp_path, monkeypatch)
    _write_docs(config, "Refund policy: all purchases refundable within 14 days of charge.")
    rag.ingest_dir()
    store.init_db()
    cid = store.new_conversation("t")
    result = agent.run_agent("What is the refund window?", cid)
    assert result["mode"] == "rag_only"          # no key -> offline path
    assert "14 days" in result["answer"]
    assert result["sources"] == ["doc.md"]


def test_offline_empty_kb_friendly_message(tmp_path, monkeypatch):
    config, store, rag, llm, agent = _fresh(tmp_path, monkeypatch)
    store.init_db()
    result = agent.run_agent("hello?", store.new_conversation())
    assert result["mode"] == "rag_only"
    assert "offline" in result["answer"].lower()


def test_api_chat_offline_flow(tmp_path, monkeypatch):
    config, store, rag, llm, agent = _fresh(tmp_path, monkeypatch)
    _write_docs(config, "Late fee: 2% capped at 25 dollars per invoice.")
    import src.api as api
    importlib.reload(api)
    from fastapi.testclient import TestClient
    client = TestClient(api.app)  # startup auto-ingests DOCS_DIR

    r = client.post("/chat", json={"message": "what is the late fee cap?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "rag_only"
    assert "25" in body["answer"]
    assert body["conv_id"]

    r2 = client.get(f"/conversations/{body['conv_id']}/messages")
    assert r2.status_code == 200
    roles = [m["role"] for m in r2.json()]
    assert roles == ["user", "assistant"]

    h = client.get("/health").json()
    assert h["knowledge_chunks"] >= 1
    assert h["llm"]["online"] is False  # no key configured in tests


def test_breaker_trips_after_consecutive_failures(tmp_path, monkeypatch):
    config, store, rag, llm, agent = _fresh(tmp_path, monkeypatch)
    # fake a configured key so the call path is exercised, then make the
    # provider constructor blow up like a real network failure would
    monkeypatch.setenv("GROQ_API_KEY", "sk-fake")

    class _FakeProviderError(Exception):
        pass

    def _boom():
        raise _FakeProviderError("provider unreachable")

    monkeypatch.setattr(llm, "_client", _boom)

    import pytest
    with pytest.raises(llm.OfflineError):
        llm.llm_complete([{"role": "user", "content": "x"}])
    with pytest.raises(llm.OfflineError):
        llm.llm_complete([{"role": "user", "content": "x"}])
    # breaker now open -> immediate OfflineError, no provider contact
    with pytest.raises(llm.OfflineError, match="circuit breaker"):
        llm.llm_complete([{"role": "user", "content": "x"}])
    assert llm.llm_status()["online"] is False
