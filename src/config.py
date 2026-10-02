"""Paths, model, and the offline policy."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.models import DEFAULT_MODEL, MODELS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dir(env_var: str, default_name: str) -> str:
    """Absolutise a directory path and create it.

    Why: chromadb.PersistentClient and sqlite3 both fail with "unable to open
    database file" when the parent directory does not exist, and neither
    expands `~`. Creating it here turns a confusing failure into a first-run
    success.
    """
    raw = os.environ.get(env_var) or os.path.join(ROOT, default_name)
    resolved = os.path.abspath(os.path.expanduser(raw.strip()))
    os.makedirs(resolved, exist_ok=True)
    return resolved


DB_PATH = _dir("MP_DB", "megaproject.db")
CHROMA_DIR = _dir("MP_CHROMA", "chroma_db")
DOCS_DIR = os.environ.get("MP_DOCS") or os.path.join(ROOT, "knowledge_docs")

# Default model. The previous version of this project pinned a model the
# provider had already retired, so every call 404'd -- and because the circuit
# breaker reported that as "offline", it looked like the degradation path was
# working rather than the online tier being dead.
LLM_MODEL = os.environ.get("MP_MODEL", DEFAULT_MODEL)

# Failures before the breaker opens. Two, so one transient timeout does not take
# the system offline.
MAX_FAILURES = 2
PROBE_INTERVAL_S = 60

# The agent loop is capped so a confused model cannot spin forever.
MAX_ROUNDS = 8

# Writes are off unless explicitly enabled. The operator is a reader by default.
ALLOW_WRITE = os.environ.get("MP_ALLOW_WRITE", "0") == "1"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHUNK_WORDS = 100
TOP_K = 3
