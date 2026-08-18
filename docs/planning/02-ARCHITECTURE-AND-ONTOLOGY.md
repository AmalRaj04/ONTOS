# 02 — Architecture and Ontology Schema

The system design and the graph schema. **Freeze §4 by hour 8** — it is the only hard
coupling between the four workstreams.

---

## 1. HydraDB: what you are actually building against

Grounded in the repo README, not assumption. Read `cypher-compat.md` and `architecture.md`
in the repo before you write a line of Cypher; the notes below are the load-bearing parts.

| Property | Value | Consequence for you |
|---|---|---|
| Language | Rust, requires 1.91+, `libcypher-parser`, SuiteSparse GraphBLAS | Build takes real time. Do it first, on every machine, hour 0. |
| Query language | **A practical OpenCypher subset** | See §2 — the subset is the biggest technical risk in the project. |
| Wire protocols | Bolt 5.x (Neo4j drivers) and HTTPS JSON/NDJSON | Use the Python `neo4j` driver. It works, and the repo ships smoke tests proving it. |
| Storage | S3-compatible object store is the durable source of truth | Query nodes are disposable. This is a demo asset — see §7. |
| Consistency | `causal` (default) and `strong` | Deliberate use of both is a differentiator — see §7. |
| Path procedures | `algo.SPpaths`, `algo.SSpaths`, `algo.MSpaths` | `MSpaths` is your workhorse. See §5. |
| License | AGPL-3.0 | Run as a separate process. See doc 00 §7. |
| Ports | Bolt 7687, HTTP 8443, admin 9090 | `/readyz` and `/metrics` on admin |

### Setup landmines the README names explicitly

These are documented failure modes. Hitting one at hour 3 is annoying; hitting one at
hour 60 is fatal. Handle them all in your bootstrap script:

- **`RUST_MIN_STACK=33554432` is mandatory.** Without it the node starts, serves
  `/readyz`, and then *aborts on the first query with a stack overflow*. This is the
  single nastiest failure mode in the stack because everything looks healthy until it
  isn't.
- `CLOUD_PROVIDER` unset reports as the literal string `null`. `local` also needs
  `LOCAL_PATH` pointing at a directory **that already exists**.
- macOS: `brew install cleishm/neo4j/libcypher-parser` — the plain name does not exist.
  Rust must come from the official installer, not Homebrew.
- macOS invoking `cargo` directly needs `BINDGEN_EXTRA_CLANG_ARGS` and `LIBRARY_PATH`
  exported; `just` exports them for you, direct `cargo` does not.
- `GRAPH_ALLOW_PLAINTEXT=true` for local dev; TLS is required by default otherwise.
- The node holds the foreground. That is it working, not hanging. Second shell for
  everything else.

Verify with `just native-check` then `just smoke` before you trust anything. A listening
port is not proof the node works — the README says this outright. **A round-tripped write
is.** Put a round-tripped write in your healthcheck.

## 2. The Cypher subset is your biggest technical risk

The README documents support for: typed relationships, bounded variable-length paths,
property and label predicates, ordering, pagination, aggregation, `OPTIONAL MATCH`,
`UNION`, and batched `UNWIND` writes.

**`MERGE` is not on that list. Neither is `CREATE INDEX`, nor any APOC procedure.**

It may still be supported — the list reads as illustrative rather than exhaustive, and
`cypher-compat.md` exists precisely to answer this. But **do not design around `MERGE`
until you have personally run one against a live node.** Idempotent upsert is load-bearing
for every ingest pipeline ever written, and discovering at hour 30 that you don't have it
is a project-ending event.

**Hour 1, first thing, before any design work — run this probe:**

