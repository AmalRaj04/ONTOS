# 03 — Entity Resolution and Ontology Alignment

The track brief names this as the hard part: *deciding that "Sam", "@soham" and
"S. Ratnaparkhi" are one person*. This document specifies how.

**Owner:** B. **Depends on:** Tier 1 structural graph. **Budget:** ~14 hours.

---

## 1. Why this is a graph problem

The naive framing is pairwise string similarity. That framing fails on the track's own
example, and understanding *why* is the whole design.

- `"Sam"` vs `"S. Ratnaparkhi"` — string similarity near zero. First name vs surname.
- `"@soham"` vs `"Sam"` — different names entirely. `Soham` → `Sam` is a real-world
  nickname mapping no edit-distance metric recovers.
- `"@soham"` vs `"S. Ratnaparkhi"` — shares one initial. Below any usable threshold.

Every pair fails on strings alone. But the *transitive* structure is recoverable:

```
"@soham"  ──same Slack handle──▶  soham@company.com  ◀──email header──  "S. Ratnaparkhi"
    │                                                                          │
    └─── co-authors ENG-4412 with, replies in same thread as ───┐              │
                                                                ▼              │
                                                             "Sam" ◀───────────┘
                                              (signs Confluence page as "Sam R.")
```

Resolution is **connected-component discovery over an evidence graph**, where edges come
from heterogeneous signals and no single signal is sufficient. That is a traversal
problem, and it is why the track routed this to a graph database rather than a vector one.

## 2. Pipeline

```
mentions ──▶ normalize ──▶ block ──▶ candidate pairs ──▶ score ──▶ cluster ──▶ adjudicate ──▶ canonicalize
             (§3)          (§4)      (~10⁵ pairs)        (§5)      (§6)        (§7 LLM)      (§8)
```

Never compare all pairs. With ~2M mentions, all-pairs is 2×10¹² comparisons. Blocking
takes that to ~10⁵. **Blocking quality determines both your runtime and your recall
ceiling** — a true pair that never becomes a candidate can never be merged, no matter how
good your scorer is.

## 3. Normalization

Deterministic, cheap, applied to every mention at ingest:

```python
def normalize_person(surface: str) -> PersonKeys:
    s = unicodedata.normalize("NFKD", surface).strip()
    s = re.sub(r"^[@<]|[>]$", "", s)              # @soham, <soham@x.com>
    s = re.sub(r"\s+", " ", s.lower())
    s = re.sub(r"\b(mr|ms|mrs|dr|prof)\.?\b", "", s)
    return PersonKeys(
        norm       = s,
        email      = extract_email(surface),
        handle     = extract_handle(surface),      # slack/github/jira handle
        tokens     = s.split(),
        initials   = "".join(t[0] for t in s.split() if t),
        soundex    = [soundex(t) for t in s.split()],
        surname    = s.split()[-1] if " " in s else None,
        givenname  = s.split()[0] if " " in s else s,
    )
```

Keep a nickname lexicon (`sam↔samuel↔soham`, `bob↔robert`, `liz↔elizabeth`). Seed it from
a public list, then — the useful part — **mine the corpus itself**: when a signature block
reads `Soham Ratnaparkhi` and the sending address is `sam.r@company.com`, you have
harvested a corpus-specific nickname pair for free. Email headers, Slack profile
metadata and calendar invites are all nickname goldmines. This mined lexicon is a genuine
technical contribution worth thirty seconds in the video.

## 4. Blocking

A pair becomes a candidate if it shares **any** block key. Cheap, high-recall, tolerant of
false positives — the scorer's job is to kill those.

