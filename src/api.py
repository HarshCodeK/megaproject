"""REST API for the AI Agent OS.

  POST /chat            {message, conv_id?}  -> agent turn (offline-safe)
  POST /operator        {task, conv_id?, allow_write?, max_rounds?} -> operator run
  GET  /operator/runs   list recent operator runs
  GET  /operator/runs/{id}  single run with full trace
  GET  /conversations   list
  GET  /conversations/{id}/messages
  POST /ingest          re-ingest knowledge_docs/ -> chunk count
  GET  /health          {llm_online, knowledge_chunks, messages, operator}
  GET  /tools           list available operator tools
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src import store, rag
from src.agent import run_agent
from src.llm import llm_status
from src.config import ALLOW_WRITE, ALLOW_OPERATOR_WRITE_VIA_API

app = FastAPI(title="MegaProject — AI Agent OS", version="0.2.0")


class ChatIn(BaseModel):
    message: str
    conv_id: str | None = None


class OperatorIn(BaseModel):
    task: str
    conv_id: str | None = None
    allow_write: bool | None = None
    max_rounds: int | None = None


@app.on_event("startup")
def _startup():
    store.init_db()
    if rag.knowledge_size() == 0:
        rag.ingest_dir()


@app.post("/chat")
def chat(body: ChatIn):
    store.init_db()
    conv_id = body.conv_id or store.new_conversation(body.message[:40])
    history = store.get_messages(conv_id)
    result = run_agent(body.message, conv_id, history)
    store.add_message(conv_id, "user", body.message, result["mode"])
    store.add_message(conv_id, "assistant", result["answer"], result["mode"])
    return {"conv_id": conv_id, **result}


@app.post("/operator")
def operator(body: OperatorIn):
    """Run a real tool-calling operator session."""
    store.init_db()
    conv_id = body.conv_id or store.new_conversation(body.task[:40])
    from src.operator import run_operator
    result = run_operator(
        task=body.task,
        conv_id=conv_id,
        allow_write=body.allow_write,
        max_rounds=body.max_rounds,
    )
    # Log to DB
    store.log_operator_run(
        conv_id=conv_id,
        task=body.task,
        mode=result["mode"],
        rounds=result["rounds"],
        total_ms=result["total_ms"],
        tools_used=result["tools_used"],
        sources=result["sources"],
        answer=result["answer"],
        trace=result["trace"],
    )
    return {"conv_id": conv_id, **result}


@app.get("/operator/runs")
def operator_runs():
    """Recent operator runs for the dashboard."""
    store.init_db()
    return {"runs": store.get_operator_runs(20)}


@app.get("/operator/runs/{run_id}")
def operator_run(run_id: str):
    """Single operator run with full trace."""
    store.init_db()
    run = store.get_operator_run(run_id)
    if run is None:
        raise HTTPException(404, f"run not found: {run_id}")
    return run


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


@app.get("/tools")
def tools():
    """List available operator tools with their capabilities."""
    from src.op_tools import TOOLS
    return {
        "tools": TOOLS,
        "write_enabled": ALLOW_WRITE,
        "write_via_api_allowed": ALLOW_OPERATOR_WRITE_VIA_API,
    }


@app.get("/health")
def health():
    return {
        "llm": llm_status(),
        "knowledge_chunks": rag.knowledge_size(),
        "operator": {
            "write_enabled": ALLOW_WRITE,
            "write_via_api": ALLOW_OPERATOR_WRITE_VIA_API,
        },
    }