```cypher
// probe 1: MERGE
MERGE (p:Person {canonical_id: 'probe-1'}) RETURN p.canonical_id;
// probe 2: MERGE on relationship
MATCH (a:Person {canonical_id:'probe-1'})
MERGE (a)-[r:PROBE]->(b:Person {canonical_id:'probe-2'}) RETURN type(r);
// probe 3: batched UNWIND write (documented as supported)
UNWIND [{id:'p3'},{id:'p4'}] AS row CREATE (p:Person {canonical_id: row.id}) RETURN count(p);
// probe 4: bounded var-length path (documented as supported)
MATCH path = (a:Person {canonical_id:'probe-1'})-[:PROBE*1..3]->(b) RETURN length(path);
// probe 5: index DDL
CREATE INDEX FOR (p:Person) ON (p.canonical_id);
// probe 6: aggregation + OPTIONAL MATCH (documented as supported)
MATCH (p:Person) OPTIONAL MATCH (p)-[:PROBE]->(q) RETURN p.canonical_id, count(q) ORDER BY p.canonical_id;
```

Record the results in `docs/cypher-support.md` in your repo. That file is itself a
judging asset — it demonstrates you engaged with the real system rather than assuming
Neo4j semantics.

**Fallback if `MERGE` is absent** (design for this now, it costs nothing if unneeded):
push idempotency to the client. Maintain a deterministic ID scheme (§4.1), keep an
in-process `set` of already-written IDs during a batch, and use `UNWIND … CREATE` for
new entities only. Ingest is a single-writer batch job, so you own the write path
completely and client-side dedupe is sound. This is arguably *faster* than `MERGE`
anyway — you avoid a read-before-write per row.

**Fallback if `CREATE INDEX` is absent:** the README says the planner uses property
indexes, and `algo.MSpaths` takes `sourceProperty` / `sourceValues`, which strongly
implies indexed property lookup exists in some form. If explicit DDL is unavailable,
indexing is likely implicit or configured at the node level — check `architecture.md`
§ index lifecycle, and ask in Discord. Do not silently proceed with unindexed lookups
over 500K nodes.

**Use Discord.** The HydraDB team runs office hours all nine days and answers repo
questions fastest there. A Cypher-compatibility question answered in ten minutes is
worth more than four hours of your own bisection.

## 3. System architecture

```
                         ┌──────────────────────────────┐
   EnterpriseRAG-Bench   │  9 source adapters           │
   (~500K docs, 9 srcs)  │  slack/gmail/linear/gdrive/  │
          │              │  hubspot/fireflies/github/   │
          └─────────────▶│  jira/confluence             │
                         └──────────────┬───────────────┘
                                        │ canonical Document records
                                        ▼
                         ┌──────────────────────────────┐
                         │ TIER 1 — structural ingest   │  no LLM
                         │ • Document nodes             │  ~6h, whole corpus
                         │ • AUTHORED_BY / IN_THREAD    │
                         │ • deterministic mentions:    │
                         │   @handles, emails, ENG-123  │
                         │ • MinHash near-dup clusters  │
                         └──────────────┬───────────────┘
                                        │ batched UNWIND writes
                                        ▼
              ╔═════════════════════════════════════════════════╗
              ║              H Y D R A D B                      ║
              ║   OpenCypher · Bolt 5.x · object-store durable   ║
              ╚════════╤════════════════════════════════╤═══════╝
                       │                                │
        ┌──────────────▼───────────┐      ┌─────────────▼──────────────┐
        │ TIER 2 — semantic enrich │      │ QUERY LAYER                │
        │ • LLM claim extraction   │      │ • plan (LOOKUP/HOP/        │
        │ • entity resolution      │◀─────│   CONFLICT/ABSENCE)        │
        │ • conflict detection     │ lazy │ • traverse (algo.MSpaths)  │
        │   (doc 03, doc 04)       │ fill │ • assemble evidence        │
        │ • cached back to graph   │      │ • ABSTENTION GATE          │
        └──────────────────────────┘      │ • grounded synthesis       │
                                          └─────────────┬──────────────┘
                                                        ▼
                                          ┌────────────────────────────┐
                                          │ Answer + citations + path  │
                                          │ + conflict adjudication    │
                                          │ + "not in corpus" w/ reason│
                                          └────────────────────────────┘
```