| Block key | Catches |
|---|---|
| Exact normalized email | The trivial and most reliable case |
| Email local-part | `s.ratnaparkhi@` ↔ `sratnaparkhi@` |
| Handle across systems | `@soham` in Slack ↔ `soham` in GitHub |
| Surname | `Ratnaparkhi` ↔ `S. Ratnaparkhi` |
| Soundex of any token | `Ratnaparki` ↔ `Ratnaparkhi` (typos) |
| Given name + team | `Sam` on Team Atlas ↔ `Sam R.` on Team Atlas |
| Nickname-expanded given name | `Sam` ↔ `Soham` via lexicon |
| **Co-membership in a thread** | The graph signal — see below |

That last one is the differentiator. Two mentions appearing in the same Slack thread,
email chain, or meeting transcript are *contextually* linked regardless of string
similarity — and it is retrieved by traversal, not by string index. It is what rescues
the `"Sam"` ↔ `"S. Ratnaparkhi"` pair that every string-based blocker drops.

```cypher
// graph-native blocking: mentions sharing a document or thread context
MATCH (m1:Mention)<-[:MENTIONS]-(:Chunk)<-[:HAS_CHUNK]-(d:Document)
      -[:IN_THREAD]->(t:Thread)<-[:IN_THREAD]-(d2:Document)
      -[:HAS_CHUNK]->(:Chunk)-[:MENTIONS]->(m2:Mention)
WHERE m1.mention_type = 'PERSON' AND m2.mention_type = 'PERSON'
  AND m1.mention_id < m2.mention_id
RETURN m1.mention_id, m2.mention_id, count(DISTINCT t) AS shared_threads
ORDER BY shared_threads DESC
LIMIT 50000
```

## 5. Scoring

A candidate pair gets a score in [0,1] from weighted, independently-computed features.
Keep them separable — you will tune weights on Thursday and you want to do it without
re-running extraction.

| Feature | Weight | Notes |
|---|---|---|
| `email_exact` | 0.45 | Near-decisive. Beware shared aliases (`support@`) — exclude role addresses. |
| `handle_match` | 0.25 | Cross-system handle identity |
| `name_similarity` | 0.15 | Jaro-Winkler on normalized forms |
| `nickname_link` | 0.15 | Lexicon hit, corpus-mined preferred |
| **`cooccurrence_path`** | 0.20 | **From `algo.MSpaths`.** Short paths through shared threads/tickets/meetings |
| `team_overlap` | 0.10 | Same team/project context |
| `temporal_plausibility` | 0.05 | Activity windows overlap or abut |
| `role_consistency` | 0.05 | Same title/role asserted |
| `negative_cooccurrence` | **−0.40** | **See below** |

### The negative signal is the one people forget

**Two distinct mentions appearing in the same sentence are almost never the same person.**
`"Sam handed this to Soham"` is strong evidence *against* merging. So is appearing in the
same @-mention list, the same email To: line, or the same attendee roster.

Without this, alias clusters collapse catastrophically — a single bad merge chains through
transitive closure and swallows an entire team into one node. It is the most common and
most destructive failure mode in entity resolution, and it will look, from your metrics,
like everything is fine right up until a demo question returns nonsense.

```cypher
// harvest negative pairs — run before clustering
MATCH (c:Chunk)-[:MENTIONS]->(m1:Mention), (c)-[:MENTIONS]->(m2:Mention)
WHERE m1.mention_id < m2.mention_id
  AND m1.mention_type = 'PERSON' AND m2.mention_type = 'PERSON'
  AND abs(m1.char_offset - m2.char_offset) < 200
  AND m1.surface_norm <> m2.surface_norm
RETURN m1.mention_id, m2.mention_id, count(*) AS co_sentence_count
```

### Batch scoring with `algo.MSpaths`

The co-occurrence feature is where HydraDB earns its place. Instead of one query per
candidate pair, resolve all of them together:

