"""Streamlit front end.

Run:  streamlit run app.py
"""
import streamlit as st

from src import agent as agent_mod
from src import llm, rag
from src.tools import SPECS

ACCENT, INK, MUTED, RULE = "#1F7A5C", "#12181F", "#5A6673", "#DCE3E8"

st.set_page_config(page_title="Sandboxed Agent", layout="wide", page_icon="◈")
st.markdown(f"""
<style>
  .block-container {{ padding-top:2.2rem; max-width:1060px; }}
  h1 {{ letter-spacing:-.02em; color:{INK}; font-size:1.6rem; margin-bottom:.1rem; }}
  .sub {{ color:{MUTED}; font-size:.9rem; margin-bottom:1.4rem; }}
  .rule {{ height:1px; background:{RULE}; border:0; margin:1.3rem 0; }}
  .tool {{ font-size:.78rem; border-left:2px solid {RULE}; padding-left:.7rem;
           margin:.3rem 0; }}
  .stButton>button[kind="primary"] {{ background:{ACCENT}; border-color:{ACCENT}; }}
  .mono {{ font-family:ui-monospace,Menlo,Consolas,monospace; font-size:.78rem; }}
</style>""", unsafe_allow_html=True)

st.title("Sandboxed coding agent")
st.markdown('<div class="sub">The agent reads, searches and runs read-only commands '
            "inside a workspace sandbox. It is never trusted: every path is resolved "
            "and checked before any I/O, and writes stay off unless you enable them.</div>",
            unsafe_allow_html=True)

status = llm.status()
with st.sidebar:
    st.markdown("#### Status")
    st.markdown(f"- **model:** `{status['model']}`")
    st.markdown(f"- **key configured:** {status['key_configured']}")
    st.markdown(f"- **online:** {status['online']}")
    if status["last_error"]:
        # The whole point of tracking the reason: a retired model and a network
        # outage both say "offline" otherwise, and they need opposite fixes.
        st.error(f"last failure: {status['last_error'][:120]}")
    st.markdown('<hr class="rule">', unsafe_allow_html=True)
    st.markdown("#### Tools")
    for s in SPECS:
        st.markdown(f'<div class="tool"><strong>{s["function"]["name"]}</strong><br>'
                    f'<span class="mono">{s["function"]["description"][:90]}</span></div>',
                    unsafe_allow_html=True)

task = st.text_area("Task", value="Read src/config.py and tell me which LLM model this "
                                  "project defaults to. Use the tools rather than guessing.",
                    height=110, label_visibility="collapsed")
allow_write = st.checkbox("Allow writes", value=False,
                          help="Off by default. Enabling lets the agent create and edit "
                               "files inside the sandbox.")

if st.button("Run agent", type="primary"):
    with st.spinner("Working..."):
        st.session_state["r"] = agent_mod.run(task, allow_write=allow_write)

r = st.session_state.get("r")
if r:
    st.markdown("#### Answer")
    st.write(r["answer"])
    st.markdown(f'<div class="sub">mode <code>{r["mode"]}</code> · {r["rounds"]} rounds · '
                f'{r["total_ms"]} ms · tools: {", ".join(r["tools_used"]) or "none"}</div>',
                unsafe_allow_html=True)
    if r["trace"]:
        st.markdown("#### Trace")
        for step in r["trace"]:
            if step["phase"] == "act":
                for tc in step["tool_calls"]:
                    st.markdown(f'<div class="tool">round {step["round"]} '
                                f'<strong>{tc["tool"]}</strong> '
                                f'<span class="mono">{str(tc["args"])[:110]}</span></div>',
                                unsafe_allow_html=True)
                    st.code(tc["result"][:300], language="json")
            else:
                st.markdown(f'<div class="tool">round {step["round"]} '
                            f'<strong>{step["phase"]}</strong> — {step.get("detail","")}</div>',
                            unsafe_allow_html=True)