The arrow from the query layer back into Tier 2 is the lazy-extraction path from doc 00
§5, and it is the loop worth talking about in the video: **cheap traversal decides where
to spend expensive extraction.**

## 4. The ontology schema

This is the artifact to freeze. Ship it as `schema.cypher` + `src/schema/models.py`.

### 4.1 Deterministic IDs

Every node gets a content-addressed ID. This buys you client-side idempotency (§2),
reproducible ingest, and safe re-runs after a crash — which you *will* need.

```python
def node_id(kind: str, *parts: str) -> str:
    """Stable across runs, machines, and partial re-ingests."""
    raw = "\x1f".join([kind, *(p.strip().lower() for p in parts)])
    return f"{kind}:{hashlib.blake2b(raw.encode(), digest_size=12).hexdigest()}"

# Document — natural key from the corpus
node_id("doc", source_system, source_native_id)
# Mention — a surface form at a position in a document
node_id("mention", doc_id, str(char_offset), surface)
# Claim — content-addressed on the normalized triple + source
node_id("claim", subject_id, predicate, object_repr, doc_id)
# Entity — assigned by resolution, NOT content-addressed (it changes as clusters merge)
node_id("person", uuid4().hex)
```

Note the asymmetry: **entities get opaque IDs, everything else is content-addressed.**
Entity identity is a *decision* your resolver makes and can revise; document identity is
a fact. Encoding that distinction in the ID scheme prevents a whole class of bug where
re-running resolution scrambles your provenance.

### 4.2 Node labels

| Label | Purpose | Key properties |
|---|---|---|
| `Document` | One source artifact | `doc_id`, `source_system`, `native_id`, `title`, `created_at`, `updated_at`, `uri`, `content_hash`, `simhash` |
| `Chunk` | Passage within a document | `chunk_id`, `ordinal`, `text`, `char_start`, `char_end` |
| `Mention` | A surface form at a position | `mention_id`, `surface`, `char_offset`, `mention_type` |
| `Person` | Canonical human | `canonical_id`, `display_name`, `primary_email`, `confidence`, `alias_count` |
| `Team` | Org unit | `canonical_id`, `name` |
| `Project` | Initiative / epic | `canonical_id`, `name`, `status` |
| `Product` | Product or component | `canonical_id`, `name` |
| `Customer` | External account | `canonical_id`, `name`, `domain` |
| `Ticket` | Linear/Jira/GitHub issue | `canonical_id`, `tracker`, `key`, `state` |
| `Meeting` | Fireflies transcript event | `canonical_id`, `occurred_at` |
| `Thread` | Slack thread / email chain | `canonical_id`, `channel` |
| `Claim` | **Reified fact** | `claim_id`, `predicate`, `subject_id`, `object_id`/`object_literal`, `asserted_at`, `extraction_confidence`, `polarity` |
| `ConflictSet` | Cluster of mutually incompatible claims | `conflict_id`, `predicate`, `resolution_status`, `winner_claim_id`, `rationale` |

### 4.3 Relationship types

| Type | From → To | Carries |
|---|---|---|
| `HAS_CHUNK` | Document → Chunk | `ordinal` |
| `MENTIONS` | Chunk → Mention | — |
| `RESOLVES_TO` | Mention → Person/Project/… | `score`, `method` |
| `ALIAS_OF` | Mention/alias string → canonical entity | `surface`, `evidence_count` |
| `AUTHORED_BY` | Document → Person | `role` |
| `IN_THREAD` | Document → Thread | `position` |
| `NEAR_DUPLICATE_OF` | Document → Document | `similarity` |
| `EVIDENCED_BY` | **Claim → Chunk** | `char_start`, `char_end` |
| `ASSERTS` | Claim → entity (subject) | — |
| `ABOUT` | Claim → entity (object) | — |
| `CONTRADICTS` | Claim ↔ Claim | `detector`, `score` |
| `SUPERSEDES` | Claim → Claim | `reason` |
| `IN_CONFLICT_SET` | Claim → ConflictSet | — |
| `WORKS_ON` | Person → Project | derived |
| `MEMBER_OF` | Person → Team | derived |
| `BLOCKS` / `RELATES_TO` | Ticket → Ticket | — |
| `OWNS` | Person → Ticket/Project | — |

