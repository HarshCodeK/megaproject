"""Retrieval over ChromaDB with local embeddings.

Embeddings rather than keyword matching, for the usual reason: the question is
"what is our refund policy" and the document says "purchases may be returned
within fourteen days". Those share almost no words.

The embedding model is local (22M parameters, CPU, free), so retrieval works
offline and costs nothing. That is what lets the system answer with no API key.
"""
import os

from src.config import CHROMA_DIR, CHUNK_WORDS, DOCS_DIR, EMBEDDING_MODEL, TOP_K

COLLECTION = "knowledge"

_embedder = None
_client = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def _get_collection():
    global _client
    if _client is None:
        import chromadb
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client.get_or_create_collection(COLLECTION)


def chunk(text: str):
    words = text.split()
    return [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, len(words), CHUNK_WORDS)]


def ingest(directory: str = None) -> int:
    """Embed every .txt/.md in the directory. Deterministic ids make it an upsert."""
    directory = directory or DOCS_DIR
    if not os.path.isdir(directory):
        raise FileNotFoundError(f"knowledge directory not found: {directory}")

    chunks, ids, metadatas = [], [], []
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".txt", ".md")):
            continue
        with open(os.path.join(directory, name), encoding="utf-8", errors="ignore") as f:
            for i, piece in enumerate(chunk(f.read().strip())):
                if piece:
                    chunks.append(piece)
                    ids.append(f"{name}::{i}")
                    metadatas.append({"source": name, "chunk": i})
    if not chunks:
        raise RuntimeError(f"no .txt or .md documents in {directory}")

    coll = _get_collection()
    # Delete first so chunks from edited or removed files actually disappear.
    for cid in ids:
        try:
            coll.delete(ids=[cid])
        except Exception:
            pass
    coll.add(embeddings=_get_embedder().encode(chunks).tolist(),
             documents=chunks, ids=ids, metadatas=metadatas)
    return len(chunks)


def size() -> int:
    try:
        return _get_collection().count()
    except Exception:
        return 0


def ensure_index() -> int:
    return size() or ingest()


def search(query: str, k: int = TOP_K):
    """Top-k chunks with their source file."""
    coll = _get_collection()
    if coll.count() == 0:
        ingest()
        coll = _get_collection()
    if coll.count() == 0:
        return []
    res = coll.query(query_embeddings=_get_embedder().encode([query]).tolist(),
                     n_results=min(k, coll.count()))
    return [{"text": d, "source": m.get("source", "unknown")}
            for d, m in zip(res["documents"][0], res["metadatas"][0])]
