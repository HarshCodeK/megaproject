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

# Operator (real repo work: read/grep/run/edit behind a sandbox)
# Workspace the operator may touch. Defaults to this repo; override per deploy.
WORKSPACE = os.environ.get("MP_WORKSPACE", ROOT)
# Writes are OFF unless explicitly enabled. The API additionally requires
# MP_ALLOW_OPERATOR_WRITE=1 before it will even accept allow_write=true.
ALLOW_WRITE = os.environ.get("MP_ALLOW_WRITE", "0") == "1"
ALLOW_OPERATOR_WRITE_VIA_API = os.environ.get("MP_ALLOW_OPERATOR_WRITE", "0") == "1"
MAX_OPERATOR_ROUNDS = int(os.environ.get("MP_MAX_ROUNDS", "10"))
CMD_TIMEOUT_S = int(os.environ.get("MP_CMD_TIMEOUT_S", "30"))
MAX_READ_BYTES = 200_000
MAX_TOOL_OUTPUT_CHARS = 20_000

# RAG
CHUNK_WORDS = 100
TOP_K = 3