### 4.4 The one design decision that matters most

**Claims are nodes, not edge properties.** Everything downstream depends on this.

The obvious modelling is `(:Person)-[:OWNS {source: 'doc-123'}]->(:Project)`. It is
compact and it is a dead end. You cannot attach two contradictory owners without either
losing one or creating parallel edges you cannot then reason over as a set. You cannot
carry per-assertion timestamps distinct from document timestamps. You cannot express
"three sources agree, one disagrees" as a first-class structure. And you cannot answer
"why do you believe this" without a place to hang the reasoning.

Reified:

```cypher
(:Claim {predicate:'OWNS', asserted_at: ...})
  -[:ASSERTS]->      (:Person   {canonical_id:'person:a1b2'})
  -[:ABOUT]->        (:Project  {canonical_id:'project:c3d4'})
  -[:EVIDENCED_BY]-> (:Chunk)<-[:HAS_CHUNK]-(:Document {source_system:'confluence'})
```

Now a contradiction is `(:Claim)-[:CONTRADICTS]-(:Claim)`, adjudication is a property on
a `ConflictSet`, corroboration is a `count()` over distinct `EVIDENCED_BY` sources, and
"why do you believe this" is a traversal. Every feature in docs 03, 04 and 05 falls out
of this decision. Every one of them is blocked without it.

The cost is node count — roughly 3–5× more nodes than the naive model. HydraDB is built
for graph scale on object storage. Spend it.

### 4.5 Schema-as-code

```python
# src/schema/models.py — frozen at hour 8
from pydantic import BaseModel
from typing import Literal
from datetime import datetime

SourceSystem = Literal["slack","gmail","linear","gdrive","hubspot",
                       "fireflies","github","jira","confluence"]

class Document(BaseModel):
    doc_id: str
    source_system: SourceSystem
    native_id: str
    title: str | None
    body: str
    created_at: datetime | None
    author_raw: str | None
    thread_key: str | None
    uri: str | None
    content_hash: str
    simhash: int

class Claim(BaseModel):
    claim_id: str
    predicate: str
    subject_id: str
    object_id: str | None
    object_literal: str | None
    polarity: Literal["affirm","negate"] = "affirm"
    asserted_at: datetime | None
    extraction_confidence: float
    evidence_chunk_id: str
```

Every adapter's job is to emit `Document`. Nothing else. That one-line contract is what
lets four people work in parallel without a standup every hour.

## 5. Traversal: `algo.MSpaths` is the workhorse

The native path procedures are the most HydraDB-specific thing you can use, and
`MSpaths` in particular is designed for exactly the shape of problem you have — the
README says it "resolves many indexed source and target values and evaluates them
together, avoiding client-side query fan-out."

You have two workloads that are naturally many-source, many-target:

**(a) Entity-resolution candidate scoring.** Given 300 candidate mention pairs, you want
a co-occurrence-path score for each. Client-side that is 300 round trips. Server-side:

```cypher
CALL algo.MSpaths({
  sourceLabel: 'Mention',
  sourceProperty: 'surface_norm',
  sourceValues: $candidate_surfaces,
  targetValues: $candidate_surfaces,
  pairwise: true,
  relTypes: ['MENTIONS','HAS_CHUNK','IN_THREAD','AUTHORED_BY'],
  relDirection: 'both',
  maxLen: 4,
  pathCount: 5,
  resultLimit: 2000
})
YIELD path
RETURN path
```

**(b) Multi-hop question answering.** Question mentions three entities; you want the
connecting subgraph, not three independent top-k lists.

