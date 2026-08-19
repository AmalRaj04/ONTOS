"""M1 DoD (BUILD-SPEC.md §13): one document ingested (Tier 1), one claim extracted
(Tier 2), one LOOKUP question answered end-to-end with a real citation through the
full anchor -> plan -> traverse -> synthesize path.

Plain-Python script, not pytest (BUILD-SPEC.md §3 calls for plain Python over the
project's tooling; no pytest dependency exists). Hits the real Gemini/Groq APIs and
a running graph-node (`make hydradb-up`) — this is a manual/CI-smoke verification of
the walking skeleton, not a fast unit test. Run: `python -m tests.test_m1_walking_skeleton`.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from src.db.client import HydraClient
from src.ingest.adapters.confluence import ConfluenceAdapter
from src.ingest.tier1_structural import ingest_document
from src.ingest.tier2_semantic import process_document
from src.llm.router import LLMRouter
from src.query.pipeline import answer_question
from src.schema.ids import node_id


def main() -> None:
    client = HydraClient()
    router = LLMRouter()

    adapter = ConfluenceAdapter()
    target = None
    for doc in adapter.iter_documents(os.environ["CORPUS_DIR"]):
        if "Deploy / Upgrade / Roll Back perf-canary" in (doc.title or ""):
            target = doc
            break
    assert target is not None, "fixture document not found in corpus"

    tier1_summary = ingest_document(client, target)
    assert tier1_summary["chunk_count"] > 0, "Tier 1 wrote no chunks"
    print("Tier 1:", tier1_summary)

    chunk_id = node_id("chunk", target.doc_id, "0")
    tier2_summary = process_document(client, router, target, chunk_id)
    assert tier2_summary["claims_written"] > 0, "Tier 2 extracted no claims"
    print("Tier 2:", tier2_summary)

    result = answer_question(
        client, router, "m1_demo_lookup", "What does Vanessa Ortiz own or coordinate?"
    )
    assert result["abstained"] is False, "expected a direct answer, got abstention"
    assert result["citations"], "expected at least one citation"
    assert result["citations"][0]["doc_id"] == target.doc_id
    print("LOOKUP result:", result["answer"])
    print("Citation:", result["citations"][0])

    absent = answer_question(
        client, router, "m1_demo_absence", "Who is the CFO of Ganymede Robotics?"
    )
    assert absent["abstained"] is True, "expected abstention for an absent entity"
    print("Abstention result:", absent["traversal"]["path_summary"])

    client.close()
    print("M1 WALKING SKELETON: PASS")


if __name__ == "__main__":
    main()
