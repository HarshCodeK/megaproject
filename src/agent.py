"""Agent runtime — a planner/worker loop with tools, generalized from
multi-agent-coder's planner->architect->coder pipeline.

The agent has exactly two tools, kept deliberately small:
  - rag_search(query): retrieve knowledge chunks
  - llm_think(prompt): one reasoning step (online only)

Loop: plan -> act (tool calls) -> synthesize. Offline, the loop compresses
to retrieve -> stitch (RAG-only mode) without ever crashing.
"""
import json
import time

from src import rag, store
from src.llm import llm_complete, OfflineError, llm_status
from src.config import MAX_AGENT_STEPS

TOOLS = [
    {"name": "rag_search", "description": "Search the knowledge base for relevant chunks. Args: {\"query\": str}"},
    {"name": "llm_think", "description": "One reasoning step about gathered context. Args: {\"prompt\": str}"},
]


def run_agent(user_message: str, conv_id: str, history: list = None) -> dict:
    """Run one agent turn. Returns {answer, mode, steps, sources}."""
    history = history or []
    steps = []

    # --- lazy first-boot ingest (startup hook doesn't fire under bare TestClient) ---
    if rag.knowledge_size() == 0:
        rag.ingest_dir()

    # --- offline fast path: RAG-only synthesis -----------------------------
    status = llm_status()
    if not status["online"] or not status["key_configured"]:
        return _rag_only_answer(user_message, conv_id, steps)

    # --- online: plan ------------------------------------------------------
    plan_messages = [
        {"role": "system", "content": (
            "You are an agent planner. Given the user's message, decide if the knowledge "
            "base should be searched first. Reply with ONLY a JSON object: "
            '{"search": true/false, "query": "<rag query or empty>"}. '
            "Search when the question could be answered by documents; skip for chit-chat.")},
        {"role": "user", "content": user_message},
    ]
    try:
        plan_raw = llm_complete(plan_messages, purpose="plan", conv_id=conv_id)
        plan = _safe_json(plan_raw, {"search": True, "query": user_message})
        steps.append({"step": "plan", "detail": plan})
    except OfflineError:
        return _rag_only_answer(user_message, conv_id, steps)

    # --- act: optional RAG retrieval ---------------------------------------
    sources, context = [], ""
    if plan.get("search", True):
        query = plan.get("query") or user_message
        t0 = time.time()
        chunks = rag.retrieve(query, conv_id=conv_id)
        store.log_tool_call(conv_id, "rag_search", {"query": query}, f"{len(chunks)} chunks", (time.time() - t0) * 1000)
        sources = [c["source"] for c in chunks]
        context = "\n\n---\n\n".join(f"[{c['source']}]\n{c['text']}" for c in chunks)
        steps.append({"step": "rag_search", "detail": {"query": query, "chunks": len(chunks)}})

    # --- synthesize ---------------------------------------------------------
    sys = (
        "You are a helpful assistant with access to a knowledge base. "
        "Answer the user using the CONTEXT when it is relevant; cite sources like [file.txt]. "
        "If context is empty or irrelevant, answer from general knowledge and say so."
    )
    msgs = [{"role": "system", "content": sys}]
    for h in history[-6:]:
        msgs.append({"role": h[0], "content": h[1]})
    user_block = f"CONTEXT:\n{context}\n\nUSER: {user_message}" if context else user_message
    msgs.append({"role": "user", "content": user_block})

    try:
        answer = llm_complete(msgs, purpose="synthesize", conv_id=conv_id)
        mode = "agent"
    except OfflineError:
        answer = _stitch(chunks, user_message)
        mode = "agent_offline"
        steps.append({"step": "offline_fallback", "detail": "LLM dropped mid-run; RAG-stitched answer"})

    return {"answer": answer, "mode": mode, "steps": steps, "sources": sources}


def _rag_only_answer(user_message: str, conv_id: str, steps: list) -> dict:
    chunks = rag.retrieve(user_message, conv_id=conv_id)
    steps.append({"step": "rag_only", "detail": {"chunks": len(chunks)}})
    return {"answer": _stitch(chunks, user_message), "mode": "rag_only", "steps": steps,
            "sources": [c["source"] for c in chunks]}


def _stitch(chunks: list, question: str) -> str:
    if not chunks:
        return ("I'm currently offline and the knowledge base has nothing relevant. "
                "Add documents to knowledge_docs/ or try again when the connection returns.")
    out = [f"Offline mode — answering from the local knowledge base ({len(chunks)} chunk(s)):\n"]
    for c in chunks:
        out.append(f"From [{c['source']}]:\n{c['text']}\n")
    return "\n".join(out)


def _safe_json(raw: str, default: dict) -> dict:
    try:
        return json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        return default
