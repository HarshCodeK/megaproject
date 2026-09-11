"""MegaProject UI — SaaS-style chat over the AI Agent OS.

Run: streamlit run app.py
The API can run separately (src/api.py); the UI talks to the runtime directly.
"""
import streamlit as st

from src import store, rag
from src.agent import run_agent
from src.llm import llm_status

st.set_page_config(page_title="MegaProject — AI Agent OS", layout="wide")
store.init_db()

# ---- sidebar: status + knowledge -------------------------------------------
with st.sidebar:
    st.title("MegaProject")
    st.caption("AI Agent OS · RAG-integrated · offline-capable")

    status = llm_status()
    box = st.container()
    if status["online"] and status["key_configured"]:
        box.success(f"LLM online — {status['model']}")
    else:
        box.warning("Offline mode — RAG-only answers")

    st.metric("Knowledge chunks", rag.knowledge_size())

    if st.button("Re-ingest knowledge_docs/"):
        n = rag.ingest_dir()
        st.toast(f"Ingested {n} chunks")

    st.divider()
    if st.button("+ New conversation"):
        st.session_state.pop("conv_id", None)
        st.rerun()

    with st.expander("Recent conversations"):
        with store._conn() as c:
            rows = c.execute("SELECT conv_id, title FROM conversations ORDER BY created_at DESC LIMIT 8").fetchall()
        for cid, title in rows:
            label = (title or "Untitled")[:30]
            if st.button(label, key=f"cv_{cid}", use_container_width=True):
                st.session_state["conv_id"] = cid
                st.rerun()

# ---- main chat --------------------------------------------------------------
st.title("💬 Agent Chat")
if "conv_id" not in st.session_state:
    st.session_state["conv_id"] = store.new_conversation()
conv_id = st.session_state["conv_id"]

for role, content, mode, _ in store.get_messages(conv_id, 50):
    with st.chat_message(role):
        st.markdown(content)
        if role == "assistant" and mode and mode != "agent":
            st.caption(f"mode: {mode}")

prompt = st.chat_input("Ask anything — try 'what does the refund policy say?'")
if prompt:
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            result = run_agent(prompt, conv_id, store.get_messages(conv_id))
        store.add_message(conv_id, "user", prompt, result["mode"])
        store.add_message(conv_id, "assistant", result["answer"], result["mode"])
        st.markdown(result["answer"])
        if result["mode"] != "agent":
            st.caption(f"mode: {result['mode']}")
        if result.get("sources"):
            with st.expander("Sources"):
                for s in result["sources"]:
                    st.text(s)
    st.rerun()