```python
def score_cooccurrence(driver, surfaces: list[str]) -> dict[tuple[str,str], float]:
    q = """
    CALL algo.MSpaths({
      sourceLabel: 'Mention', sourceProperty: 'surface_norm',
      sourceValues: $vals, targetValues: $vals, pairwise: true,
      relTypes: ['MENTIONS','HAS_CHUNK','IN_THREAD','AUTHORED_BY','OWNS'],
      relDirection: 'both', maxLen: 4, pathCount: 5,
      fairRelationshipVariants: true, resultLimit: 5000
    }) YIELD path RETURN path
    """
    # score = f(path_count, mean_path_length) — shorter and more numerous is stronger
    with driver.session() as s:
        return aggregate_paths(s.run(q, vals=surfaces))
```

Batch in chunks of 200–500 source values. Measure; tune `resultLimit` and `maxLen` down
if latency bites. `maxLen: 4` is the sweet spot — beyond that everything connects to
everything and the signal vanishes into the small-world property of the graph.

## 6. Clustering

Score threshold → edges → connected components, **with guards**. Naive transitive closure
is how you get a 4,000-member "person" cluster.

```python
def cluster(pairs, positives, negatives, tau=0.62):
    g = nx.Graph()
    g.add_edges_from((a, b, {"w": s}) for (a, b), s in pairs.items() if s >= tau)
    g.remove_edges_from(negatives)                  # hard negatives always win

    for comp in list(nx.connected_components(g)):
        sub = g.subgraph(comp)
        # Guard 1: cluster size. Real people rarely exceed ~12 surface forms.
        # Guard 2: conductance. A cluster joined by one weak bridge is two clusters.
        if len(comp) > 12 or has_weak_bridge(sub, min_weight=0.75):
            yield from split_by_min_cut(sub)        # or escalate to LLM (§7)
        else:
            yield comp
```

**Guards, in priority order:**
1. **Hard negatives override everything.** Never merge across a co-sentence pair.
2. **Size cap.** > 12 members ⇒ suspicious ⇒ split or escalate.
3. **Bridge detection.** If removing one edge disconnects the component and that edge is
   weak, it is two clusters joined by a coincidence.
4. **Email conflict.** Two different confirmed personal emails in one cluster ⇒ split.
   This is almost always correct and almost always catches a real error.

## 7. LLM adjudication — only at the boundary

Do **not** run an LLM over every pair. Run it only where the score sits in the uncertain
band, roughly `0.45 ≤ score < 0.72`. In practice that is 3–8% of candidates, which is the
difference between affordable and impossible.

```
You are resolving whether two name mentions in a company's internal
documents refer to the same person.

MENTION A: "S. Ratnaparkhi"
  Contexts (3 of 14):
   - [gmail, 2026-03-11] "From: S. Ratnaparkhi <sam.r@redwood.ai>"
   - [confluence, 2026-02-02] "Owner: S. Ratnaparkhi — Inference Latency workstream"
   ...
MENTION B: "@soham"
  Contexts (3 of 41):
   - [slack #eng-inference, 2026-03-11] "@soham can you take the p99 regression?"
   ...

GRAPH EVIDENCE:
  - shared threads: 12
  - shared tickets: 4
  - co-authored documents: 2
  - appear in the same sentence as distinct actors: 0
  - confirmed emails: A=sam.r@redwood.ai, B=(none)

Answer with JSON only:
{"same": true|false, "confidence": 0.0-1.0, "reason": "<20 words"}

If the evidence is genuinely insufficient, answer false with low confidence.
Do not guess from name similarity alone.
```

Two things make this prompt work. **Graph evidence is in the prompt** — the model is
adjudicating structural facts, not doing string matching you could have done yourself.
And the last line matters: without it, models resolve aggressively toward `true`, because
being asked the question implies the answer is interesting.

Persist every adjudication with its reason. `(:Person)` nodes carry `resolution_method`
and `resolution_reason`, so the demo can show *why* the system believes two names are one
person. That explainability is a judging asset — it turns a black-box merge into a legible
decision.

## 8. Canonicalization

