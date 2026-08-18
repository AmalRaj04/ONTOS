# ONTOS — Build Specification

**Read this entire document before writing any code.** It is self-contained — you do
not need the other planning documents to build the system, though they exist in
`docs/planning/` if you want the reasoning behind a decision.

Project: an enterprise ontology built on HydraDB, for Hack Hydra Track 01.
Deadline: 2026-08-21, 12:29 PM IST. Build accordingly — see §13 for the milestone plan
this implies.

---

## 0. How to use this document

- §7 (ontology schema) is **frozen**. Do not redesign it mid-build. If you find a real
  problem with it, stop and flag it rather than silently deviating — a schema change
  after ingestion has started is expensive.
- §7.6 defines the TBox authoring step — this runs **before** any Tier 2 semantic
  extraction (§8.4) and produces `ontology/tbox.yaml`, which Tier 2 then validates every
  extracted claim against. Do not start Tier 2 extraction before the TBox is frozen (M0.5
  in §13). Check for the dataset's own generation scaffolding first (§7.6.1) — it may
  remove most of the authoring work.
- §6's Cypher probe suite must run, and its results must be recorded, **before** any
  ingestion code is written. The result changes how the ingest writer works (§8.3).
- Two real repos should be cloned alongside this one before you start (see the human
  prerequisites checklist — they should already be present): `hydra-db/hydradb` and the
  EnterpriseRAG-Bench release. Ground truth in those repos (`architecture.md`,
  `cypher-compat.md`, actual document JSON shapes) overrides any description below if
  they conflict — this spec was written from that repo's README, not from running code.
- Work through §13's milestones in order. Each has an explicit Definition of Done.
  Don't start M2 with M1 incomplete.
- §14 is a scope guard. If a task isn't in this document and isn't required to satisfy
  §13, don't build it without checking in first.

---

## 1. Mission

Ingest the EnterpriseRAG-Bench corpus (~500,000 documents across Slack, Gmail, Linear,
Google Drive, HubSpot, Fireflies, GitHub, Jira, Confluence) into HydraDB as a single
queryable ontology. Every extracted fact is a reified `Claim` node with a provenance
edge to its exact source. Entities with many surface forms ("Sam" / "@soham" /
"S. Ratnaparkhi") resolve to one canonical node via graph-native evidence, not string
matching alone. Contradictory claims are never silently overwritten — they persist,
linked, and are adjudicated by an explicit, inspectable trust function. A query layer
answers questions by traversal, and — this is the core differentiator — **treats an
empty bounded traversal as evidence of absence**, correctly declining to answer
questions the corpus doesn't cover instead of confabulating.

The one-sentence thesis: a vector index always returns k results; a graph traversal can
return the empty set. Every design decision below serves that sentence.

## 2. Non-negotiable constraints

- HydraDB is AGPL-3.0. **Run it as a separate process/container, communicate only over
  Bolt or HTTP.** Never vendor, fork, or statically link its source into this repo.
  This repo is licensed Apache-2.0.
- Claims are graph **nodes**, never edge properties. This is not a style preference —
  conflict handling and provenance are structurally impossible without it. See §7.4.
- No second datastore. HydraDB is the only place data lives. No shadow vector DB, no
  separate document store holding the "real" answer.
- Every write path must be resumable/checkpointed. Ingest of ~500K documents will be
  interrupted; a non-resumable pipeline costs hours you don't have.
- No LLM call may be made without a token/request budget and a cache. See §16.

## 3. Tech stack — fixed, do not re-litigate

