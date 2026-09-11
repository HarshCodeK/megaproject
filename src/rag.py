"""RAG engine — ChromaDB + local sentence-transformer embeddings.

Works fully offline (models cached locally). Ingest text/markdown files,
retrieve top-k chunks for a query. Same chunking as
multimodal-financial-assistant's knowledge_base.py.
"""
import os
import json

import chromadb
from sentence_transformers import SentenceTransformer

from src.config import CHROMA_PATH, EMBEDDING_MODEL, DOCS_DIR, CHUNK_WORDS, TOP_K
from src import store

_embedder = None
_collection = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def _get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        _collection = client.get_or_create_collection("knowledge")
    return _collection


def _chunk_text(text: str) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, len(words), CHUNK_WORDS)]


def ingest_dir(directory: str = None) -> int:
    """Chunk + embed every .txt/.md in a directory. Returns chunks stored."""
    directory = directory or DOCS_DIR
    os.makedirs(directory, exist_ok=True)
    model, coll = _get_embedder(), _get_collection()

    chunks, ids, metas = [], [], []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith((".txt", ".md")):
            continue
        text = open(os.path.join(directory, fname), encoding="utf-8", errors="ignore").read()
        for i, chunk in enumerate(_chunk_text(text)):
            chunks.append(chunk)
            ids.append(f"{fname}::{i}")
            metas.append({"source": fname, "chunk": i})

    if not chunks:
        return 0
    # Re-ingest fresh each time: ids are deterministic so this is an upsert,
    # but delete first to drop chunks from removed/edited files.
    for cid in ids:
        try:
            coll.delete(ids=[cid])
        except Exception:
            pass
    coll.add(embeddings=model.encode(chunks).tolist(), documents=chunks, ids=ids, metadatas=metas)
    return len(chunks)


def retrieve(query: str, conv_id: str = None, k: int = TOP_K) -> list[dict]:
    """Top-k chunks with sources; every retrieval is logged."""
    coll, model = _get_collection(), _get_embedder()
    if coll.count() == 0:
        return []
    res = coll.query(query_embeddings=model.encode([query]).tolist(), n_results=min(k, coll.count()))
    out = []
    for doc, meta in zip(res["documents"][0], res["metadatas"][0]):
        out.append({"text": doc, "source": meta["source"]})
    if conv_id:
        store.log_rag_call(conv_id, query[:200], len(out), json.dumps([o["source"] for o in out]))
    return out


def knowledge_size() -> int:
    try:
        return _get_collection().count()
    except Exception:
        return 0
