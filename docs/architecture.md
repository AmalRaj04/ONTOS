# ONTOS architecture

One HydraDB graph, no second store. This document walks the pipeline top to bottom,
matching the module layout in `src/`.

## Data flow

```
9 source adapters (src/ingest/adapters/)
        │  Document(doc_id, source_system, native_id, title, body,
        │           created_at, author_raw, thread_key, uri,
        │           content_hash, simhash)
        ▼
Tier 1 — structural (src/ingest/tier1_structural.py)
        │  chunk_body(): ~500-word paragraph-aware windows -> Chunk
        │  extract_mentions(): regex-only (handle/email/ticket_id/channel) -> Mention
        │  Document -[:HAS_CHUNK]-> Chunk -[:MENTIONS]-> Mention
        ▼
Tier 2 — semantic, bounded sample (src/ingest/tier2_semantic.py, run_tier2.py)
        │  LLM extraction, TBox-validated against ontology/tbox.yaml
        │  Claim(claim_id, predicate, subject_id, object_id|object_literal,
        │        polarity, asserted_at, extraction_confidence, evidence_chunk_id)
        │  Claim -[:EVIDENCED_BY]-> Chunk, Claim -[:ASSERTS/:ABOUT]-> provisional Mention
        ▼
Entity resolution (src/resolution/)
        │  normalize -> block -> score -> LLM-adjudicate uncertain band -> cluster -> canonicalize
        │  Person(canonical_id, name, primary_email, alias_count, source_systems, cluster_size)
        │  Mention -[:RESOLVES_TO]-> Person   (mentions are never deleted)
        ▼
Conflict detection (src/conflict/)
        │  detect: Claim pairs sharing (resolved subject, functional predicate),
        │          >1 distinct object
        │  classify: rule out SUPERSEDES (temporal gap) and GRANULARITY (substring),
        │            LLM-classify the residue as CONTRADICTION/SCOPE_DIFFERENT/
        │            NEGATION/DUPLICATE
        │  trust: score each distinct object value, margin-gate the winner
        │  ConflictSet(subject, predicate, resolution_status, winner, margin, rationale)
        │  ConflictSet -[:INVOLVES]-> Claim
        ▼
Query layer (src/query/)
        │  anchor: resolve question entities, same normalization as ER,
        │          through data/er_alias_map.json
        │  plan: classify LOOKUP/MULTIHOP/CONFLICT/AGGREGATE/TEMPORAL (1 LLM call)
        │  traverse: direct Claim match / Claim-chain BFS / direct ConflictSet read /
        │            predicate-filtered aggregate / chronological sort
        │  gate: abstain on unresolved anchors, empty traversal, or weak uncorroborated
        │        evidence — an empty bounded result is evidence of absence
        │  synthesize: grounded generation, cite every claim, surface conflicts,
        │              re-check absence in the prompt itself (defence in depth)
        ▼
Output contract (BUILD-SPEC.md §11): {question_id, answer, abstained, confidence,
citations[], traversal{}, conflicts[], graph_stats{}}
```

## Schema

Frozen node labels (`ontology/tbox.yaml`): `Person, Team, Project, Product, Customer,
Ticket, Meeting, Thread`, plus the pipeline's own first-class types `Document, Chunk,
Mention, Claim, ConflictSet`. 15 relations, 9 of them `functional: true` (single-valued
per subject — the ones conflict detection operates over): `OWNS, REPORTS_TO,
LAUNCH_DATE, STATUS, DEADLINE, PRICE, HEADCOUNT, PRIORITY, TIER`.

Every node also carries an internal non-negative integer `id` — HydraDB's physical
vertex identity requirement — derived deterministically from the same content-hash as
the human-readable string id (`src/schema/ids.py`'s `hydra_id()`), so `MERGE`-based
upserts stay idempotent across resumed/retried ingest runs.

## Why the query layer doesn't use a live `algo.MSpaths` call

`algo.MSpaths` (confirmed working live — `docs/cypher-support.md`'s P7 probe) is a
batch pairwise-shortest-path primitive over *graph edges between nodes*. This schema
reifies relationships as `Claim` **nodes** with `subject_id`/`object_id` string
properties (per BUILD-SPEC.md §7.5's frozen `Claim` model) rather than as edges
directly connecting resolved entities — so there's no `Person`-to-`Person` edge for
`MSpaths` to walk yet. Wiring `RESOLVES_TO` onto the claim-subject/claim-object
provisional mentions too (not just the author/email/handle mentions ER actually
resolved) would create that edge path, but was out of reach in the build's remaining
time. `src/query/traverse.py`'s `traverse_multihop()` instead does a bounded BFS over
Claims-as-edges, entity-resolution-backed via the same alias map anchor.py uses — real
multi-hop reasoning, implemented differently than originally planned. Documented as a
time-budget substitution in that module, not silently swapped in.

## Why full-corpus label scans are avoided everywhere except query time

HydraDB has no user-declarable index DDL (`CREATE INDEX` is rejected at parse time —
confirmed live, P5 probe); indexing is automatic and server-managed via
`graph-indexer`'s background CSC generations. At 244,822 Document nodes, a
`MATCH (n:Label) WHERE ...` full-label scan exceeds the 240s query timeout
(`PROJECT.md` decision #34) — confirmed live during this build. ID-keyed point lookups
(`MATCH (n {id: $vertex})`, the pattern the whole query layer actually uses) remain
fast regardless. Entity resolution (`src/resolution/records.py`) and conflict
detection (`src/conflict/detect.py`) both avoid this by reading directly from the
local corpus files / a local `data/claims.jsonl` mirror instead of scanning the graph
for candidate gathering — the graph write at the end of each pipeline is still the
authoritative source of truth for query time.

## Consistency modes

`causal` on the interactive query path (default hot path, BUILD-SPEC.md §11 step 3);
`strong` for the evaluation harness (`eval/run_eval.py`) so results are reproducible
run to run (BUILD-SPEC.md §12).
