"""REST API for the AI Agent OS.

  POST /chat            {message, conv_id?}  -> agent turn (offline-safe)
  GET  /conversations   list
  GET  /conversations/{id}/messages
  POST /ingest          re-ingest knowledge_docs/ -> chunk count
  GET  /health          {llm_online, knowledge_chunks, messages}
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src import store, rag
from src.agent import run_agent
from src.llm import llm_status

app = FastAPI(title="MegaProject — AI Agent OS", version="0.1.0")


class ChatIn(BaseModel):
    message: str
    conv_id: str | None = None


@app.on_event("startup")
def _startup():
    store.init_db()
    if rag.knowledge_size() == 0:
        rag.ingest_dir()  # auto-ingest on first boot so demo works instantly


@app.post("/chat")
def chat(body: ChatIn):
    store.init_db()
    conv_id = body.conv_id or store.new_conversation(body.message[:40])
    history = store.get_messages(conv_id)
    result = run_agent(body.message, conv_id, history)
    store.add_message(conv_id, "user", body.message, result["mode"])
    store.add_message(conv_id, "assistant", result["answer"], result["mode"])
    return {"conv_id": conv_id, **result}


@app.get("/conversations")
def conversations():
    store.init_db()
    with store._conn() as c:
        rows = c.execute("SELECT conv_id, title, created_at FROM conversations ORDER BY created_at DESC LIMIT 50").fetchall()
    return [{"conv_id": r[0], "title": r[1], "created_at": r[2]} for r in rows]


@app.get("/conversations/{conv_id}/messages")
def messages(conv_id: str):
    store.init_db()
    return [{"role": m[0], "content": m[1], "mode": m[2], "at": m[3]} for m in store.get_messages(conv_id, 200)]


@app.post("/ingest")
def ingest():
    n = rag.ingest_dir()
    return {"chunks_stored": n}


@app.get("/health")
def health():
    return {"llm": llm_status(), "knowledge_chunks": rag.knowledge_size()}
