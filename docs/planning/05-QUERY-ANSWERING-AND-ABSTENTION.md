# 05 — Query Answering and Abstention

The layer judges actually interact with. Everything upstream exists to make this work.

**Owner:** C. **Depends on:** frozen schema (can build against fixtures before ingest
finishes). **Budget:** ~14 hours.

---

## 1. Pipeline

```
question
   │
   ├─▶ 1. ANCHOR       resolve question entities → canonical graph nodes
   │                    (fails here ⇒ likely abstention; see §5)
   ├─▶ 2. PLAN         classify: LOOKUP | MULTIHOP | CONFLICT | AGGREGATE | ABSENCE
   ├─▶ 3. TRAVERSE     execute Cypher / algo.MSpaths against a pinned snapshot
   ├─▶ 4. LAZY-FILL    if neighbourhood unenriched, extract now, cache to graph
   ├─▶ 5. ASSEMBLE     gather claims + evidence chunks + conflict sets
   ├─▶ 6. GATE         ◀── abstention decision. The most important step.
   └─▶ 7. SYNTHESIZE   grounded answer with citations, or a reasoned "not in corpus"
```

Build steps 1, 2, 3 and 6 first. Step 7 is a prompt. Step 4 is an optimization. A system
that traverses correctly and abstains correctly but writes clumsy prose scores far better
than one that writes beautifully about nothing.

## 2. Anchoring

Extract entities from the question, resolve them against canonical nodes using the same
normalization and alias machinery from doc 03 §3. Reuse it directly — one code path,
so question-side and corpus-side normalization can never drift.

```python
def anchor(question: str) -> AnchorResult:
    mentions = extract_question_entities(question)   # cheap NER or one LLM call
    anchors, unresolved = [], []
    for m in mentions:
        cands = resolve_against_graph(m)             # exact → alias → fuzzy
        if cands and cands[0].score >= 0.7:
            anchors.append(cands[0])
        else:
            unresolved.append(m)
    return AnchorResult(anchors, unresolved)
```

**An unresolved anchor is your earliest and cheapest abstention signal.** If a question
asks about "the Helios migration" and no `Project` node resolves to anything like Helios —
not by name, not by alias, not fuzzily — the answer is very probably not in the corpus,
and you know that before spending a single traversal. Do not throw that signal away; feed
it to the gate in §5.

## 3. Planning

Classify the question, then execute the matching strategy. One LLM call, structured output.

| Class | Signature | Strategy |
|---|---|---|
| `LOOKUP` | One anchor, one predicate | Direct claim match, trust-ranked |
| `MULTIHOP` | Two+ anchors, or a relation chain | `algo.MSpaths` between anchors |
| `CONFLICT` | Asks what is true / whether sources agree | Query `ConflictSet` directly |
| `AGGREGATE` | how many / list all / which of | Aggregation over claim set |
| `TEMPORAL` | as of / when did / before | Time-scoped claims, `SUPERSEDES` chain |
| `ABSENCE` | Probe for something plausibly absent | Bounded search, then gate |

Note that `ABSENCE` is rarely *stated*. The corpus's unanswerable questions look exactly
like answerable ones — that is the point of the category. So absence is not primarily a
plan class; **it is an outcome the gate decides**, and any plan can end there. Treat the
class as a weak prior only.

### Traversal patterns

```cypher
-- LOOKUP: trust-ranked claims for a subject/predicate
MATCH (c:Claim {predicate: $pred})-[:ASSERTS]->(s {canonical_id: $subject})
OPTIONAL MATCH (c)-[:IN_CONFLICT_SET]->(cs:ConflictSet)
MATCH (c)-[:EVIDENCED_BY]->(ch:Chunk)<-[:HAS_CHUNK]-(d:Document)
RETURN c, ch, d, cs
ORDER BY c.trust DESC
LIMIT 20
```

```cypher
-- MULTIHOP: connecting subgraph between anchors, in one server-side call
CALL algo.MSpaths({
  sourceLabel: 'Entity', sourceProperty: 'canonical_id',
  sourceValues: $anchor_ids, targetValues: $anchor_ids,
  pairwise: true,
  relTypes: ['ASSERTS','ABOUT','OWNS','WORKS_ON','MEMBER_OF','BLOCKS','RELATES_TO'],
  relDirection: 'both', maxLen: 4, pathCount: 8,
  fairRelationshipVariants: true, resultLimit: 200
}) YIELD path
RETURN path
```

