# MegaProject — AI Agent OS

**An AI agent operating system**: agent runtime + RAG knowledge, with a
smooth degradation path to **RAG-only answers when offline**. SaaS-style UI
(Streamlit chat) + REST API (FastAPI) + SQLite session/telemetry store.

A consolidation of the right pieces from my existing projects — agent loop
([multi-agent-coder](https://github.com/HarshCodeK/multi-agent-coder)), RAG
grounding ([multimodal-financial-assistant](https://github.com/HarshCodeK/multimodal-financial-assistant),
[rag-rbac-chatbot](https://github.com/HarshCodeK/rag-rbac-chatbot)),
confidence-tiered degradation ([hybrid-log-classifier](https://github.com/HarshCodeK/hybrid-log-classifier)),
gateway/API + logging ([kay-kay](https://github.com/HarshCodeK/kay-kay)).

## What it does

- **Agent runtime** — planner decides whether to search knowledge, retrieves
  RAG context, synthesizes a cited answer. Steps and tool calls are logged.
- **Offline degradation** — a circuit breaker trips after repeated LLM
  failures; the runtime switches to RAG-only stitched answers and keeps
  serving. When the provider returns, agent mode resumes automatically.
- **Knowledge base** — drop `.txt`/`.md` files into `knowledge_docs/`;
  they're chunked, embedded locally (sentence-transformers), and stored in
  ChromaDB. Re-ingest anytime via API or UI.
- **Everything logged** — conversations, messages, tool calls, LLM calls
  (with latency/tokens), RAG retrievals (with sources) in SQLite.

```
User message
   |
   v
Agent runtime (plan -> retrieve -> synthesize)
   |             |
   |             +-- RAG: ChromaDB + local embeddings (offline OK)
   |
   v
Groq LLM online? --yes--> agent mode (grounded answer w/ citations)
   |
   no (circuit breaker)
   |
   v
RAG-only stitched answer (never crashes, always serves)
```

## Quickstart

```bash
git clone https://github.com/HarshCodeK/megaproject.git
cd megaproject
pip install -r requirements.txt

# optional (enables agent mode; without it, RAG-only mode works fine)
export GROQ_API_KEY=...        # or set it in .env

python -m streamlit run app.py       # UI
python -m uvicorn src.api:app --reload   # REST API (http://localhost:8000/docs)
```

Drop documents into `knowledge_docs/` (a sample `policies.md` ships with it)
and they're ingested automatically on first boot.

## API

| Endpoint | What |
|---|---|
| `POST /chat` | one agent turn — `{message, conv_id?}` → answer + mode + sources |
| `GET /conversations` | list conversations |
| `GET /conversations/{id}/messages` | transcript |
| `POST /ingest` | re-chunk + re-embed `knowledge_docs/` |
| `GET /health` | LLM online/offline status + KB size |

## Tests (offline, no key needed)

```bash
python -m pytest tests/ -q
```

Covers: ingest → retrieve, offline agent turn (RAG-only), empty-KB message,
full API chat flow, and the circuit breaker tripping on consecutive LLM
failures.

## Roadmap

- Role-based access on knowledge docs (from rag-rbac-chatbot)
- Multi-step tool workflows beyond rag_search
- Deployed SaaS hosting + auth
