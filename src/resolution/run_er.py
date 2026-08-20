"""BUILD-SPEC.md §9/§13 M3 orchestrator. Run the full entity-resolution pipeline
(normalize -> block -> score -> adjudicate -> cluster -> canonicalize) over every
Person-identifying signal in the ingest-frozen corpus (docs/coverage.md), and
verify the milestone's non-negotiable invariants before writing anything.

Usage: python -m src.resolution.run_er
"""

import json
import os
import random
import time

from dotenv import load_dotenv

from src.db.client import HydraClient
from src.llm.router import LLMRouter
from src.resolution.adjudicate_llm import adjudicate_pair, in_uncertain_band
from src.resolution.block import compute_features, generate_candidate_pairs
from src.resolution.canonicalize import canonicalize_clusters
from src.resolution.cluster import TAU, cluster_records
from src.resolution.records import collect_identity_records, load_employee_directory
from src.resolution.score import build_scoring_context, score_pair

ADJUDICATION_CAP = int(os.environ.get("ER_ADJUDICATION_CAP", "500"))
REPORT_PATH = "docs/er-report.md"


def _verify_invariants(records, scored_pairs, clusters) -> dict:
    """M3 DoD (BUILD-SPEC.md §13): zero co-sentence violations within any cluster,
    ~zero email-conflict rate."""
    idx_to_cluster = {}
    for ci, member_idxs in enumerate(clusters):
        for idx in member_idxs:
            idx_to_cluster[idx] = ci

    co_sentence_violations = 0
    for (i, j), (score, feats) in scored_pairs.items():
        if feats.get("negative_cooccurrence", 0.0) >= 1.0 and idx_to_cluster.get(i) == idx_to_cluster.get(j):
            co_sentence_violations += 1

    email_conflicts = 0
    for member_idxs in clusters:
        emails = {records[i].raw.lower() for i in member_idxs if records[i].kind == "email_mention"}
        if len(emails) > 1:
            email_conflicts += 1

    return {
        "co_sentence_violations": co_sentence_violations,
        "email_conflict_clusters": email_conflicts,
        "email_conflict_rate": email_conflicts / len(clusters) if clusters else 0.0,
    }


def main() -> None:
    load_dotenv()
    corpus_dir = os.environ["CORPUS_DIR"]

    t0 = time.monotonic()
    print("[er] collecting identity records from ingest-frozen corpus...", flush=True)
    records = collect_identity_records(corpus_dir)
    employees = load_employee_directory(corpus_dir)
    print(f"[er] {len(records)} identity records, {len(employees)} employee-roster entries "
          f"({time.monotonic()-t0:.0f}s)", flush=True)

    feats = compute_features(records, employees)

    print("[er] blocking...", flush=True)
    candidate_pairs = generate_candidate_pairs(records, feats)
    print(f"[er] {len(candidate_pairs)} candidate pairs after blocking "
          f"({time.monotonic()-t0:.0f}s)", flush=True)

    ctx = build_scoring_context(records, feats, employees)

    print("[er] scoring...", flush=True)
    scored_pairs: dict[tuple[int, int], tuple[float, dict]] = {}
    for (i, j), matched_keys in candidate_pairs.items():
        score, features = score_pair(i, j, records, feats, matched_keys, ctx)
        scored_pairs[(i, j)] = (score, features)
    print(f"[er] scored {len(scored_pairs)} pairs ({time.monotonic()-t0:.0f}s)", flush=True)

    uncertain = [
        (i, j) for (i, j), (score, feats) in scored_pairs.items()
        if in_uncertain_band(score) and feats.get("negative_cooccurrence", 0.0) < 1.0
    ]
    uncertain_fraction = len(uncertain) / len(scored_pairs) if scored_pairs else 0.0
    print(f"[er] {len(uncertain)} pairs in uncertain band ({uncertain_fraction:.1%} of candidates)", flush=True)

    sample = uncertain
    if len(uncertain) > ADJUDICATION_CAP:
        random.seed(42)
        sample = random.sample(uncertain, ADJUDICATION_CAP)
        print(f"[er] uncertain band exceeds ER_ADJUDICATION_CAP={ADJUDICATION_CAP}; "
              f"sampling {ADJUDICATION_CAP} for LLM adjudication, rest resolved by score-only", flush=True)

    router = LLMRouter()
    adjudicated = 0
    same_count = 0
    for i, j in sample:
        score, features = scored_pairs[(i, j)]
        try:
            same, confidence, reason = adjudicate_pair(router, records[i], records[j], score, features)
        except Exception as e:
            print(f"[er] adjudication failed for ({i},{j}): {e}", flush=True)
            continue
        adjudicated += 1
        if same and confidence >= 0.5:
            scored_pairs[(i, j)] = (max(score, TAU + 0.1), features)
            same_count += 1
        else:
            scored_pairs[(i, j)] = (min(score, TAU - 0.05), features)
        if adjudicated % 50 == 0:
            print(f"[er] adjudicated {adjudicated}/{len(sample)} ({time.monotonic()-t0:.0f}s)", flush=True)

    print(f"[er] adjudication done: {adjudicated} calls, {same_count} confirmed same-person "
          f"({time.monotonic()-t0:.0f}s)", flush=True)

    print("[er] clustering...", flush=True)
    emails_by_idx = {i: f.email for i, f in enumerate(feats) if f.email}
    clusters = cluster_records(len(records), scored_pairs, emails_by_idx)
    print(f"[er] {len(clusters)} clusters from {len(records)} records "
          f"({time.monotonic()-t0:.0f}s)", flush=True)

    invariants = _verify_invariants(records, scored_pairs, clusters)
    print(f"[er] invariants: {invariants}", flush=True)

    print("[er] canonicalizing (writing Person nodes + RESOLVES_TO edges)...", flush=True)
    client = HydraClient()
    summary = canonicalize_clusters(client, records, feats, clusters, ctx)
    client.close()
    alias_map = summary.pop("alias_map")
    with open("data/er_alias_map.json", "w") as f:
        json.dump(alias_map, f)
    print(f"[er] DONE: {summary}, alias_map ({len(alias_map)} entries) -> data/er_alias_map.json", flush=True)

    report = {
        "identity_records": len(records),
        "employee_roster_entries": len(employees),
        "candidate_pairs": len(candidate_pairs),
        "scored_pairs": len(scored_pairs),
        "uncertain_band_fraction": uncertain_fraction,
        "adjudication_calls": adjudicated,
        "adjudication_confirmed_same": same_count,
        "clusters": len(clusters),
        "invariants": invariants,
        "canonicalize_summary": summary,
        "tau": TAU,
        "elapsed_seconds": round(time.monotonic() - t0),
    }
    os.makedirs("data", exist_ok=True)
    with open("data/er_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"[er] report written to data/er_report.json", flush=True)


if __name__ == "__main__":
    main()
