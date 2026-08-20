# Corpus coverage

BUILD-SPEC.md §8.3 (amended 2026-08-19) requires this document to state, plainly,
what fraction of the 511,970-document EnterpriseRAG-Bench corpus is actually
structurally ingested (Tier 1) — this is a scope decision driven by real build-time
and build-schedule constraints, not a quality shortcut, and is stated as one here
rather than left implicit.

**Status: ingest frozen for this submission (2026-08-20, 244,822 docs). See
"Second pass and freeze" below for why this supersedes the original 64,957-doc M2
number.**

## Why coverage isn't 100%, and why that's still a sound call

Three real constraints surfaced during the build, in this order:

1. **Disk.** HydraDB's per-property key storage model runs ~150-200KB of graph data
   per document once `Chunk`/`Mention` nodes and edges are written (see `PROJECT.md`
   decisions #1, #28, #31) — not the few KB the raw text alone would suggest. At that
   density the full corpus needs ~90GB. The build machine's internal disk had only a
   few GB free; a user-supplied external 512GB SSD (reformatted to exFAT, mounted at
   `/Volumes/ONTOS_SSD`) removed this constraint.
2. **Time.** With disk no longer binding, per-document *write* cost turned out to be
   the real ceiling, especially for Gmail (dense in regex-matchable mentions — full
   quoted reply-chain headers per message) and Slack (285,606 docs, 56% of the
   corpus). A full-corpus ingest would starve every later milestone (M3-M7).
3. **Wall-clock schedule.** During a push to 250,000 documents (50% of the corpus,
   proportional across all 9 sources), the build machine hit three compounding
   infrastructure failures — an SSD disconnect that broke Docker's bind mount, a
   Docker Desktop virtiofs bug that misreported the SSD's free space, and a memory
   cache misconfiguration (16GB `GRAPH_DATA_CACHE_BYTES` on an 8GB-RAM machine) that
   caused severe swap thrashing and repeated silent process death (`PROJECT.md`
   decisions #31, #33, #34, #37). Diagnosing and fixing these consumed a large block
   of the build's remaining time. With the deadline close, ingest was frozen at
   244,822 documents rather than spending further hours finishing the last ~21K
   Gmail documents — every other source had already reached or exceeded its
   proportional 250K-push target.

Given that, the ingest order in BUILD-SPEC.md §8.3 was followed with a bounded
"stratified fill" target rather than an exhaustive one:

## Ingest order

1. **Question-priority tier — 812 documents, ingested first, unconditionally.**
   Every document referenced by `expected_doc_ids` across all 500 official + 100
   extra eval questions (looked up directly via EnterpriseRAG-Bench's own
   `generated_data/uuid_index.json`, not by resolving question entities against
   already-ingested data — see `src/ingest/priority.py`). This means every
   question's core answerability is protected regardless of the fill percentage
   below: a question's own required document is never a coverage gap.
2. **Stratified fill, proportional to each source's real share of the corpus**
   (`src/ingest/run_ingest.py`'s `compute_stratified_targets`), run twice: once
   with a 25,000-document floor (M2, 2026-08-19), then scaled up to a
   250,000-document floor (2026-08-20) once external SSD storage removed the disk
   constraint. Final ingest was frozen mid-way through the second pass.

## What this does and doesn't protect

- **Protected regardless of fill %:** literal answerability of all 500+100 eval
  questions (their exact source documents are always present), cross-source
  representation for entity resolution and conflict detection (every source has
  substantial volume — the smallest, gmail, still has 38,000 documents), and the
  ER/BM25/ablation comparisons in M6 (both sides of every comparison see the same
  corpus).
- **Not fully protected:** a minority of questions (mostly `multi-hop` and some
  `aggregate` categories) that need incidental supporting context beyond their own
  `expected_doc_ids` may see slightly reduced accuracy versus a full-corpus ingest.
  Gmail specifically sits at 64% of its own 250K-push proportional target (31% of
  its full 121,391-doc share) rather than 100% like the other eight sources. This
  is a real, stated tradeoff, not a hidden one.

## Final numbers (frozen 2026-08-20)

Document counts below are the per-source ingest checkpoint offsets — each one is a
running count of documents that source's `bulk_ingest()` call confirmed written
(not an estimate).

| Source | Ingested | 250K stratified target | % of target | Total in corpus | % of corpus |
|---|---|---|---|---|---|
| confluence | 5,189 | 2,534 | 204.8% | 5,189 | 100.0% |
| fireflies | 10,173 | 4,968 | 204.8% | 10,174 | ~100.0% |
| gdrive (google_drive) | 20,500 | 12,261 | 167.2% | 25,109 | 81.6% |
| github | 3,932 | 3,932 | 100.0% | 8,053 | 48.8% |
| gmail | 38,000 | 59,276 | 64.1% | 121,391 | 31.3% |
| hubspot | 7,333 | 7,333 | 100.0% | 15,018 | 48.8% |
| jira | 2,989 | 2,989 | 100.0% | 6,121 | 48.8% |
| linear | 17,242 | 17,242 | 100.0% | 35,309 | 48.8% |
| slack | 139,464 | 139,464 | 100.0% | 285,606 | 48.8% |
| **Total** | **244,822** | **250,000** | **97.9%** | **511,970** | **47.8%** |

8 of 9 sources reached or exceeded their proportional share of the 250,000-document
target; only gmail fell short (frozen at 38,000/59,276 when the build's remaining
time had to shift to M3-M7).

## Second pass and freeze

The original M2 milestone (2026-08-19) completed at 64,957 documents (12.7% of the
corpus) under a 25,000-document stratified-fill floor. With the external SSD
removing the disk constraint, a second ingest pass targeted 50% of the corpus
(250,000 documents, chosen after explicit cost/benefit discussion — see
`PROJECT.md` decision #35 for the "why 250K, not 100%" reasoning) proportionally
across all 9 sources. That pass ran into the infrastructure failures described
above; once resolved, ingest resumed and completed for 7 of 8 remaining sources,
with gmail the sole source still short of target when the build's schedule
required freezing ingest to leave adequate time for M3-M7 and demo preparation.
The **244,822-document corpus above is final** for this submission.

## Near-duplicate detection

`src/ingest/dedupe.py` (MinHash/LSH, BUILD-SPEC.md §3/§8.3 step 5) runs over
whatever's ingested. It was run once at the 64,957-document scale (M2, see
`PROJECT.md` for the 254 confirmed near-duplicate pairs found then) and is
re-run over the full 244,822-document corpus as part of M3, whose entity
resolution corroboration logic depends on `NEAR_DUPLICATE_OF` edges being
current.
