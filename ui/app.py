"""BUILD-SPEC.md §13 M7 / planning doc 05 §8 — four-element demo page: traversal
path, conflict banner, alias line, citations. Five preloaded demo questions
(lookup, multi-hop, conflict, alias-dependent, absent-answer) plus a free-text box
for judges to try their own.

Run: streamlit run ui/app.py
"""

import json
import os

import streamlit as st
from dotenv import load_dotenv

from src.db.client import HydraClient
from src.llm.router import LLMRouter
from src.query.pipeline import answer_question

load_dotenv()

st.set_page_config(page_title="ONTOS", layout="wide")

DEMO_QUESTIONS = {
    "Lookup — direct fact": "What is the status of the hot-route capacity protection rollout in us-east?",
    "Multi-hop — relationship path": "Who does the manager of the person who owns the Smart Routing project report to?",
    "Conflict — contradictory claims": "What is the current status of the applied-ml-platform project, and are there any conflicting reports about it?",
    "Alias-dependent — cross-source identity": "What has Ava Chen been working on across Slack, email, and Jira?",
    "Absent answer — should abstain": "What is the exact per-route-group budget configured for each enterprise account on the initial allowlist for the hot-route capacity rollout?",
}


@st.cache_resource
def get_clients():
    client = HydraClient()
    router = LLMRouter()
    return client, router


def render_result(question_id: str, question: str, result: dict) -> None:
    st.subheader("Answer")
    if result["abstained"]:
        st.warning(f"**Abstained.** {result['traversal']['path_summary']}")
    else:
        st.success(result["answer"])
        st.caption(f"Confidence: {result['confidence']}")

    # 1. Traversal path
    with st.expander("Traversal path", expanded=True):
        t = result["traversal"]
        st.write(f"**Anchors:** {', '.join(t['anchors']) or '(none resolved)'}")
        st.write(f"**Path count:** {t['path_count']}  |  **Max hops:** {t['max_hops']}")
        st.write(t["path_summary"])

    # 2. Conflict banner
    if result["conflicts"]:
        for c in result["conflicts"]:
            banner = st.error if c["status"] == "CONTESTED" else st.info
            banner(
                f"**Conflict ({c['status']})** — winner: `{c['winner'] or 'none (contested/abstained)'}`, "
                f"margin: {c['margin']:.2f}\n\n{c['rationale']}"
            )

    # 3. Alias line
    aliases = _alias_line(result["traversal"]["anchors"])
    if aliases:
        st.caption(f"**Known aliases:** {aliases}")

    # 4. Citations
    if result["citations"]:
        st.write("**Citations:**")
        for c in result["citations"]:
            st.markdown(
                f"- `{c['source_system']}` **{c['title'] or c['doc_id']}** "
                f"(doc `{c['native_id']}`, chunk `{c['chunk_id']}`, span {c['quote_span']})"
            )
    else:
        st.caption("No citations (abstained or no supporting claims).")

    with st.expander("Raw graph_stats"):
        st.json(result["graph_stats"])


@st.cache_data
def _load_alias_map() -> dict[str, str]:
    path = "data/er_alias_map.json"
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def _alias_line(anchors: list[str]) -> str:
    alias_map = _load_alias_map()
    reverse: dict[str, list[str]] = {}
    for alias, cid in alias_map.items():
        reverse.setdefault(cid, []).append(alias)
    lines = []
    for a in anchors:
        cid = alias_map.get(a)
        if cid and len(reverse.get(cid, [])) > 1:
            others = [x for x in reverse[cid] if x != a][:5]
            lines.append(f"{a} = {', '.join(others)}")
    return "; ".join(lines)


st.title("ONTOS")
st.caption("An enterprise ontology on HydraDB — entity resolution, contradiction adjudication, and correct abstention.")

client, router = get_clients()

col1, col2 = st.columns([1, 2])
with col1:
    choice = st.radio("Demo questions", list(DEMO_QUESTIONS.keys()) + ["Custom..."])
with col2:
    if choice == "Custom...":
        question = st.text_area("Your question", height=100)
    else:
        question = DEMO_QUESTIONS[choice]
        st.text_area("Question", value=question, height=100, disabled=True)

if st.button("Ask", type="primary") and question:
    with st.spinner("Traversing the graph..."):
        result = answer_question(client, router, "ui-demo", question, consistency="strong")
    render_result("ui-demo", question, result)

st.divider()
st.caption(
    "Recovery: this page hits the same live HydraDB/query pipeline used for evaluation — "
    "kill and restart graph-node, ask the same question again, and the answer + citations "
    "are identical (no local cache)."
)
