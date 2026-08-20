"""Tier 2 bulk orchestrator — BUILD-SPEC.md §8.4 steps 1-3, deferred from M1 (see
tier2_semantic.py's docstring: "the selection logic in §8.4's steps 1-3 —
stratified sampling, question-neighbourhood pull, lazy enrichment — is M2/M5
work"). Runs LLM claim extraction over a bounded, representative subset of the
ingest-frozen corpus (docs/coverage.md), not the full 244,822 documents — Tier 2 is
LLM-cost/time-bound, not disk-bound (§8.4: "a few thousand documents... if
TIER2_SAMPLE_FRACTION pushes past that, lower it"). This is what gives M4 (conflict
detection) and M6's CONFLICT/AGGREGATE eval questions actual Claim nodes to work
with — M2 only ran Tier 1 (structural) at full scale.

Subset = question-priority tier (guaranteed, 812 docs — protects every eval
question's own required document) + a stratified sample proportional to each
source's real share of the *ingested* corpus, sized by TIER2_TARGET_TOTAL.

Usage: python -m src.ingest.run_tier2
       TIER2_TARGET_TOTAL=1500 python -m src.ingest.run_tier2
"""

import argparse
import os
import random
import sys
import time

from dotenv import load_dotenv

from src.db.client import HydraClient
from src.ingest.adapters import ALL_ADAPTERS
from src.ingest.checkpoint import Checkpoint
from src.ingest.priority import load_priority_documents
from src.ingest.tier1_structural import chunk_body
from src.ingest.tier2_semantic import process_document
from src.llm.router import LLMRouter
from src.schema.ids import node_id

DONE_MARKER_DIR = "data/checkpoints/tier2_done"


def _done_marker(source: str) -> str:
    return os.path.join(DONE_MARKER_DIR, f"{source}.ids")


def _load_done(source: str) -> set[str]:
    path = _done_marker(source)
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.strip() for line in f if line.strip()}


def _mark_done(source: str, doc_id: str) -> None:
    os.makedirs(DONE_MARKER_DIR, exist_ok=True)
    with open(_done_marker(source), "a") as f:
        f.write(doc_id + "\n")


def _process_one(client: HydraClient, router: LLMRouter, doc) -> dict:
    chunks = chunk_body(doc.body)
    for c in chunks:
        c["chunk_id"] = node_id("chunk", doc.doc_id, str(c["ordinal"]))
    fallback_chunk_id = chunks[0]["chunk_id"] if chunks else node_id("chunk", doc.doc_id, "0")
    return process_document(client, router, doc, fallback_chunk_id, chunks=chunks)


def _reservoir_sample_source(corpus_dir: str, source: str, adapter_cls, offset: int, want: int) -> list:
    """Reservoir-samples `want` docs from the first `offset` yielded by this
    source's adapter, without materializing the full offset-sized pool in memory
    first — offset can be >100K for slack/gmail while `want` is a few hundred, so
    building the whole pool just to random.sample() it would cost real time and
    real memory on a build machine that already hit an OOM incident once
    (PROJECT.md decision #37)."""
    if offset <= 0 or want <= 0:
        return []
    rng = random.Random(hash(source) & 0xFFFFFFFF)
    adapter = adapter_cls()
    reservoir: list = []
    for idx, doc in enumerate(adapter.iter_documents(corpus_dir)):
        if idx >= offset:
            break
        if len(reservoir) < want:
            reservoir.append(doc)
        else:
            j = rng.randint(0, idx)
            if j < want:
                reservoir[j] = doc
    return reservoir


def _stratified_sample_docs(corpus_dir: str, target_total: int) -> dict[str, list]:
    """Sample proportionally from each source's *ingested* prefix (checkpoint
    offset), not the full source — Tier 2 must never reach past what Tier 1
    actually wrote (no Chunk/Mention nodes would exist to attach claims to).
    Sources are read concurrently (one thread each) for the same reason
    src/resolution/records.py's collect_identity_records does: `iter_records()`
    eagerly lists+sorts every file in a source's directory before yielding
    anything, which dominates wall time at ~500K total corpus files far more
    than any per-document work does."""
    from concurrent.futures import ThreadPoolExecutor

    ingested_counts = {s: Checkpoint(s).load() for s in ALL_ADAPTERS}
    total_ingested = sum(ingested_counts.values())
    per_source_target = {
        s: round(target_total * n / total_ingested) if total_ingested else 0
        for s, n in ingested_counts.items()
    }

    sampled: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = {
            source: pool.submit(
                _reservoir_sample_source, corpus_dir, source, adapter_cls,
                ingested_counts[source], per_source_target[source],
            )
            for source, adapter_cls in ALL_ADAPTERS.items()
        }
        for source, future in futures.items():
            sampled[source] = future.result()
            print(f"[tier2:sample] {source}: {len(sampled[source])} sampled "
                  f"(of {ingested_counts[source]} ingested, target {per_source_target[source]})", flush=True)
    return sampled


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", default=list(ALL_ADAPTERS.keys()))
    args = parser.parse_args()

    corpus_dir = os.environ["CORPUS_DIR"]
    target_total = int(os.environ.get("TIER2_TARGET_TOTAL", "1500"))

    client = HydraClient()
    router = LLMRouter()

    t0 = time.monotonic()
    print(f"[tier2] priority tier (guaranteed)...", flush=True)
    priority_docs = load_priority_documents(corpus_dir)
    priority_by_source: dict[str, list] = {}
    for d in priority_docs:
        priority_by_source.setdefault(d.source_system, []).append(d)

    print(f"[tier2] stratified sample, target_total={target_total}...", flush=True)
    sample_by_source = _stratified_sample_docs(corpus_dir, target_total)

    totals = {"claims_written": 0, "claims_dropped": 0, "docs_processed": 0, "docs_skipped": 0}
    for source in args.sources:
        if source not in ALL_ADAPTERS:
            print(f"unknown source: {source}", file=sys.stderr)
            continue
        done = _load_done(source)
        docs_by_id = {d.doc_id: d for d in priority_by_source.get(source, [])}
        for d in sample_by_source.get(source, []):
            docs_by_id.setdefault(d.doc_id, d)
        docs = list(docs_by_id.values())
        print(f"[tier2:{source}] {len(docs)} candidate docs ({len(done)} already done)", flush=True)

        for i, doc in enumerate(docs):
            if doc.doc_id in done:
                totals["docs_skipped"] += 1
                continue
            try:
                result = _process_one(client, router, doc)
            except Exception as e:
                print(f"[tier2:{source}] FAILED doc {doc.doc_id[:24]}: {e}", file=sys.stderr, flush=True)
                continue
            _mark_done(source, doc.doc_id)
            totals["claims_written"] += result["claims_written"]
            totals["claims_dropped"] += result["claims_dropped"]
            totals["docs_processed"] += 1
            if totals["docs_processed"] % 25 == 0:
                elapsed = time.monotonic() - t0
                print(
                    f"[tier2] processed={totals['docs_processed']} "
                    f"claims_written={totals['claims_written']} "
                    f"claims_dropped={totals['claims_dropped']} elapsed={elapsed:.0f}s",
                    flush=True,
                )

    client.close()
    print(f"[tier2] DONE: {totals} elapsed={time.monotonic()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
