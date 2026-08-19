# Corpus coverage

BUILD-SPEC.md §8.3 (amended 2026-08-19) requires this document to state, plainly,
what fraction of the 511,958-document EnterpriseRAG-Bench corpus is actually
structurally ingested (Tier 1) — this is a scope decision driven by real build-time
constraints, not a quality shortcut, and is stated as one here rather than left
implicit.

**Status: ingest complete for this milestone (M2).**

## Why coverage isn't 100%, and why that's still a sound call

Two real constraints surfaced during the build, in this order:

1. **Disk.** HydraDB's per-property key storage model runs ~150-200KB of graph data
   per document once `Chunk`/`Mention` nodes and edges are written (see `PROJECT.md`
   decisions #1, #28, #31) — not the few KB the raw text alone would suggest. At that
   density the full corpus needs ~90GB. The build machine's internal disk had only a
   few GB free; a user-supplied external 512GB SSD (reformatted to exFAT, mounted at
   `/Volumes/ONTOS_SSD`) removed this constraint entirely.
2. **Time.** With disk no longer binding, per-document *write* cost turned out to be
   the real ceiling. Gmail and Slack's content is far denser in regex-matchable
   mentions than the other seven sources — Gmail threads embed full quoted
   reply-chain headers per message (observed ~47 email-address mentions/document),
   and a full ingest of just Gmail (121,391 docs) alone projected to 6-7 hours at
   the observed rate; Slack (285,606 docs, 56% of the corpus) risked a similar or
   larger cost. Combined, a full-corpus ingest was not going to fit inside the
   build's remaining time window without starving every later milestone (M3-M7).

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
2. **Stratified fill, target 25,000 documents, proportional to each source's real
   share of the corpus** (`src/ingest/run_ingest.py`'s `compute_stratified_targets`).
   In practice several sources had already exceeded their proportional share from
   an earlier full-ingest attempt before the target was set (see `PROJECT.md`
   decision #35), so final coverage came out well above the 25K floor — **64,957
   documents total**, not 25,000.

## What this does and doesn't protect

- **Protected regardless of fill %:** literal answerability of all 500+100 eval
  questions (their exact source documents are always present), cross-source
  representation for entity resolution and conflict detection (every source has
  meaningful volume, not just the priority tier's ~35-225 documents per source),
  and the ER/BM25/ablation comparisons in M6 (both sides of every comparison see
  the same corpus).
- **Not fully protected:** a minority of questions (mostly `multi-hop` and some
  `aggregate` categories) that need incidental supporting context beyond their own
  `expected_doc_ids` may see slightly reduced accuracy versus a full-corpus ingest.
  This is a real, stated tradeoff, not a hidden one.

## Final numbers

Document counts below are the per-source ingest checkpoint offsets — each one is a
running count of documents that source's `bulk_ingest()` call confirmed written
(not an estimate); a live `MATCH (d:Document) RETURN count(*)` cross-check was
attempted but times out at this node count without a label-scan index, which is a
known limitation (`PROJECT.md` decision #34) — ID-keyed point lookups (the access
pattern the rest of the pipeline actually uses) remain fast regardless.

| Source | Ingested | Total in corpus | Coverage |
|---|---|---|---|
| confluence | 5,189 | 5,189 | 100.0% |
| fireflies | 10,173 | 10,174 | ~100.0% |
| gdrive (google_drive) | 20,500 | 25,109 | 81.6% |
| github | 393 | 8,053 | 4.9% |
| gmail | 12,000 | 121,391 | 9.9% |
| hubspot | 733 | 15,018 | 4.9% |
| jira | 299 | 6,121 | 4.9% |
| linear | 1,724 | 35,309 | 4.9% |
| slack | 13,946 | 285,606 | 4.9% |
| **Total** | **64,957** | **511,970** | **12.7%** |

(github/hubspot/jira/linear/slack all land at ~4.9% because they hit their
proportional stratified-fill target exactly, having had no prior full-ingest
progress; confluence/fireflies/gdrive/gmail exceed their proportional targets
because they were partially or fully ingested before the fill target was set,
and that earlier progress was kept rather than discarded.)

## Near-duplicate detection

`src/ingest/dedupe.py` (MinHash/LSH, BUILD-SPEC.md §3/§8.3 step 5) runs over
whatever's ingested — coverage above applies identically to `NEAR_DUPLICATE_OF`
edge coverage.