| Layer | Choice |
|---|---|
| Graph DB | HydraDB, run via Docker Compose (`docker-compose.yml`) or `cargo run --bin graph-node` |
| DB driver | Official `neo4j` Python driver, Bolt 5.x, routed URI |
| Object storage (HydraDB's backing store) | MinIO, local dev |
| App language | Python 3.12 |
| Data validation | Pydantic v2 |
| LLM providers | Gemini (Google AI Studio, free tier) + Groq (free tier) — used as two independent worker pools, see §16 |
| Near-dup detection | `datasketch` (MinHash/LSH) |
| Graph utilities (client-side clustering) | `networkx` |
| Eval / scripting | plain Python, `tqdm`, resumable JSONL |
| Demo UI | Streamlit (single page) |
| Orchestration | `Makefile` or `just` — pick one, keep it thin |

Do not introduce a second graph library, a second web framework, or a vector DB. If a
task seems to need one, it's a sign the design has drifted from §1 — stop and reconsider
rather than reaching for a new dependency.

## 4. Repository layout

```
ontos/
├── README.md                    # judge-facing, doc 08 template
├── LICENSE                      # Apache-2.0
├── THIRD_PARTY.md
├── docker-compose.yml           # HydraDB + MinIO
├── Makefile
├── .env.example
├── schema.cypher                # §7.4, applied at bootstrap
├── src/
│   ├── schema/
│   │   ├── models.py            # §7.5 Pydantic models — frozen
│   │   └── ontology.yaml        # predicate alignment table, §9.9-equivalent
│   ├── ingest/
│   │   ├── adapters/            # one file per source: slack.py, gmail.py, ...
│   │   ├── tier1_structural.py
│   │   ├── tier2_semantic.py
│   │   ├── dedupe.py             # SimHash/LSH near-duplicate clustering
│   │   └── writer.py             # batched UNWIND writes, idempotent
│   ├── resolution/
│   │   ├── normalize.py
│   │   ├── block.py
│   │   ├── score.py               # includes algo.MSpaths batch scoring
│   │   ├── cluster.py             # connected components + guards
│   │   ├── adjudicate_llm.py      # uncertain-band LLM calls
│   │   └── canonicalize.py
│   ├── conflict/
│   │   ├── detect.py
│   │   ├── classify.py            # taxonomy: temporal/scope/granularity/true
│   │   └── trust.py               # the trust function
│   ├── query/
│   │   ├── anchor.py
│   │   ├── plan.py
│   │   ├── traverse.py
│   │   ├── gate.py                 # abstention gate — highest-priority file in repo
│   │   └── synthesize.py
│   ├── llm/
│   │   ├── providers.py            # Gemini + Groq clients, uniform interface
│   │   ├── router.py               # dual-provider load balancing, §16
│   │   └── cache.py                # content-addressed response cache
│   └── db/
│       └── client.py                # HydraDB Bolt session wrapper, consistency modes
├── eval/
│   ├── run_eval.py
│   ├── baselines/bm25.py
│   ├── ablations/
│   └── results/
├── ui/
│   └── app.py                       # Streamlit demo
├── docs/
│   ├── planning/                    # the 9 strategy docs, for humans
│   ├── architecture.md
│   ├── cypher-support.md            # §6 probe results — write this first
│   └── coverage.md                  # what fraction of corpus is semantically enriched
└── tests/
```

## 5. Environment variables

```bash
# .env.example

# HydraDB connection
HYDRADB_BOLT_URI=neo4j://127.0.0.1:7687
HYDRADB_HTTP_URI=http://127.0.0.1:8443
HYDRADB_AUTH_TOKEN=local-development-token-32-bytes

# LLM providers — see §16. Both required for the dual-worker pattern.
GEMINI_API_KEY=
GROQ_API_KEY=

# Corpus location
CORPUS_DIR=./data/enterpriserag-bench   # downloaded release artifacts

# Ingest tuning
INGEST_BATCH_SIZE=500
TIER2_SAMPLE_FRACTION=0.08              # stratified sample size, tune down if rate-limited

# Eval
EVAL_CONSISTENCY_MODE=strong            # strong for reproducible eval runs
```

`RUST_MIN_STACK=33554432` must be set in whatever process launches `graph-node` — put
it in `docker-compose.yml`'s environment block or the launch script. Without it the node
starts, serves `/readyz`, and aborts on the first query.

## 6. Phase 0 — HydraDB bring-up and Cypher probe

Do this before any application code.

```bash
git clone https://github.com/hydra-db/hydradb.git vendor/hydradb   # reference only, not linked
cd vendor/hydradb && just native-check && just smoke
```

Bring up the dev node (or use `docker-compose.yml` if already written):

```bash
mkdir -p .hydradb/store .hydradb/cache
printf '%s\n' 'local-development-token-32-bytes' > .hydradb/auth-token
export CLOUD_PROVIDER=local LOCAL_PATH="$PWD/.hydradb/store" \
       GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_CELL_ID=cell-0 GRAPH_CELLS=cell-0 \
       GRAPH_NODE_ID=node-0 GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
       GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 GRAPH_DATA_CACHE_DIR="$PWD/.hydradb/cache" \
       GRAPH_AUTH_TOKEN_FILE="$PWD/.hydradb/auth-token" GRAPH_ALLOW_PLAINTEXT=true \
       RUST_MIN_STACK=33554432
cargo run --locked --features server-runtime --bin graph-node
```

Verify with a round-tripped write (a listening port is not proof — the write must
return the right value):

```bash
curl -sS http://127.0.0.1:8443/v1/graphs/default/query \
  -H "Authorization: Bearer local-development-token-32-bytes" \
  -H 'X-Graph-Namespace: default' -H 'Content-Type: application/json' \
  --data '{"cell_id":"cell-0","query":"CREATE (a {id: 1})-[:FOLLOWS]->(b {id: 2})"}'
curl -sS http://127.0.0.1:8443/v1/graphs/default/query \
  -H "Authorization: Bearer local-development-token-32-bytes" \
  -H 'X-Graph-Namespace: default' -H 'Content-Type: application/json' \
  --data '{"cell_id":"cell-0","query":"MATCH (a {id: 1})-[:FOLLOWS]->(b) RETURN b.id AS id"}'
```

Second call must return `{"type":"vertex_id","value":2}`.

**Now run the probe suite and write `docs/cypher-support.md`:**

```cypher
// P1 — node MERGE
MERGE (p:Person {canonical_id: 'probe-1'}) RETURN p.canonical_id;
// P2 — relationship MERGE
MATCH (a:Person {canonical_id:'probe-1'})
MERGE (a)-[r:PROBE]->(b:Person {canonical_id:'probe-2'}) RETURN type(r);
// P3 — batched UNWIND write (documented as supported)
UNWIND [{id:'p3'},{id:'p4'}] AS row CREATE (p:Person {canonical_id: row.id}) RETURN count(p);
// P4 — bounded variable-length path (documented as supported)
MATCH path = (a:Person {canonical_id:'probe-1'})-[:PROBE*1..3]->(b) RETURN length(path);
// P5 — index DDL
CREATE INDEX FOR (p:Person) ON (p.canonical_id);
// P6 — aggregation + OPTIONAL MATCH (documented as supported)
MATCH (p:Person) OPTIONAL MATCH (p)-[:PROBE]->(q)
RETURN p.canonical_id, count(q) ORDER BY p.canonical_id;
// P7 — the native path procedure the whole ER design depends on
CALL algo.MSpaths({
  sourceLabel:'Person', sourceProperty:'canonical_id',
  sourceValues:['probe-1','probe-2'], targetValues:['probe-1','probe-2'],
  pairwise:true, relTypes:['PROBE'], relDirection:'both', maxLen:2, pathCount:3
}) YIELD path RETURN path;
```

**Decision gate:**
- If P1/P2 (`MERGE`) fail → implement `writer.py` using the client-side idempotent
  pattern in §8.3, not `MERGE`. Do this now, don't discover it mid-ingest.
- If P5 (`CREATE INDEX`) fails → read `architecture.md` in the cloned reference repo for
  the index lifecycle section, or ask in the Hack Hydra Discord, before proceeding
  unindexed over 500K nodes.
- If P7 fails → this blocks the entire entity-resolution batch-scoring design (§9) and
  the multi-hop query design (§11). Stop and resolve this before writing resolution
  code; check `cypher-compat.md` and Discord immediately.

Write the actual results — pass/fail and any error text — to `docs/cypher-support.md`.
This file is also a judging asset later; keep it accurate.

## 7. Ontology schema — FROZEN

### 7.1 ID scheme

```python
import hashlib

def node_id(kind: str, *parts: str) -> str:
    raw = "\x1f".join([kind, *(p.strip().lower() for p in parts)])
    return f"{kind}:{hashlib.blake2b(raw.encode(), digest_size=12).hexdigest()}"

# Documents, mentions, claims: content-addressed (deterministic from source data)
node_id("doc", source_system, source_native_id)
node_id("mention", doc_id, str(char_offset), surface)
node_id("claim", subject_id, predicate, object_repr, doc_id)

# Entities (Person/Project/Team/...): opaque, assigned at canonicalization
# because resolution is a revisable hypothesis, not a fact
f"person:{uuid4().hex}"
```

### 7.2 Node labels

| Label | Key properties |
|---|---|
| `Document` | `doc_id`, `source_system`, `native_id`, `title`, `created_at`, `updated_at`, `uri`, `content_hash`, `simhash`, `declared_container` |
| `Chunk` | `chunk_id`, `ordinal`, `text`, `char_start`, `char_end` |
| `Mention` | `mention_id`, `surface`, `surface_norm`, `char_offset`, `mention_type` |
| `Person` | `canonical_id`, `display_name`, `primary_email`, `confidence`, `alias_count`, `resolution_method` |
| `Team` | `canonical_id`, `name` |
| `Project` | `canonical_id`, `name`, `status` |
| `Product` | `canonical_id`, `name` |
| `Customer` | `canonical_id`, `name`, `domain` |
| `Ticket` | `canonical_id`, `tracker`, `key`, `state` |
| `Meeting` | `canonical_id`, `occurred_at` |
| `Thread` | `canonical_id`, `channel` |
| `Claim` | `claim_id`, `predicate`, `subject_id`, `object_id`, `object_literal`, `asserted_at`, `extraction_confidence`, `polarity`, `trust` |
| `ConflictSet` | `conflict_id`, `predicate`, `subject_id`, `conflict_type`, `resolution_status`, `winner_claim_id`, `margin`, `rationale` |

### 7.3 Relationship types

| Type | From → To | Key properties |
|---|---|---|
| `HAS_CHUNK` | Document → Chunk | `ordinal` |
| `MENTIONS` | Chunk → Mention | — |
| `RESOLVES_TO` | Mention → Person/Project/... | `score`, `method` |
| `AUTHORED_BY` | Document → Person | `role` |
| `IN_THREAD` | Document → Thread | `position` |
| `NEAR_DUPLICATE_OF` | Document → Document | `similarity` |
| `EVIDENCED_BY` | Claim → Chunk | `char_start`, `char_end` |
| `ASSERTS` | Claim → entity (subject) | — |
| `ABOUT` | Claim → entity (object) | — |
| `CONTRADICTS` | Claim ↔ Claim | `detector`, `score` |
| `SUPERSEDES` | Claim → Claim | `reason` |
| `IN_CONFLICT_SET` | Claim → ConflictSet | — |
| `WORKS_ON` / `MEMBER_OF` / `OWNS` / `BLOCKS` / `RELATES_TO` | derived, per predicate alignment | — |

**Design rule that must not be violated:** claims are nodes. Never write a fact as an
edge property (e.g. `(:Person)-[:OWNS {source:'doc'}]->(:Project)`). Every conflict,
provenance, and abstention feature depends on claims being addressable, queryable
entities in their own right.

### 7.4 `schema.cypher` (apply at bootstrap; adjust per §6 probe results)

```cypher
CREATE INDEX FOR (d:Document) ON (d.doc_id);
CREATE INDEX FOR (d:Document) ON (d.source_system);
CREATE INDEX FOR (m:Mention) ON (m.surface_norm);
CREATE INDEX FOR (p:Person) ON (p.canonical_id);
CREATE INDEX FOR (c:Claim) ON (c.claim_id);
CREATE INDEX FOR (c:Claim) ON (c.predicate);
CREATE INDEX FOR (t:Ticket) ON (t.canonical_id);
CREATE INDEX FOR (p:Project) ON (p.canonical_id);
-- if CREATE INDEX is unsupported per §6, delete this file and note the gap
-- in docs/cypher-support.md instead
```

### 7.5 `src/schema/models.py` (Pydantic — frozen, all adapters emit `Document` only)

```python
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
    declared_container: str | None = None

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

Every ingest adapter's contract is: **input = raw source records, output = `list[Document]`.**
Nothing else crosses that boundary. This is what lets ingest, resolution, conflict, and
query be built and tested independently.

### 7.6 TBox authoring — runs before Tier 2 extraction

The node/relationship labels in §7.2–7.3 are the coarse property-graph schema and stay
frozen as written. This step adds a **finer-grained terminology layer on top** — the
formal predicate vocabulary, with domain/range/cardinality — that Tier 2 extraction
validates against. This is the TBox in the classical sense: schema/terminology,
separate from the ABox (the actual claim instances Tier 2 produces).

Important scoping note: HydraDB has no DL reasoner. There is no automatic subsumption or
consistency checking. The TBox here is an explicit artifact you author and your own code
enforces at write time — not something the database entails for you.

**Do not attempt to enumerate every class and relation the corpus could contain.** Scope
the TBox against **competency questions** — the standard ontology-engineering technique
for keeping this bounded (Noy & McGuinness, "Ontology Development 101"). You already have
the competency questions: the 500 official eval questions plus the track brief's own
examples. The TBox is complete when every one of them can be phrased entirely in its
vocabulary — not when the corpus has been exhaustively catalogued.

#### 7.6.1 Check for generation scaffolding first — do this before authoring anything

EnterpriseRAG-Bench's own generation process used human-in-the-loop scaffolding: a
company overview, initiatives, an **employee directory**, and source structure, built
before noise was introduced, for a simulated company ("Redwood Inference", an AI
inference provider). If any of this scaffolding is present in the cloned
`onyx-dot-app/EnterpriseRAG-Bench` repo — check the generation-framework code paths, not
just the release download — it is close to ground truth for both the TBox (what classes
and relations the company's world actually has) and part of the ABox (the employee
directory in particular would be labelled ground truth for entity resolution, which
otherwise has no labels at all — see §9 and doc 03 §10). Search the repo for this before
spending any hours inferring the TBox from raw documents. If found, treat it as the TBox
backbone and skip most of steps 2-3 below.

#### 7.6.2 Authoring steps (budget: 4-6 hours, one person can do this)

1. Gather competency questions: all 500 official questions, the track brief's examples,
   and a stratified sample of ~100-200 documents across all nine sources (read them, do
   not skip this — the sample is what catches source-specific vocabulary the question
   set alone won't surface).
2. Enumerate classes: reconcile top-down (what the competency questions require) with
   bottom-up (what the sample and any scaffolding found in 7.6.1 actually contain).
3. Enumerate relations. For each: canonical name, domain class, range class(es),
   **functional** (at most one live value — `OWNS`, `LAUNCH_DATE`, `STATUS`) or
   **non-functional** (legitimately many — `WORKS_ON`, `MENTIONS`), and the source-system
   vocabulary that maps onto it (`assignee`/`owner`/`DRI` → `OWNS`).
4. Validate: attempt to phrase every competency question using only this vocabulary. Any
   question you can't phrase names a gap — fix it now.
5. Freeze. Two artifacts, kept in sync:

```yaml
# ontology/tbox.yaml
classes:
  Person:      {parent: null}
  Team:        {parent: null}
  Project:     {parent: null}
  Product:     {parent: null}
  Customer:    {parent: null}
  Ticket:      {parent: null}
  Meeting:     {parent: null}
  Thread:      {parent: null}
  # extend only with classes required by a competency question or the scaffolding —
  # not speculatively

relations:
  OWNS:
    domain: Person
    range: [Ticket, Project, Product]
    functional: true
    temporal: true
    source_forms:
      jira: [assignee, reporter]
      linear: [assignee]
      hubspot: [deal_owner, contact_owner]
      confluence: [page_owner, DRI]
      slack: [claimed_by]
    inverse: OWNED_BY
  LAUNCH_DATE:
    domain: Project
    range: literal(date)
    functional: true
    temporal: true
  WORKS_ON:
    domain: Person
    range: [Project]
    functional: false
  MEMBER_OF:
    domain: Person
    range: [Team]
    functional: false
  # ... complete against the competency-question checklist, not the full corpus
```

```cypher
-- materialize the TBox in the graph itself, alongside schema.cypher —
-- this makes the ontology queryable, not just documented in a config file
UNWIND $classes AS c
  CREATE (:Class {name: c.name, parent: c.parent});
UNWIND $relations AS r
  CREATE (:Relation {name: r.name, domain: r.domain, range: r.range,
                     functional: r.functional, temporal: r.temporal});
```

`src/schema/ontology.yaml` (already referenced in §4's repo layout) **is** this file —
this section defines its required contents. `doc 04 §3`'s `functional_predicates` list
and `doc 03 §9`'s alignment table are both subsumed by it; don't maintain three versions
of the same information.

## 8. Ingestion

### 8.1 Corpus

EnterpriseRAG-Bench release artifacts (not in the repo itself — download from the
latest GitHub release: `github.com/onyx-dot-app/EnterpriseRAG-Bench/releases/latest`).
Per-source slice files (`<source_type>_slice_<N>.zip`, ≤5,000 docs each) exist for
partial downloads — start with 2-3 slices per source, not the full corpus, until the
pipeline is proven end to end.

### 8.2 Adapter interface

```python
# src/ingest/adapters/base.py
from abc import ABC, abstractmethod
from src.schema.models import Document

class SourceAdapter(ABC):
    source_system: str

    @abstractmethod
    def iter_documents(self, path: str) -> Iterator[Document]:
        """Yield normalized Document records from raw source files."""
```

One file per source under `src/ingest/adapters/`. Inspect real record shapes before
writing each adapter — do not assume a schema, read a sample file first.

### 8.3 Tier 1 — structural (no LLM, full corpus)

For every `Document`:
1. Write the `Document` node.
2. Chunk the body (simple paragraph/token-window split, ~500 tokens, is sufficient).
3. Extract deterministic mentions: `@handles`, email addresses, ticket-ID patterns
   (`[A-Z]+-\d+`), `#channel` refs — regex, no model.
4. Write `AUTHORED_BY`, `IN_THREAD` edges from structured metadata (author field,
   thread/channel key) — these exist directly in the source records, no inference needed.
5. Compute SimHash, batch into LSH candidate buckets, confirm with Jaccard, write
   `NEAR_DUPLICATE_OF` edges (`src/ingest/dedupe.py`).

Writer pattern (`src/ingest/writer.py`) — **adapt based on §6 probe results**:

```python
# If MERGE works:
def write_documents(session, docs: list[Document]):
    session.run("""
        UNWIND $docs AS d
        MERGE (n:Document {doc_id: d.doc_id})
        SET n += d
    """, docs=[d.model_dump(mode="json") for d in docs])

# If MERGE is unavailable, client-side idempotency instead:
_written: set[str] = set()   # or a persisted checkpoint file/table

def write_documents_no_merge(session, docs: list[Document]):
    new = [d for d in docs if d.doc_id not in _written]
    if not new:
        return
    session.run("UNWIND $docs AS d CREATE (n:Document) SET n = d",
                docs=[d.model_dump(mode="json") for d in new])
    _written.update(d.doc_id for d in new)
```

Batch size from `INGEST_BATCH_SIZE` (default 500). **Checkpoint after every batch** —
write the last-processed offset per source to a local file. A crash at document 300,000
must resume near there, not at zero.

### 8.4 Tier 2 — semantic (LLM-backed, targeted subset)

Do **not** run this over the full corpus — see §16 for why and the budget.

Selection for enrichment:
1. A stratified random sample across all nine sources (`TIER2_SAMPLE_FRACTION` of each).
2. Documents in the graph neighbourhood of the 500 official eval questions (resolve
   question entities first via Tier 1's structural graph, then pull their neighbourhood).
3. Anything pulled in lazily at query time by `src/query/traverse.py` when it hits an
   unenriched region — cache the result permanently, same code path as (1) and (2).

For each selected document, one LLM call extracts claims as structured JSON:

```
Extract factual claims from this document as JSON.
Each claim: {"predicate": str, "subject": str, "object": str,
             "polarity": "affirm"|"negate", "confidence": 0.0-1.0,
             "evidence_span": [start_char, end_char]}
Use predicates from this list where possible: OWNS, WORKS_ON, MEMBER_OF,
LAUNCH_DATE, STATUS, REPORTS_TO, DEADLINE, BLOCKS, PRICE, HEADCOUNT.
Only extract claims explicitly stated in the text. Do not infer.
Return a JSON array. Empty array if no clear claims exist.

DOCUMENT [{source_system}, {title}]:
{body}
```

**TBox validation gate — apply before writing any claim:** check the extracted
`predicate` against `ontology/tbox.yaml` (§7.6). If the predicate isn't declared, either
map it through `source_forms` to a canonical predicate or drop the claim into a
`claims_unmapped.jsonl` side file for review — never write an untyped predicate straight
into the graph. If the predicate is declared, confirm the subject/object types are
consistent with its `domain`/`range`; log a mismatch rather than silently writing it.
This is the concrete payoff of authoring the TBox first: extraction errors get caught at
write time instead of surfacing later as bad query results with no clear cause.

Write `Claim` nodes with `EVIDENCED_BY → Chunk` and `ASSERTS`/`ABOUT` edges to
provisional entity mentions (resolved properly in the next stage). Batch through the
dual-provider router (§16), checkpointed the same way as Tier 1.

## 9. Entity resolution

Full detail in `docs/planning/03-ENTITY-RESOLUTION.md`. Pipeline summary — implement in
this order:

1. **Normalize** (`normalize.py`) — lowercase, strip honorifics, extract email/handle,
   compute soundex, split given/surname. Maintain a nickname lexicon; seed from a public
   list, then mine the corpus itself from signature blocks and email headers.
2. **Block** (`block.py`) — candidate pairs from: exact email, email local-part, handle,
   surname, soundex, given-name+team, nickname-expansion, **and graph co-membership**
   (mentions sharing a document/thread — this is the one string-only blocking misses).
3. **Score** (`score.py`) — weighted features (email/handle/name-sim/nickname/team/
   temporal/role), plus **`cooccurrence_path`** scored via batched `algo.MSpaths` with
   `pairwise: true` (hundreds of candidates in one server call), plus a **negative**
   weight for co-sentence appearance (two names in the same sentence are strong evidence
   *against* merging — implement this before clustering at scale, or clusters collapse).
4. **Cluster** (`cluster.py`) — threshold → edges → connected components, with guards:
   hard negatives always override; size cap ~12; weak-bridge detection splits a
   component joined by one coincidental edge; conflicting confirmed emails force a split.
5. **Adjudicate** (`adjudicate_llm.py`) — LLM call **only** for pairs in the uncertain
   score band (~0.45–0.72), with graph evidence counts included in the prompt. This
   should be roughly 3-8% of candidates — if it's much higher, the scorer needs tuning,
   not more LLM budget.
6. **Canonicalize** (`canonicalize.py`) — write `Person`/`Project`/etc nodes with opaque
   IDs, link every original `Mention` via `RESOLVES_TO` (never delete mentions — they're
   the alias-set evidence for the demo and the provenance chain for citations).

## 10. Conflict detection and trust function

Full detail in `docs/planning/04-CONFLICT-RESOLUTION.md`. Summary:

1. **Detect** (`detect.py`) — candidates are `Claim` pairs sharing subject+predicate
   with different objects, restricted to predicates marked `functional: true` in
   `ontology.yaml` (OWNS, LAUNCH_DATE, STATUS, DEADLINE, ...). Non-functional predicates
   (WORKS_ON, MENTIONS) legitimately take many values — excluding them is what keeps
   false-positive rate manageable.
2. **Classify** (`classify.py`) — before treating anything as a true conflict, rule out:
   temporal succession (>14 day gap, no validity overlap → `SUPERSEDES`, not a conflict),
   scope difference (different qualifiers), granularity (hierarchically related objects).
   Only the residue goes to the LLM classifier, and only the residue is a true conflict.
3. **Trust function** (`trust.py`):
   ```
   trust = 0.30·authority + 0.25·recency + 0.25·corroboration
         + 0.10·specificity + 0.05·extraction_confidence − 0.15·staleness_penalty
   ```
   - `authority`: per-predicate source-type weight from `ontology.yaml` (Confluence/Jira
     high for status fields, Slack low; invert for "who's currently on this" type facts).
   - `corroboration`: **count distinct sources after collapsing `NEAR_DUPLICATE_OF`
     clusters to one vote each**, and weight cross-system agreement above within-system
     agreement. This is the step most implementations get wrong — the corpus is
     deliberately seeded with near-duplicates to trap naive vote-counting.
   - If the top-two margin is < 0.12, do not silently pick a winner — mark
     `resolution_status = CONTESTED` and present both in synthesis (§11).
4. Persist every `ConflictSet` with its `rationale` string at ingest time — adjudicate
   once, not per query, so eval results are reproducible.

## 11. Query answering and the abstention gate

Full detail in `docs/planning/05-QUERY-ANSWERING-AND-ABSTENTION.md`. Build order:

1. **Anchor** (`anchor.py`) — resolve question entities against the graph using the
   *same* normalization code as §9 (one shared code path, question-side and corpus-side
   can never drift apart). An anchor that fails to resolve at all is your earliest and
   cheapest abstention signal — surface it, don't discard it.
2. **Plan** (`plan.py`) — classify into `LOOKUP` / `MULTIHOP` / `CONFLICT` / `AGGREGATE`
   / `TEMPORAL`. One structured-output LLM call.
3. **Traverse** (`traverse.py`) — direct claim match for `LOOKUP`; `algo.MSpaths` with
   `pairwise: true` between anchors for `MULTIHOP`; direct `ConflictSet` read for
   `CONFLICT`. Use `causal` consistency on this path (default hot path). Trigger lazy
   Tier-2 enrichment here if the neighbourhood is unenriched (bounded, cached — see §8.4).
4. **Gate** (`gate.py`) — **this file matters more than any other in the query layer.**
   ```python
   def should_abstain(signals) -> Decision:
       if signals.unresolved_anchors and not signals.claim_count:
           return Decision(True, "entity not found under any known alias")
       if signals.path_count == 0 and signals.claim_count == 0:
           return Decision(True, "entities exist but no relationship found within bound")
       if signals.max_trust < 0.35 and signals.evidence_sources < 2:
           return Decision(True, "only weak, uncorroborated evidence")
       return Decision(False, "")
   ```
   Make every abstention informative: what was searched, what near-miss entity exists
   (fuzzy-match the unresolved anchor against canonical names), not a bare "not found."
5. **Synthesize** (`synthesize.py`) — grounded generation with hard rules: only use
   provided claims, cite every statement, surface conflicts explicitly rather than
   picking silently, and re-check absence even though the gate already ran (defence in
   depth — rule 4 in the prompt, doc 05 §6).

Output contract every caller must return:

```json
{
  "question_id": "q_0142", "answer": "...", "abstained": false, "confidence": 0.81,
  "citations": [{"doc_id":"...", "source_system":"...", "title":"...",
                 "chunk_id":"...", "quote_span":[412,486]}],
  "traversal": {"anchors":[...], "path_count":3, "max_hops":3, "path_summary":"..."},
  "conflicts": [{"conflict_id":"...", "status":"RESOLVED", "winner":"...",
                 "margin":0.31, "rationale":"..."}],
  "graph_stats": {"claims_considered":14, "documents_touched":9,
                  "consistency":"causal", "latency_ms":840}
}
```

Use `strong` consistency, not `causal`, when this is called from the eval harness — see
§12.

## 12. Evaluation

Full detail in `docs/planning/07-EVALUATION-HARNESS.md`. Build `eval/run_eval.py`
resumable, writing JSONL in the benchmark's expected format
(`{"question_id", "answer", "document_ids"}`). Score per-category, not just aggregate.
Required numbers, minimum:

- Per-category accuracy against the official 500-question set.
- Abstention precision, abstention recall, and false-abstention rate — three numbers,
  not one.
- The ER ablation: run the eval with resolution disabled (every mention its own entity)
  vs enabled, report the multi-hop delta. This is the single most important number in
  the project — it's a measured claim about the track's own stated hard problem.
- A BM25 baseline over the same corpus for comparison.
- 20-30 sampled failures, categorized, written to `eval/results/error_analysis.md`.

## 13. Build milestones

Each milestone has a Definition of Done. Do not start the next until the current one's
DoD is met. Detailed hour budgets are in `docs/planning/06-IMPLEMENTATION-PLAN.md` — use
them as pacing guidance, but the DoD is what actually gates progress.

**M0 — Foundation.**
DoD: HydraDB running, round-tripped write verified, `docs/cypher-support.md` written
from the §6 probe, `schema.cypher` and `models.py` committed, repo public with license.

**M0.5 — TBox frozen.**
DoD: §7.6 checked against the cloned EnterpriseRAG-Bench repo for generation scaffolding
first; `ontology/tbox.yaml` written and materialized as `:Class`/`:Relation` nodes;
every one of the 500 official questions can be phrased in its vocabulary (spot-check at
least 50 by hand); file committed and treated as frozen going forward, same discipline as
§7's graph schema. Do not start Tier 2 extraction before this milestone's DoD is met.

**M1 — Walking skeleton.**
DoD: one real document ingested (Tier 1), one claim extracted (Tier 2, manually
triggered), one `LOOKUP` question answered end-to-end with a real citation, through the
full `anchor → plan → traverse → synthesize` path. Ugly is fine. This proves every
interface boundary works before you scale anything up.

**M2 — Ingest depth.**
DoD: all nine adapters working, full corpus structurally ingested (Tier 1) and
checkpointed, near-duplicate clusters written, node/edge counts sane and logged.

**M3 — Entity resolution.**
DoD: full pipeline (§9) run over the ingested corpus; negative co-occurrence guard
confirmed working (spot-check: no cluster contains two names that co-occur in one
sentence); alias sets inspectable via a live query; cluster size distribution has no
long tail past ~12.

**M4 — Conflict resolution.**
DoD: detection + classification + trust function (§10) run over the graph; 30
detections hand-inspected for taxonomy correctness; at least one real cross-system
contradiction identified and adjudicated with a stored rationale.

**M5 — Query completeness.**
DoD: all plan classes implemented; abstention gate passing hand-made
should-abstain / should-not-abstain test cases; lazy enrichment path working; output
contract (§11) stable.

**M6 — Evaluation and proof.**
DoD: full eval run complete with per-category scores, three abstention numbers, ER
ablation, BM25 baseline, error analysis written. Numbers may be modest — they must be
real and reproducible (`strong` consistency, resumable harness).

**M7 — Ship.**
DoD: README complete, THIRD_PARTY.md present, demo UI or CLI functional, video script
ready, all nine boxes in the planning doc's "definition of done" checked.

## 14. Explicit scope cuts — do not build these without checking in

- Fine-tuning or training any model.
- A custom embedding model or learned re-ranker.
- Multi-tenancy, auth, RBAC.
- Kubernetes deployment (the Helm chart exists in the HydraDB repo; not needed here —
  single local node is correct and sufficient).
- A second corpus (HERB) — out of scope, see planning doc 00 §6.
- A polished frontend beyond the four-element Streamlit page in doc 05 §8.
- Any feature not traceable to §1, §7, §9, §10, or §11.

## 15. Licensing

- This repo: Apache-2.0 `LICENSE` at root, from the first commit.
- HydraDB: AGPL-3.0, run as a separate process, never vendored or linked. Note this
  explicitly in `THIRD_PARTY.md`.
- EnterpriseRAG-Bench: MIT, used unmodified, cited with its arXiv ID.
- Declare any AI coding assistant used in `THIRD_PARTY.md` — permitted under hackathon
  rules, and declaring it costs nothing.

## 16. LLM providers — dual free-tier worker pattern

No paid API key is assumed. Two free, no-credit-card providers, used as two
independent worker pools so their rate limits don't share a ceiling:

| Provider | Env var | Use for |
|---|---|---|
| Google Gemini (AI Studio) | `GEMINI_API_KEY` | Claim extraction, ER adjudication, answer synthesis — better reasoning quality, 1M context |
| Groq | `GROQ_API_KEY` | High-volume simple calls — blocking-stage classification, negative-pair harvesting — fast, separate quota |

`src/llm/providers.py` implements one interface for both (`.complete(prompt, schema) →
dict`), `src/llm/router.py` load-balances across them, `src/llm/cache.py` caches by
content hash of `(prompt, model)` so a re-run or a retry never re-spends budget.

**Both providers require billing to stay disabled to keep the free tier active** —
enabling billing on a Gemini project removes free-tier access entirely rather than
supplementing it. Do not add a card to either account during the hackathon.

Budget check: Tier 2 enrichment targets a stratified sample plus question-neighbourhood
documents — on the order of a few thousand documents, not 500,000. That volume fits
comfortably inside both providers' free daily quotas run in parallel. If
`TIER2_SAMPLE_FRACTION` pushes past that, lower it rather than assuming the quota will
stretch — checkpointing (§8.4) means a lower sample now can always be extended later.