```cypher
-- CONFLICT: the adjudication is already stored (doc 04 §6)
MATCH (cs:ConflictSet {subject_id: $subject, predicate: $pred})
      <-[:IN_CONFLICT_SET]-(c:Claim)-[:EVIDENCED_BY]->(ch:Chunk)
      <-[:HAS_CHUNK]-(d:Document)
RETURN cs.resolution_status, cs.winner_claim_id, cs.rationale,
       collect({claim: c, chunk: ch, doc: d}) AS all_claims
```

### Consistency mode

Use `causal` on the interactive path — the default hot path, no object-store freshness
round trip. Use `strong` for evaluation runs and immediately after any ingest barrier, so
scores are reproducible against a known-fresh snapshot.

```python
session.run(query, consistency="strong" if eval_mode else "causal")
```

Doing this deliberately, and being able to explain why, is a small thing that signals you
read the architecture rather than the quickstart.

## 4. Lazy extraction

If traversal lands in a neighbourhood with `Document` nodes but no `Claim` nodes, that
region is structurally ingested (Tier 1) but not semantically enriched (Tier 2).

```python
def ensure_enriched(doc_ids: list[str], budget_ms: int = 4000) -> None:
    cold = [d for d in doc_ids if not has_claims(d)]
    if not cold:
        return
    for batch in chunked(cold[:40], 10):             # bounded — never unbounded
        claims = extract_claims_llm(batch)
        write_claims(claims)                          # cached permanently
```

Bounded, cached, and warm the full question-set neighbourhood before recording the video.
The architecture is the story; a stall on camera is not.

## 5. The abstention gate

**This is the highest-value component in the project.** The track names it explicitly,
the benchmark scores it as a category, and it is where the graph structurally beats a
vector index. Get it right and you have a defensible claim no similarity-based system can
make.

### Why the graph wins here, stated precisely

A vector index returns the k nearest chunks. There is no k=0. Ask about something absent
and you get the k *least irrelevant* chunks, with cosine scores that look unremarkable —
and an LLM handed plausible-looking context will confabulate from it. The failure is
silent and confident, which is the worst combination.

A bounded traversal has a zero. `algo.MSpaths` with `maxLen: 4` between two anchors either
returns paths or it does not. An empty result over a **pinned, snapshot-consistent view**
is not "we didn't find it" — it is "within four hops of both anchors, in one consistent
view of the entire graph, no connection exists." That is evidence, and it is the kind of
evidence you can act on.

### Gate signals

```python
@dataclass
class AbstentionSignals:
    unresolved_anchors: list[str]   # entities in Q not in graph        → strong
    path_count: int                 # MSpaths result cardinality        → strong at 0
    claim_count: int                # claims matching subject+predicate → strong at 0
    max_trust: float                # best claim's trust score          → weak
    evidence_sources: int           # distinct source systems           → weak
    predicate_known: bool           # predicate in the ontology at all  → moderate
    semantic_floor: float           # best chunk similarity in region   → weak, tiebreak
```

```python
def should_abstain(s: AbstentionSignals) -> Decision:
    # Hard: an entity the corpus has never heard of
    if s.unresolved_anchors and not s.claim_count:
        return Decision(True, f"No entity matching {s.unresolved_anchors} exists "
                              f"in the corpus under any known alias.")
    # Hard: anchors exist, but nothing connects them within bound
    if s.path_count == 0 and s.claim_count == 0:
        return Decision(True, "Both entities exist, but no relationship between them "
                              "is asserted anywhere within 4 hops.")
    # Soft: something found, but too weak to assert
    if s.max_trust < 0.35 and s.evidence_sources < 2:
        return Decision(True, "Only weak, uncorroborated single-source evidence found; "
                              "not sufficient to answer.")
    return Decision(False, "")
```

### Make the abstention informative

A bare "I don't know" is a worse product than a wrong answer, and judges will feel that.
Every abstention should say what *was* found and where the trail went cold:

> **Not found in the corpus.** "Project Helios" does not appear in any of the 500,132
> indexed documents under that name or any resolved alias. The closest entities are
> *Project Helix* (47 documents, Platform team) and *Helios Analytics* (a HubSpot
> customer account, 3 documents). If you meant either, ask again and I'll answer.
>
> Searched: all 9 source systems, one consistent snapshot, 4-hop bound from every
> resolvable anchor.

That is genuinely useful. It tells the user the question was understood, the search was
real, and offers the likely repair. **The near-miss suggestion is itself a graph query** —
fuzzy-match the unresolved anchor against canonical entity names — and it turns your
abstention from a dead end into a product feature.

### Calibration

Abstention has a precision/recall tradeoff, and both failure modes are costly:
- Abstain too readily → miss answerable questions → looks weak.
- Abstain too rarely → confabulate → looks untrustworthy, which is worse.