Use it for both. Say so in the video. This is a concrete, specific, non-generic use of
HydraDB's own API surface, and it is precisely what the Best Use award asks for.

## 6. Handling the corpus's declared noise

The corpus is generated with cross-document coherence and then *deliberately* seeded with
misfiled documents, near-duplicates and conflicting information. Each needs a specific
mechanism; none is handled by "just index it".

### 6.1 Misfiled documents
Treat a document's declared location — channel, folder, project field — as **a claim
about the document, not a fact.** Store it as `declared_container`, and separately derive
`inferred_container` from content: entities mentioned, thread neighbours, ticket
references. When they disagree beyond a threshold, emit a `Claim` with predicate
`FILED_IN` and let the normal conflict machinery handle it. Retrieval scopes by inferred
container with declared container as a weak prior — so a misfiled doc is still findable.

The demo beat: show a document sitting in the wrong channel that ONTOS still retrieves
correctly, and show *why* — the inferred container edge.

### 6.2 Near-duplicates
SimHash (64-bit) at ingest, banded LSH for candidate pairs, exact Jaccard on shingles for
confirmation. Cluster into `NEAR_DUPLICATE_OF` components with one canonical
representative.

The subtle and important part: **corroboration must count distinct sources, not distinct
documents.** Otherwise a fact duplicated across five near-identical docs outvotes a fact
stated once authoritatively — which is exactly the trap the corpus is built to spring.
Doc 04 §4 handles this in the trust function; the dedupe clusters are what make it
possible.

### 6.3 Contradictions
Doc 04, in full.

## 7. "What would this project lose without HydraDB?"

The rules require you to be ready to answer this. Here is the answer. Put it in README §7
and learn it.

> ONTOS has no second store. The graph is not an index over a document database — it is
> the database. Documents, chunks, mentions, entities, claims, evidence links and
> conflict sets all live in HydraDB, and every answer the system produces is the return
> value of a traversal.
>
> Four things would break, not degrade:
>
> **Entity resolution** is transitive closure over a similarity graph, scored by
> co-occurrence paths. We evaluate hundreds of candidate pairs in single
> `algo.MSpaths` calls with `pairwise: true`, server-side, on one pinned snapshot.
> Without graph-native batch path evaluation this becomes client-side fan-out and stops
> being tractable at 500K documents.
>
> **Multi-hop answers** are bounded variable-length traversals. Top-k similarity cannot
> chain; each hop compounds the recall loss of the last.
>
> **Conflict adjudication** requires claim-level provenance as first-class structure —
> reified `Claim` nodes with `EVIDENCED_BY` edges and `CONTRADICTS` links. A chunk
> embedding has nowhere to put "who asserted this, when, and with what corroboration".
>
> **Abstention** is the one we care about most. A vector index always returns k results;
> similarity has no zero. A bounded traversal that terminates with an empty path set is
> positive evidence of absence, and HydraDB's snapshot-consistent reads make that
> emptiness *trustworthy* — we know we searched a single consistent view of the graph,
> not a partially-visible one.
>
> We also use HydraDB's two read modes deliberately: `strong` after each ingest barrier
> and before every evaluation run, so results are reproducible against a known-fresh
> snapshot; `causal` on the interactive query path, where the latency of an object-store
> freshness check is not worth paying.

### The recovery demo — 20 seconds that prove the point

HydraDB's central architectural claim is that S3-compatible object storage is the durable
source of truth and that query nodes hold only disposable state. Prove you understood it:

1. Show the graph answering a question.
2. `kill -9` the `graph-node` process on camera.
3. Restart it. Point at the empty local cache directory.
4. Ask the same question. Same answer.

You did not re-ingest 500,000 documents. Most teams will use HydraDB as "a graph database
that happens to be there". This shows you built *on the architecture*. It costs twenty
seconds of video and it is the strongest single moment you can put in front of the Best
Use panel.
