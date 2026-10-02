"""FastAPI service.

Run:  uvicorn src.api:app --reload
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src import agent as agent_mod
from src import llm, rag, workspace
from src.tools import SPECS

app = FastAPI(title="MegaProject — Sandboxed Agent", version="1.0.0")


class Task(BaseModel):
    task: str = Field(..., min_length=1, max_length=8000)
    model: str | None = None
    max_rounds: int | None = Field(None, ge=1, le=20)
    allow_write: bool | None = None


@app.on_event("startup")
def _startup():
    try:
        rag.ensure_index()
    except Exception:
        pass    # the agent still works with tools even if the index is missing


@app.get("/health")
def health():
    """Health, with the reason the last LLM failure happened.

    `key_configured` and `online` are separate on purpose: a project whose model
    id has been retired looks identical to one that is merely offline unless the
    reason is reported.
    """
    return {
        "llm": llm.status(),
        "knowledge_chunks": rag.size(),
        "workspace": workspace.root_path(),
        "writes_enabled": agent_mod.ALLOW_WRITE,
    }


@app.get("/tools")
def tools():
    """Every tool the agent has, and whether writes are on."""
    return {
        "tools": [{"name": s["function"]["name"],
                   "description": s["function"]["description"]} for s in SPECS],
        "writes_enabled": agent_mod.ALLOW_WRITE,
    }


@app.post("/agent")
def run_agent(body: Task):
    """One agent turn. Returns the answer plus the full trace."""
    return agent_mod.run(
        task=body.task,
        model_id=body.model,
        max_rounds=body.max_rounds or agent_mod.MAX_ROUNDS,
        allow_write=body.allow_write,
    )


@app.post("/ingest")
def ingest():
    return {"chunks": rag.ingest()}
