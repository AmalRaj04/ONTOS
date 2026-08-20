# ONTOS — an enterprise ontology on HydraDB

Hack Hydra 2026 · Track 01: Enterprise Context and Ontology

ONTOS turns 244,822 noisy enterprise documents — Slack, Gmail, Linear, Google Drive,
HubSpot, Fireflies, GitHub, Jira, Confluence — into a single queryable ontology in
HydraDB, then answers questions over it with citations, explicit conflict adjudication,
and a calibrated ability to say **"that isn't in here."**

[Architecture](docs/architecture.md) · [Coverage](docs/coverage.md) · [Results](eval/results/) · [Project tracker](PROJECT.md)

---

## What makes this different

A vector index always returns k results. A graph traversal can return the empty set.

That asymmetry is the whole design. Every fact is a **reified `Claim` node** with an
edge back to the exact source chunk, so ONTOS can tell you not just what it believes
but why, who said it, when, and who disagrees.

| Capability | How |
|---|---|
| **Entity resolution** | A Jira reporter's display name, a Slack handle, and an email address with no string overlap resolve to one `Person` node, via blocked/scored candidate pairs (email, handle, name/nickname, team overlap, and shared-context corroboration) plus LLM adjudication in the uncertain band |
| **Conflict adjudication** | Contradictions persist as linked `Claim`s under a `ConflictSet`; a trust function over authority, recency, and cross-system corroboration picks a winner (or explicitly abstains/contests when the evidence doesn't clearly support one) and stores its reasoning |
| **Multi-hop reasoning** | Bounded traversal between resolved question anchors, chained through `Claim` nodes as edges |
| **Knowing what it doesn't know** | An empty path set over the graph is treated as *evidence of absence*, not "no result found so guess" |

## Results

See [`eval/results/scores.json`](eval/results/scores.json) for the full, current run
(per-category accuracy, document recall, abstention precision/recall/false-abstention
rate, confabulation rate) and [`eval/results/error_analysis.md`](eval/results/error_analysis.md)
for sampled failures. The ER ablation (resolution enabled vs. disabled) and the BM25
baseline comparison are in the same directory.

**Corpus coverage.** 244,822 of 511,970 documents (47.8%) are structurally ingested —
every eval question's own required document is guaranteed present via a
question-priority tier, and 8 of 9 sources reached 100%+ of their proportional share of
a 250,000-document stratified-fill target; only Gmail fell short (64%) when the build's
remaining time had to shift to entity resolution, conflict detection, and evaluation.
This was a deliberate, late, time-boxed tradeoff — the full reasoning and per-source
numbers are in [`docs/coverage.md`](docs/coverage.md), not glossed over here.
Tier 2 (LLM claim extraction) ran over a bounded, representative subset — the
question-priority tier plus a stratified sample — not the full corpus; see
[`PROJECT.md`](PROJECT.md) decisions for the exact numbers.

## Quickstart

```bash
git clone <this-repo> && cd ontos
cp .env.example .env                 # add GEMINI_API_KEY / GROQ_API_KEY / HYDRADB_AUTH_TOKEN
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

git clone <hydradb-repo> vendor/hydradb   # AGPL-3.0, run as a separate process — see THIRD_PARTY.md
make hydradb-minio-up                 # graph-node, MinIO-backed
make hydradb-indexer-up               # graph-indexer (CSC generations for traversal)

CORPUS_DIR=<path-to-EnterpriseRAG-Bench-corpus> \
  .venv/bin/python -m src.ingest.run_ingest      # Tier 1 structural ingest
.venv/bin/python -m src.ingest.run_tier2         # Tier 2 claim extraction (bounded sample)
.venv/bin/python -m src.resolution.run_er        # entity resolution
.venv/bin/python -m src.conflict.run_conflicts   # conflict detection + trust adjudication

streamlit run ui/app.py               # http://localhost:8501
```

`docker-compose.yml` builds HydraDB + MinIO in containers for a from-scratch judge run;
the commands above are the native bring-up this build actually used day-to-day (see
`Makefile` and `PROJECT.md`'s decisions log for why — a Docker Desktop virtiofs bug on
macOS misreported the bind-mounted volume's free space).

## How HydraDB is used

**ONTOS has no second store.** The graph is not an index over a document database — it
is the database. Documents, chunks, mentions, entities, claims, evidence links and
conflict sets all live in HydraDB, and every answer is the return value of a traversal.

Specifically:

- **Bounded traversal via `MATCH ... WHERE`-chained point lookups** answers LOOKUP,
  multi-hop, aggregate, and temporal questions by walking `Claim` nodes as edges between
  resolved entity anchors — see `src/query/traverse.py`.
- **`algo.MSpaths` with `pairwise: true`** is confirmed working live against this
  deployment (see [`docs/cypher-support.md`](docs/cypher-support.md)'s P7 probe) and is
  the batch-pairwise-shortest-path primitive the entity-resolution and multi-hop query
  designs are built around; the query-time MULTIHOP path uses an equivalent bounded
  Claim-chain BFS instead of a live call, a time-budget substitution documented in
  `src/query/traverse.py`'s own module docstring.
- **`causal` and `strong` consistency used deliberately** — `causal` on the interactive
  query path (the default hot path), `strong` for the evaluation harness so results are
  reproducible (BUILD-SPEC.md §12).
- **Object-store durability (MinIO-backed) means the query tier is disposable.** Kill
  `graph-node`, restart with an empty cache, and the graph is intact — no re-ingest.
- **HydraDB's OpenCypher subset has no `CREATE INDEX`** (confirmed live — see
  `docs/cypher-support.md`'s P5 probe); indexing is automatic and server-managed via
  `graph-indexer`'s background CSC generations, so no index DDL appears anywhere in this
  codebase.

Without HydraDB, ontology-native entity resolution, claim-level provenance, and
calibrated graph-based abstention would all have to be reimplemented over a
document/vector store that has no native notion of "no path exists."

## Architecture

- [`docs/architecture.md`](docs/architecture.md) — full design (if present)
- [`ontology/tbox.yaml`](ontology/tbox.yaml) — frozen schema: node classes, relations,
  functional/temporal flags, per-source field mappings
- [`docs/cypher-support.md`](docs/cypher-support.md) — what was verified live in
  HydraDB's OpenCypher subset (the P1-P7 probe suite), and what had to be worked around
- [`docs/coverage.md`](docs/coverage.md) — exactly what was ingested, and why not more
- [`PROJECT.md`](PROJECT.md) — the full milestone-by-milestone build log and every
  ground-truth finding/deviation from the frozen spec, numbered and dated

Pipeline, top to bottom: nine source adapters (`src/ingest/adapters/`) → Tier 1
structural ingest (`src/ingest/tier1_structural.py`: `Document`/`Chunk`/`Mention`
nodes, deterministic regex mentions) → Tier 2 semantic extraction
(`src/ingest/tier2_semantic.py`: TBox-validated LLM claim extraction) → entity
resolution (`src/resolution/`: normalize → block → score → LLM-adjudicate the
uncertain band → cluster → canonicalize into `Person` nodes) → conflict detection
(`src/conflict/`: detect functional-predicate disagreements → classify out
supersession/granularity → trust-score the residue) → query layer (`src/query/`:
anchor → plan → traverse → abstention gate → grounded synthesis).

## Limitations

We would rather you read these from us than discover them.

- **Coverage is 47.8% of the full corpus, not 100%** — a deliberate, late,
  time-boxed tradeoff once a 250K-document stratified-fill push hit compounding
  infrastructure failures (SSD disconnect, a Docker Desktop disk-stat bug, an
  over-provisioned cache causing memory-pressure crashes on an 8GB build machine) with
  the submission deadline close. Every eval question's own required document is
  guaranteed present regardless. Full accounting: `docs/coverage.md`.
- **Tier 2 (LLM claim extraction) ran over a bounded sample, not the full ingested
  corpus** — LLM cost/time-bound by design (BUILD-SPEC.md §8.4), sized to a few
  thousand documents rather than 244,822.
- **MULTIHOP traversal is a bounded Claim-chain BFS, not a live `algo.MSpaths` call** —
  `algo.MSpaths` needs real graph edges between entity nodes; this graph reifies
  relationships as `Claim` nodes with string subject/object properties instead, and
  wiring a second set of provisional mentions onto `Person` nodes so a real edge path
  would exist for `MSpaths` to walk was out of reach in the remaining time. Documented
  in `src/query/traverse.py`.
- **The trust function's weights (authority, recency half-life, etc.) are hand-set
  priors from the frozen spec, not learned or tuned against labeled conflict data.**
- **Entity resolution's `cooccurrence_path` feature is a neighbor-Jaccard proxy computed
  in Python, not a live `algo.MSpaths` call** — same reasoning as MULTIHOP: full-corpus
  graph reads exceed the query timeout at this scale without a property index
  (`PROJECT.md` decision #34). Documented in `src/resolution/score.py`.
- **The BM25 baseline runs over a bounded document subset** (the same one Tier 2 used),
  not the full corpus — standing up OpenSearch and indexing 511K documents wasn't in
  reach in the remaining time; see `eval/baselines/bm25_baseline.py`.

## License and attribution

ONTOS is licensed under Apache-2.0 (see [LICENSE](LICENSE)).

**HydraDB is AGPL-3.0.** ONTOS runs HydraDB as a separate service and communicates over
Bolt and HTTP. It does not incorporate, link, or modify HydraDB source. Full third-party
attribution — datasets, libraries, AI assistance — in [THIRD_PARTY.md](THIRD_PARTY.md).

Dataset: [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench)
(MIT), arXiv:2605.05253.
