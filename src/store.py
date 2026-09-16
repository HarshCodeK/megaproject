"""SQLite layer: conversations, agent runs, and every LLM/RAG call logged.

Same pattern as hybrid-log-classifier's monitor.py, extended for a chat OS.
"""
import json
import sqlite3
import datetime
import uuid

from src.config import DB_PATH


def _conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            conv_id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS messages (
            msg_id TEXT PRIMARY KEY,
            conv_id TEXT,
            role TEXT,
            content TEXT,
            mode TEXT,               -- 'agent' | 'rag_only' | 'agent_offline'
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tool_calls (
            call_id TEXT PRIMARY KEY,
            conv_id TEXT,
            tool TEXT,
            args TEXT,
            result TEXT,
            latency_ms REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS llm_calls (
            call_id TEXT PRIMARY KEY,
            conv_id TEXT,
            purpose TEXT,            -- 'plan' | 'respond' | 'synthesize'
            model TEXT,
            status TEXT,             -- 'ok' | 'offline' | 'error'
            latency_ms REAL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            error TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS rag_calls (
            call_id TEXT PRIMARY KEY,
            conv_id TEXT,
            query TEXT,
            n_results INTEGER,
            top_sources TEXT,       -- json list of chunk sources
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS operator_runs (
            run_id TEXT PRIMARY KEY,
            conv_id TEXT,
            task TEXT,
            mode TEXT,               -- 'operator' | 'operator_offline' | 'operator_readonly'
            rounds INTEGER,
            total_ms REAL,
            tools_used TEXT,         -- json list of tool names
            sources TEXT,            -- json list of file paths
            answer TEXT,
            trace TEXT,              -- full trace JSON
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)


def new_conversation(title="New conversation") -> str:
    cid = "c_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO conversations (conv_id, title) VALUES (?, ?)", (cid, title))
    return cid


def add_message(conv_id: str, role: str, content: str, mode: str = "agent"):
    mid = "m_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO messages (msg_id, conv_id, role, content, mode) VALUES (?,?,?,?,?)",
                  (mid, conv_id, role, content, mode))
    return mid


def get_messages(conv_id: str, limit=50):
    with _conn() as c:
        # rowid is monotonic; created_at has second granularity and ties break order
        rows = c.execute("SELECT role, content, mode, created_at FROM messages WHERE conv_id=? ORDER BY rowid DESC LIMIT ?",
                         (conv_id, limit)).fetchall()
    return list(reversed(rows))


def log_tool_call(conv_id, tool, args, result, latency_ms):
    tid = "t_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO tool_calls (call_id, conv_id, tool, args, result, latency_ms) VALUES (?,?,?,?,?,?)",
                  (tid, conv_id, tool, str(args), str(result), latency_ms))
    return tid


def log_llm_call(conv_id, purpose, model, status, latency_ms,
                 prompt_tokens=0, completion_tokens=0, error=None):
    init_db()  # idempotent: callers may hit the DB before any init ran
    cid = "l_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("""INSERT INTO llm_calls (call_id, conv_id, purpose, model, status, latency_ms,
                    prompt_tokens, completion_tokens, error) VALUES (?,?,?,?,?,?,?,?,?)""",
                  (cid, conv_id, purpose, model, status, latency_ms, prompt_tokens, completion_tokens, error))
    return cid


def log_rag_call(conv_id, query, n_results, top_sources):
    init_db()  # idempotent
    cid = "g_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("INSERT INTO rag_calls (call_id, conv_id, query, n_results, top_sources) VALUES (?,?,?,?,?)",
                  (cid, conv_id, query, n_results, top_sources))
    return cid


def log_operator_run(conv_id, task, mode, rounds, total_ms, tools_used, sources, answer, trace):
    """Persist a completed operator run for the dashboard."""
    init_db()
    rid = "op_" + uuid.uuid4().hex[:10]
    with _conn() as c:
        c.execute("""INSERT INTO operator_runs
                     (run_id, conv_id, task, mode, rounds, total_ms, tools_used, sources, answer, trace)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (rid, conv_id, task[:500], mode, rounds, total_ms,
                   json.dumps(tools_used), json.dumps(sources),
                   answer[:2000], json.dumps(trace)))
    return rid


def get_operator_runs(limit=20):
    """Recent operator runs for the dashboard."""
    with _conn() as c:
        rows = c.execute(
            "SELECT run_id, conv_id, task, mode, rounds, total_ms, tools_used, sources, answer, created_at "
            "FROM operator_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    runs = []
    for r in rows:
        runs.append({
            "run_id": r[0], "conv_id": r[1], "task": r[2], "mode": r[3],
            "rounds": r[4], "total_ms": r[5],
            "tools_used": json.loads(r[6]) if r[6] else [],
            "sources": json.loads(r[7]) if r[7] else [],
            "answer": r[8], "created_at": r[9],
        })
    return runs


def get_operator_run(run_id: str):
    """Single operator run with full trace."""
    with _conn() as c:
        row = c.execute(
            "SELECT run_id, conv_id, task, mode, rounds, total_ms, tools_used, sources, answer, trace, created_at "
            "FROM operator_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    if row is None:
        return None
    return {
        "run_id": row[0], "conv_id": row[1], "task": row[2], "mode": row[3],
        "rounds": row[4], "total_ms": row[5],
        "tools_used": json.loads(row[6]) if row[6] else [],
        "sources": json.loads(row[7]) if row[7] else [],
        "answer": row[8], "trace": json.loads(row[9]) if row[9] else [],
        "created_at": row[10],
    }


def get_recent_llm_stats(limit=50):
    with _conn() as c:
        rows = c.execute("SELECT purpose, status, COUNT(*) FROM llm_calls GROUP BY purpose, status").fetchall()
        msgs = c.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        op_runs = c.execute("SELECT COUNT(*) FROM operator_runs").fetchone()[0]
        op_tool_calls = c.execute("SELECT COUNT(*) FROM tool_calls WHERE tool NOT LIKE 'rag_%'").fetchone()[0]
    return {
        "llm_calls_by_status": [list(r) for r in rows],
        "total_messages": msgs,
        "operator_runs": op_runs,
        "operator_tool_calls": op_tool_calls,
    }
