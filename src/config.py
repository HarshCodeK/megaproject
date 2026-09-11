"""MegaProject — AI Agent OS configuration.

One place for paths, model names, and the offline-degradation policy.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("MP_DB", os.path.join(ROOT, "megaproject.db"))
CHROMA_PATH = os.environ.get("MP_CHROMA", os.path.join(ROOT, "chroma_db"))
DOCS_DIR = os.environ.get("MP_DOCS", os.path.join(ROOT, "knowledge_docs"))

# LLM (online tier)
LLM_PROVIDER = "groq"
LLM_MODEL = os.environ.get("MP_LLM_MODEL", "llama-3.3-70b-versatile")
LLM_TIMEOUT_S = 20

# Embeddings (offline-capable: local sentence-transformer)
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Offline policy: how many LLM failures before the session stops retrying
# and serves RAG-only answers until a probe succeeds again.
LLM_MAX_CONSECUTIVE_FAILURES = 2
LLM_PROBE_INTERVAL_S = 60

# Agent runtime
MAX_AGENT_STEPS = 8  # planner loops capped so agents can't run away

# RAG
CHUNK_WORDS = 100
TOP_K = 3
