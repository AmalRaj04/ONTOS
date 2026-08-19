"""Full-corpus Tier 1 structural ingest — BUILD-SPEC.md §8.3/§13 M2. Resumable per
source via src/ingest/checkpoint.py; batched at INGEST_BATCH_SIZE (default 500) so
each round trip to HydraDB covers many documents, not one.

Usage: python -m src.ingest.run_ingest [source ...]   (omit sources to run all nine)
       INGEST_TARGET_TOTAL=25000 python -m src.ingest.run_ingest   (stratified fill)

BUILD-SPEC.md §8.3's "stratified fill" step, made concrete: the question-priority
tier (src/ingest/priority.py) already guarantees every document any eval question
needs is ingested regardless of what follows, so the fill's job is cross-source
diversity for entity resolution / conflict detection, not raw volume. With
INGEST_TARGET_TOTAL set, each source's target is proportional to its real share of
the 511,958-document corpus (SOURCE_TOTALS below); a source already past its target
(from an earlier full-ingest attempt or the priority tier) is left alone, not
trimmed back. Time, not disk, ended up being the binding constraint in practice —
Gmail and Slack's per-document mention density (quoted reply-chain headers, chat
@handles/#channels) made a full 511,958-document ingest impractical inside the
build's remaining time window even with disk to spare.
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

from src.db.client import HydraClient
from src.ingest.adapters import ALL_ADAPTERS
from src.ingest.checkpoint import Checkpoint
from src.ingest.dedupe import append_signature
from src.ingest.priority import load_priority_documents
from src.ingest.tier1_structural import bulk_ingest

SIGNATURES_PATH = "data/minhash_signatures.jsonl"
PRIORITY_DONE_MARKER = "data/checkpoints/priority.done"

# Real per-source document counts in the corpus (vendor/EnterpriseRAG-Bench/
# generated_data/sources/<name>/**/*.json, counted directly) — the denominator for
# proportional stratified-fill targets.
SOURCE_TOTALS = {
    "confluence": 5189,
    "fireflies": 10174,
    "gdrive": 25109,
    "github": 8053,
    "gmail": 121391,
    "hubspot": 15018,
    "jira": 6121,
    "linear": 35309,
    "slack": 285606,
}


def compute_stratified_targets(target_total: int) -> dict[str, int]:
    grand_total = sum(SOURCE_TOTALS.values())
    return {
        source: round(count / grand_total * target_total)
        for source, count in SOURCE_TOTALS.items()
    }


def run_priority_tier(client: HydraClient, corpus_dir: str) -> int:
    """BUILD-SPEC.md §8.3's ordering requirement: ingest documents needed by the
    500+100 eval questions first, guaranteed. Idempotent (MERGE) and cheap (~800
    docs), so this always runs — no need to checkpoint sub-progress, only whether
    the whole tier has completed at least once."""
    if os.path.exists(PRIORITY_DONE_MARKER):
        print("[priority] already done, skipping")
        return 0
    docs = load_priority_documents(corpus_dir)
    summary = bulk_ingest(client, docs)
    for d in docs:
        append_signature(SIGNATURES_PATH, d.doc_id, d.source_system, d.body)
    os.makedirs("data/checkpoints", exist_ok=True)
    with open(PRIORITY_DONE_MARKER, "w") as f:
        f.write(str(summary["doc_count"]))
    print(f"[priority] DONE docs={summary['doc_count']} chunks={summary['chunk_count']} "
          f"mentions={summary['mention_count']}")
    return summary["doc_count"]


def run_source(
    client: HydraClient,
    source_system: str,
    corpus_dir: str,
    batch_size: int,
    target: int | None = None,
) -> dict:
    """`target`: stop once `idx` reaches this many documents scanned (not just
    newly-written) for this source — a source already past its target from an
    earlier run is left alone (start_offset >= target short-circuits immediately)."""
    adapter_cls = ALL_ADAPTERS[source_system]
    adapter = adapter_cls()
    checkpoint = Checkpoint(source_system)
    start_offset = checkpoint.load()

    if target is not None and start_offset >= target:
        print(f"[{source_system}] already at/past stratified target ({start_offset}>={target}), skipping")
        return {"docs": 0, "chunks": 0, "mentions": 0, "final_offset": start_offset}

    idx = 0
    batch = []
    doc_count = 0
    chunk_count = 0
    mention_count = 0
    t0 = time.monotonic()

    for doc in adapter.iter_documents(corpus_dir):
        if target is not None and idx >= target:
            break
        if idx < start_offset:
            idx += 1
            continue
        batch.append(doc)
        idx += 1
        if len(batch) >= batch_size:
            summary = bulk_ingest(client, batch)
            for d in batch:
                append_signature(SIGNATURES_PATH, d.doc_id, d.source_system, d.body)
            doc_count += summary["doc_count"]
            chunk_count += summary["chunk_count"]
            mention_count += summary["mention_count"]
            checkpoint.save(idx)
            batch = []
            elapsed = time.monotonic() - t0
            print(
                f"[{source_system}] offset={idx} docs={doc_count} chunks={chunk_count} "
                f"mentions={mention_count} elapsed={elapsed:.0f}s",
                flush=True,
            )

    if batch:
        summary = bulk_ingest(client, batch)
        for d in batch:
            append_signature(SIGNATURES_PATH, d.doc_id, d.source_system, d.body)
        doc_count += summary["doc_count"]
        chunk_count += summary["chunk_count"]
        mention_count += summary["mention_count"]
        checkpoint.save(idx)

    print(f"[{source_system}] DONE offset={idx} docs={doc_count} chunks={chunk_count} mentions={mention_count}")
    return {"docs": doc_count, "chunks": chunk_count, "mentions": mention_count, "final_offset": idx}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("sources", nargs="*", default=list(ALL_ADAPTERS.keys()))
    args = parser.parse_args()

    corpus_dir = os.environ["CORPUS_DIR"]
    batch_size = int(os.environ.get("INGEST_BATCH_SIZE", 500))
    target_total = os.environ.get("INGEST_TARGET_TOTAL")
    targets = compute_stratified_targets(int(target_total)) if target_total else None
    if targets:
        print(f"[stratified fill] target_total={target_total} per-source={targets}")

    client = HydraClient()
    run_priority_tier(client, corpus_dir)

    totals = {"docs": 0, "chunks": 0, "mentions": 0}
    for source in args.sources:
        if source not in ALL_ADAPTERS:
            print(f"unknown source: {source}", file=sys.stderr)
            continue
        try:
            result = run_source(client, source, corpus_dir, batch_size, targets.get(source) if targets else None)
        except Exception as e:
            # A source-level failure (retries in src/db/client.py exhausted) must
            # not kill an hours-long multi-source run — the per-source checkpoint
            # already reflects real progress; log and move on, don't lose the rest.
            print(f"[{source}] FAILED after retries: {e}", file=sys.stderr, flush=True)
            continue
        totals["docs"] += result["docs"]
        totals["chunks"] += result["chunks"]
        totals["mentions"] += result["mentions"]
    client.close()
    print(f"TOTAL: {totals}")


if __name__ == "__main__":
    main()