```cypher
UNWIND $clusters AS cl
CREATE (p:Person {
  canonical_id: cl.canonical_id,
  display_name: cl.display_name,        // longest well-formed form, or email-derived
  primary_email: cl.primary_email,
  alias_count: size(cl.surfaces),
  confidence: cl.confidence,
  resolution_method: cl.method
})
WITH p, cl
UNWIND cl.mention_ids AS mid
  MATCH (m:Mention {mention_id: mid})
  CREATE (m)-[:RESOLVES_TO {score: cl.confidence, method: cl.method}]->(p)
```

**Never delete the mentions.** Every `Mention` stays, with its surface form and offset,
linked by `RESOLVES_TO`. That gives you:
- The demo query: `MATCH (p:Person {display_name:'Soham Ratnaparkhi'})<-[:RESOLVES_TO]-(m) RETURN DISTINCT m.surface` → the alias set, live, as proof.
- Reversibility. Resolution is a hypothesis; you may revise it Thursday night, and you
  cannot revise what you destroyed.
- Provenance integrity. Claims point at chunks, chunks at mentions, mentions at entities.
  Break that chain and citation breaks with it.

## 9. Ontology alignment (T-11)

> **Superseded in sequencing — see BUILD-SPEC.md §7.6.** This section originally
> described building `ontology/alignment.yaml` ad hoc during entity-resolution work.
> The current plan authors this **before** any Tier 2 extraction, competency-question
> scoped, frozen at milestone M0.5, and named `ontology/tbox.yaml`. The content and
> reasoning below (why alignment matters, the source-vocabulary mapping problem) still
> hold — only the timing and file name changed. Don't build a second alignment file here.

Beyond people, the same machinery aligns the *type system*. Nine sources describe the same
concepts with different vocabularies:

| Concept | Slack | Jira | Linear | HubSpot | Confluence |
|---|---|---|---|---|---|
| Work item | thread | Issue | Issue | Task | — |
| Grouping | channel | Epic/Project | Project/Cycle | Deal | Space |
| Person | member | reporter/assignee | assignee | contact/owner | author |
| Status | — | workflow status | state | deal stage | page status |

Alignment produces a **canonical predicate vocabulary** and maps each source's native
vocabulary onto it — `assignee`, `owner`, `responsible`, `DRI` all become `OWNS`. Ship
this as `ontology/alignment.yaml`, human-readable and hand-auditable:

```yaml
predicates:
  OWNS:
    canonical_domain: Person
    canonical_range: [Ticket, Project, Product]
    source_forms:
      jira: [assignee, reporter]
      linear: [assignee]
      hubspot: [deal_owner, contact_owner]
      confluence: [page_owner, DRI]
      slack: [claimed_by]           # inferred from "I'll take this"
    inverse: OWNED_BY
```

Two reasons to make this a config file rather than code. It is auditable — a judge can
read it and see the ontology, which is precisely what the track asks for. And it is the
cheapest possible knob for Thursday tuning.

## 10. Evaluation

You have no labelled ER ground truth. Measure anyway:

**Intrinsic (no labels needed):**
- Cluster size distribution — a long tail past 12 means over-merging.
- Singleton rate — a very high rate means under-merging, or blocking is dropping pairs.
- Email-conflict rate within clusters — should be ~0. Any nonzero value is a real bug.
- Co-sentence violations within clusters — must be exactly 0. Non-negotiable invariant.

**Extrinsic (the one that counts):**
Ablate. Run the full QA eval with resolution **off** (every surface form its own entity)
and **on**. The delta on multi-hop questions is your ER value, measured end to end.
Expect a large gap — multi-hop questions frequently require crossing an alias boundary,
and unresolved entities sever the path entirely.

**Report that ablation number in the video.** "Multi-hop accuracy goes from X to Y when
we turn entity resolution on" is a concrete, measured, defensible claim about the hardest
part of the track. It is worth more than any absolute score.

**Hand-label 50 pairs** from the uncertain band on Thursday if time permits — one person,
forty minutes — and report precision/recall on that slice. Small, honest, labelled beats
large and hand-waved.
