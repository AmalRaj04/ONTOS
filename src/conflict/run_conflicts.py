"""BUILD-SPEC.md §10/§13 M4 orchestrator: detect -> classify -> trust, over every
Claim in data/claims.jsonl (src/ingest/run_tier2.py's output). Persists every
ConflictSet with its rationale to HydraDB (so src/query/traverse.py's CONFLICT
path can do a direct read at query time, per §11 step 3) and to a local JSON
report for the M4 DoD's 30-hand-inspected-detections check and M6's error
analysis.

Usage: python -m src.conflict.run_conflicts
"""

import json
import os
import time

from dotenv import load_dotenv

from src.conflict.classify import classify_candidate
from src.conflict.detect import detect_conflict_candidates, load_alias_map, load_claims
from src.conflict.trust import adjudicate
from src.db.client import HydraClient
from src.ingest.writer import upsert_edges, upsert_nodes
from src.llm.router import LLMRouter
from src.schema.ids import hydra_id, opaque_id

REPORT_PATH = "data/conflicts_report.json"


def _persist_conflict_sets(client: HydraClient, resolved: list[dict]) -> dict:
    conflictset_nodes = []
    involves_edges = []
    for r in resolved:
        conflict_id = opaque_id("ConflictSet")
        claim_ids = [c["claim_id"] for c in r["claims"]]
        conflictset_nodes.append(
            {
                "vertex": hydra_id(conflict_id),
                "conflict_id": conflict_id,
                "subject": r["subject"],
                "predicate": r["predicate"],
                "resolution_status": r["resolution_status"],
                "winner": r["winner"] or "",
                "margin": r["margin"],
                "rationale": r["trust_rationale"],
                "claim_count": len(claim_ids),
            }
        )
        for claim_id in claim_ids:
            involves_edges.append(
                {
                    "from_vertex": hydra_id(conflict_id),
                    "to_vertex": hydra_id(claim_id),
                    "rel_vertex": hydra_id(f"involves:{conflict_id}:{claim_id}"),
                }
            )
    upsert_nodes(client, "ConflictSet", conflictset_nodes)
    upsert_edges(client, "ConflictSet", "Claim", "INVOLVES", involves_edges)
    return {"conflictset_count": len(conflictset_nodes), "involves_edge_count": len(involves_edges)}


def main() -> None:
    load_dotenv()
    t0 = time.monotonic()

    claims = load_claims()
    alias_map = load_alias_map()
    print(f"[conflict] {len(claims)} claims loaded, {len(alias_map)} alias-map entries", flush=True)

    candidates = detect_conflict_candidates(claims, alias_map)
    print(f"[conflict] {len(candidates)} detection candidates (shared subject+functional-predicate, "
          f">1 distinct object) ({time.monotonic()-t0:.0f}s)", flush=True)

    router = LLMRouter()
    classified = []
    for i, cand in enumerate(candidates):
        try:
            classified.append(classify_candidate(router, cand))
        except Exception as e:
            print(f"[conflict] classify failed for candidate {i}: {e}", flush=True)
            continue
        if (i + 1) % 25 == 0:
            print(f"[conflict] classified {i+1}/{len(candidates)} ({time.monotonic()-t0:.0f}s)", flush=True)

    by_class: dict[str, int] = {}
    for c in classified:
        by_class[c["classification"]] = by_class.get(c["classification"], 0) + 1
    print(f"[conflict] classification breakdown: {by_class}", flush=True)

    true_conflicts = [c for c in classified if c["classification"] == "CONTRADICTION"]
    resolved = [adjudicate(c) for c in true_conflicts]
    print(f"[conflict] {len(resolved)} true conflicts adjudicated ({time.monotonic()-t0:.0f}s)", flush=True)

    status_counts: dict[str, int] = {}
    cross_system_examples = []
    for r in resolved:
        status_counts[r["resolution_status"]] = status_counts.get(r["resolution_status"], 0) + 1
        sources = {c.get("source_system") for c in r["claims"]}
        if len(sources) > 1:
            cross_system_examples.append(r)
    print(f"[conflict] resolution status counts: {status_counts}", flush=True)
    print(f"[conflict] {len(cross_system_examples)} cross-system contradictions found", flush=True)

    client = HydraClient()
    write_summary = _persist_conflict_sets(client, resolved)
    client.close()
    print(f"[conflict] DONE writing: {write_summary}", flush=True)

    os.makedirs("data", exist_ok=True)
    report = {
        "claims_total": len(claims),
        "candidates": len(candidates),
        "classification_breakdown": by_class,
        "true_conflicts": len(resolved),
        "resolution_status_counts": status_counts,
        "cross_system_contradiction_count": len(cross_system_examples),
        "write_summary": write_summary,
        "elapsed_seconds": round(time.monotonic() - t0),
        "sample_for_hand_inspection": [
            {
                "subject": r["subject"],
                "predicate": r["predicate"],
                "classification": r["classification"],
                "resolution_status": r["resolution_status"],
                "winner": r["winner"],
                "margin": r["margin"],
                "rationale": r["trust_rationale"],
                "objects": [
                    {"object_literal": c.get("object_literal"), "object_id": c.get("object_id"),
                     "source_system": c.get("source_system"), "asserted_at": c.get("asserted_at")}
                    for c in r["claims"]
                ],
            }
            for r in resolved[:30]
        ],
        "cross_system_examples": [
            {
                "subject": r["subject"],
                "predicate": r["predicate"],
                "resolution_status": r["resolution_status"],
                "winner": r["winner"],
                "rationale": r["trust_rationale"],
            }
            for r in cross_system_examples[:10]
        ],
    }
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"[conflict] report -> {REPORT_PATH}", flush=True)


if __name__ == "__main__":
    main()