Tune the thresholds on Thursday against the benchmark's unanswerable subset. **Report
both numbers separately** — abstention precision and abstention recall — plus the false-
abstention rate on answerable questions. Reporting one number hides the tradeoff and
looks evasive; reporting three shows you understand the problem.

## 6. Synthesis

```
You are answering a question about a company's internal knowledge base.
You have been given a set of CLAIMS retrieved from a knowledge graph.
Each claim carries provenance and a trust score.

RULES
1. Use ONLY the claims provided. Never add outside knowledge.
2. Cite every factual statement as [source_system: document_title].
3. If claims conflict, say so explicitly, present both, and state which
   is better supported and why. Do not silently pick one.
4. If the claims do not answer the question, say so plainly. Do not
   construct a plausible-sounding answer from adjacent facts.
5. Be concise. Two or three sentences unless the question needs more.

CLAIMS
{claims_with_provenance_and_trust}

CONFLICT SETS
{adjudications_with_rationale}

QUESTION
{question}
```

Rule 4 is load-bearing even though the gate already fired — defence in depth. Rule 3
turns conflicts into visible product behaviour rather than a hidden internal detail.

## 7. Output contract

Every answer returns the same structure. The UI renders it; the eval harness scores it;
the video shows it.

```json
{
  "question_id": "q_0142",
  "answer": "Sam Ratnaparkhi owns the inference-latency workstream.",
  "abstained": false,
  "confidence": 0.81,
  "citations": [
    {"doc_id": "doc:a1b2", "source_system": "confluence",
     "title": "Q3 Platform Plan", "chunk_id": "chunk:c3d4", "quote_span": [412, 486]}
  ],
  "traversal": {
    "anchors": ["person:9f2a", "project:44bd"],
    "path_count": 3, "max_hops": 3,
    "path_summary": "Sam →OWNS→ ENG-4412 →PART_OF→ Inference Latency"
  },
  "conflicts": [
    {"conflict_id": "cf:77", "status": "RESOLVED",
     "winner": "claim:aa11", "margin": 0.31,
     "rationale": "Confluence page, 3 independent sources, more recent"}
  ],
  "graph_stats": {"claims_considered": 14, "documents_touched": 9,
                  "consistency": "causal", "latency_ms": 840}
}
```

The `traversal` block is what makes the demo compelling — it is the visible proof that
an actual graph walk produced the answer rather than a similarity lookup. Render it.

## 8. The demo UI

Four hours, maximum. Streamlit or a single FastAPI page. Function over polish.

```
┌────────────────────────────────────────────────────────────┐
│  ONTOS — ask the company                          [strong] │
├────────────────────────────────────────────────────────────┤
│  > Who owns the inference latency workstream?              │
├────────────────────────────────────────────────────────────┤
│  Sam Ratnaparkhi owns the inference-latency workstream.    │
│                                                            │
│  ┌── traversal ──────────────────────────────────────────┐ │
│  │  Sam Ratnaparkhi ──OWNS──▶ ENG-4412                   │ │
│  │        └──PART_OF──▶ Inference Latency (Project)      │ │
│  │  3 paths · 3 hops · 14 claims · 9 docs · 840ms        │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                            │
│  ⚠ conflict resolved                                       │
│    Confluence (12 Jun) says Sam · Slack (2 Mar) says Priya │
│    → Sam. More recent, 3 independent sources. [details]    │
│                                                            │
│  aliases resolved: "Sam", "@soham", "S. Ratnaparkhi"       │
│                                                            │
│  sources: [confluence: Q3 Plan] [linear: ENG-4412]         │
│           [slack: #eng-inference]                          │
└────────────────────────────────────────────────────────────┘
```

Four elements earn their pixels: **the traversal path** (proves graph), **the conflict
banner** (proves adjudication), **the alias line** (proves entity resolution), **the
citations** (proves grounding). Those four are your four track requirements made visible
in one screenshot. Everything else is decoration you do not have time for.

## 9. Preloaded demo questions

Have five, rehearsed, one per capability. Pick them Thursday from real eval results —
questions you *know* work — and put them as clickable chips in the UI:

1. **Simple lookup** — baseline, proves the plumbing.
2. **Multi-hop** — three entities, a path no single document contains.
3. **Conflict** — cross-system contradiction with visible adjudication.
4. **Alias-dependent** — only answerable because ER merged the surface forms. Show the
   alias line lighting up.
5. **Absent** — the abstention, with the near-miss suggestion.

Number 5 is the one to spend the most video time on. Every team will demo a lookup. Almost
none will demo a *correct, informative refusal* — and it is the capability the track brief
calls out and the benchmark scores as its own category.
