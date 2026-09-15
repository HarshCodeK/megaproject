# megaproject

> **An AI agent operating system with a plugin architecture.**
> Five separate AI systems — a voice agent, a code generator, an RBAC chatbot, a log classifier, and a document assistant — unified into one agent OS with circuit-breaker degradation, offline fallback, and SQLite persistence.

![python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python&logoColor=white)
![fastapi](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![streamlit](https://img.shields.io/badge/Streamlit-1.38+-FF4B4B?logo=streamlit&logoColor=white)
![chromadb](https://img.shields.io/badge/ChromaDB-0.5+-purple)
![license](https://img.shields.io/badge/license-MIT-lightgrey)
![tier](https://img.shields.io/badge/tier-S-4CC38A)

---

## The story

I built 5 AI systems separately:

| System | What it did | Repo |
|--------|------------|------|
| **voice-agent-prototype** | Browser voice agent (STT → intent → tools → TTS) | archived |
| **multi-agent-coder** | 3-agent code generator (planner → architect → coder) | archived |
| **rag-rbac-chatbot** | Internal chatbot with role-based access at retrieval time | archived |
| **hybrid-log-classifier** | 3-tier log classification (regex → ML → LLM) | archived |
| **multimodal-financial-assistant** | Document understanding (vision + RAG + policy grounding) | archived |

Then I unified them. The agent loop from multi-agent-coder, the RAG grounding from rag-rbac-chatbot and multimodal-financial-assistant, the tiered degradation from hybrid-log-classifier, the gateway patterns from kay-kay — all sewn into one system with a plugin architecture. Inspired by DeepSeek's harness concept.

**The 5 repos are archived. The code lives here now.**

---

## How it works

```
User message
   │
   ▼
Agent runtime (plan → retrieve → synthesize)
   │             │
   │             └── RAG: ChromaDB + local embeddings (offline OK)
   │
   ▼
Groq LLM online? ──yes──▶ agent mode (grounded answer w/ citations)
   │
   no (circuit breaker)
   │
   ▼
RAG-only stitched answer (never crashes, always serves)
```

Three modes:
- **`agent`** — full online: plan → retrieve → LLM synthesis with citations
- **`agent_offline`** — LLM dropped mid-run → RAG-stitched fallback
- **`rag_only`** — no LLM at all → retrieve + stitch chunks

The system **never crashes**. It degrades gracefully and recovers automatically.

---

## The circuit breaker

```python
# After 2 consecutive LLM failures:
#   → breaker opens (zero latency on LLM calls)
#   → agent switches to RAG-only mode
#   → probe every 60s lets one call through
#   → success resets the breaker
```

Same philosophy as hybrid-log-classifier's tiering: never crash, fall back, keep serving.

---

## Architecture

```
megaproject/
├── src/
│   ├── agent.py      # agent runtime (plan → retrieve → synthesize)
│   ├── rag.py        # ChromaDB + sentence-transformers (local embeddings)
│   ├── llm.py        # Groq with circuit breaker
│   ├── store.py      # SQLite (conversations, messages, tool/LLM/RAG calls)
│   ├── config.py     # centralized config (paths, models, degradation policy)
│   └── api.py        # FastAPI REST API
├── app.py            # Streamlit chat UI
├── knowledge_docs/   # drop .txt/.md files here
└── tests/
    └── test_offline.py  # 5 offline tests (no network, no key)
```

---

## Quickstart

```bash
pip install -r requirements.txt

# optional — enables agent mode (without it, RAG-only mode works fine)
export GROQ_API_KEY=...

# UI
python -m streamlit run app.py

# REST API
python -m uvicorn src.api:app --reload
# open http://localhost:8000/docs
```

Drop documents into `knowledge_docs/` and they're ingested automatically on first boot.

---

## API

| Endpoint | Method | What |
|---|---|---|
| `/chat` | POST | One agent turn — `{message, conv_id?}` → answer + mode + sources |
| `/conversations` | GET | List conversations |
| `/conversations/{id}/messages` | GET | Full transcript |
| `/ingest` | POST | Re-chunk + re-embed `knowledge_docs/` |
| `/health` | GET | LLM online/offline status + KB size |

---

## Evidence

| Metric | Value | Reproduce |
|---|---|---|
| source modules | 6 | `ls src/` |
| tests | 5 offline | `python -m pytest tests/ -q` |
| degradation modes | 3 | agent / agent_offline / rag_only |
| circuit breaker probes | timed (60s) | `grep PROBE_INTERVAL src/config.py` |
| everything logged | ✓ | conversations, messages, tool/LLM/RAG calls in SQLite |

---

## What this is NOT

- No auth or multi-tenancy yet
- No streaming responses
- No plugin hot-loading (plugins are code modules, not dynamic)
- No deployment story yet (local only)

---

## License

MIT
