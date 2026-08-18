# 04 — Conflict Detection and Resolution

The track's second named hard problem: *figuring out which of two contradictory statements
to trust*. The corpus is deliberately seeded with conflicting information, so this is not
a hypothetical — there are planted contradictions waiting for you, and the question set
tests whether you find them.

**Owner:** B. **Depends on:** reified `Claim` nodes (doc 02 §4.4). **Budget:** ~8 hours.

---

## 1. The principle

**Never overwrite. Never silently pick. Always adjudicate visibly.**

The tempting shortcut is last-write-wins: newest claim replaces older, done. It is wrong
in ways that matter here.

- Recency is not authority. A stale Slack aside from yesterday does not outrank a
  Confluence page updated last week and reviewed by three people.
- You destroy the audit trail. The question "who *used to* own this?" becomes
  unanswerable, and the corpus contains temporal questions.
- You cannot show your reasoning — and showing reasoning is most of the demo.
- Some conflicts are not conflicts at all. Two owners at different times is a *timeline*,
  not a contradiction. Collapsing it loses real information.

Instead: both claims persist, linked by `CONTRADICTS`, grouped into a `ConflictSet`, with
adjudication stored as an explicit, inspectable decision.

## 2. Taxonomy — not every disagreement is a contradiction

Classify before adjudicating. Getting this wrong produces false conflicts that make your
system look worse than a naive one.

| Type | Example | Handling |
|---|---|---|
| **Temporal succession** | Owner was Priya in Jan, Sam in June | **Not a conflict.** Emit `SUPERSEDES`. Answer is time-scoped. |
| **Scope difference** | "Latency is 200ms" (p50) vs "800ms" (p99) | **Not a conflict.** Different qualifiers. Detect and separate. |
| **Granularity** | "Owned by Platform team" vs "owned by Sam" | **Not a conflict.** Sam is in Platform. Hierarchy, not contradiction. |
| **True factual conflict** | Launch date "Sept 15" vs "Oct 1", same scope, same time | **Conflict.** Adjudicate. |
| **Negation** | "Ship with feature X" vs "X was cut" | **Conflict**, if not temporally ordered. |
| **Stale duplicate** | Same claim in five near-dup docs, one edited | Dedupe first (doc 02 §6.2), then evaluate. |

The first three account for most raw detections. **A system that reports them as
contradictions is noisier than one that reports nothing.** Spend real effort on the
classifier — the discrimination is the interesting part, and it is what separates you
from a team that just diffs values.

## 3. Detection

Two-stage: cheap structural candidate generation, then LLM confirmation on survivors.

### Stage 1 — structural candidates (free)

Two claims are candidates when they share a subject and predicate but differ in object:

```cypher
MATCH (c1:Claim)-[:ASSERTS]->(s)<-[:ASSERTS]-(c2:Claim)
WHERE c1.predicate = c2.predicate
  AND c1.claim_id < c2.claim_id
  AND coalesce(c1.object_id, c1.object_literal)
   <> coalesce(c2.object_id, c2.object_literal)
  AND c1.predicate IN $functional_predicates
RETURN c1, c2, s
```

`$functional_predicates` is the key list — predicates that should hold **at most one
value at a time**: `OWNS`, `LAUNCH_DATE`, `STATUS`, `REPORTS_TO`, `PRICE`, `HEADCOUNT`,
`DEADLINE`. Non-functional predicates (`MENTIONS`, `PARTICIPATED_IN`, `WORKS_ON`)
legitimately take many values and must be excluded, or you drown in false positives.

> **File name/timing updated — see BUILD-SPEC.md §7.6.** Declare functionality in
> `ontology/tbox.yaml` (formerly described here as `ontology/alignment.yaml`), authored
> upfront at milestone M0.5 rather than during conflict-detection work. Same content,
> earlier and frozen.

Declare functionality in `ontology/alignment.yaml`:

```yaml
predicates:
  OWNS:
    functional: true
    temporal: true       # value legitimately changes over time
  LAUNCH_DATE:
    functional: true
    temporal: true
  WORKS_ON:
    functional: false    # a person works on many projects
```

Also catch polarity conflicts (`polarity: affirm` vs `negate` on the same triple) and
numeric conflicts outside tolerance.

### Stage 2 — classification

Route survivors through the taxonomy. Cheap rules first, LLM only where rules are
inconclusive:

```python
def classify(c1: Claim, c2: Claim, ctx: GraphContext) -> ConflictType:
    if predicate_is_temporal(c1.predicate):
        if c1.asserted_at and c2.asserted_at:
            gap = abs((c1.asserted_at - c2.asserted_at).days)
            if gap > 14 and not overlapping_validity(c1, c2, ctx):
                return ConflictType.TEMPORAL_SUCCESSION      # SUPERSEDES, not conflict
    if c1.qualifiers != c2.qualifiers:
        return ConflictType.SCOPE_DIFFERENCE
    if ctx.is_hierarchically_related(c1.object_id, c2.object_id):
        return ConflictType.GRANULARITY
    return llm_classify(c1, c2, ctx)                          # only the residue
```

The `is_hierarchically_related` check is a graph traversal — "is Sam a member of the
Platform team?" — and it is a nice small example of the graph resolving something a
text-only pipeline cannot.

## 4. The trust function

Once a true conflict is confirmed, adjudicate. Score each claim; highest wins; the margin
determines confidence and whether you present a resolution at all.

```
trust(claim) = w_a·authority + w_r·recency + w_c·corroboration
             + w_s·specificity + w_e·extraction_confidence
             − w_d·staleness_penalty
```

### Authority — source-type prior

Not all sources are equal, and the corpus's nine source types have genuinely different
epistemic weight for enterprise facts:

| Source | Weight | Why |
|---|---|---|
| Confluence | 0.95 | Reviewed, maintained, intended as canonical |
| Google Drive (docs) | 0.85 | Deliberate artifacts, often reviewed |
| Jira / Linear | 0.85 | Structured fields are system-of-record for status/ownership |
| GitHub | 0.80 | Code and PRs are ground truth for technical claims |
| HubSpot | 0.80 | System of record for customer/deal facts |
| Gmail | 0.65 | Deliberate but unreviewed; commitments live here |
| Fireflies | 0.55 | Verbatim speech — accurate transcription of tentative statements |
| Slack | 0.45 | Fast, informal, frequently superseded within the hour |

**Make this per-predicate, not global.** A Slack thread is a poor source for a launch
date and an excellent one for "who is currently looking at the p99 regression". A Jira
status field beats a Confluence page for ticket state, and loses to it for architecture.
A flat table is a defensible v1; a predicate-conditioned table is a better one, and it is
a twenty-line change.

### Recency

Exponential decay on `asserted_at`, half-life ~90 days, tuned per predicate — deadlines
decay fast, org structure slowly. **Use assertion time, not ingest time.** A meeting
transcript from March asserts a March fact regardless of when you indexed it.

### Corroboration — the near-duplicate trap

```cypher
MATCH (c:Claim {claim_id: $cid})-[:EVIDENCED_BY]->(:Chunk)<-[:HAS_CHUNK]-(d:Document)
OPTIONAL MATCH (d)-[:NEAR_DUPLICATE_OF*0..]-(dup:Document)
WITH c, collect(DISTINCT coalesce(dup.dedupe_cluster_id, d.doc_id)) AS clusters,
        collect(DISTINCT d.source_system) AS systems
RETURN size(clusters) AS independent_supports, size(systems) AS distinct_systems
```

**Count independent sources, not documents.** The corpus is seeded with near-duplicates
precisely so that naive counting inflates the wrong answer. Collapse duplicate clusters
to one vote, and weight *cross-system* agreement higher than within-system — three
Confluence pages that copy each other are one source; a Confluence page plus a Jira field
plus a PR description are three.

This is the single most important line in the trust function and the one a careless
implementation gets wrong.

### Specificity

A claim with qualifiers (`"p99 latency in the EU region after the March rollout"`) beats a
bare one (`"latency is fine"`). Proxy: count of qualifier bindings plus named-entity
density in the evidence chunk.

### Staleness penalty

If claim A is superseded by a later claim on the same subject/predicate — even one not in
this conflict set — penalize A. This is where `SUPERSEDES` edges pay off.

### Weights

Start here, tune Thursday against the conflict-resolution question category:

```yaml
trust_weights:
  authority: 0.30
  recency: 0.25
  corroboration: 0.25
  specificity: 0.10
  extraction_confidence: 0.05
  staleness_penalty: 0.15
resolution:
  min_margin: 0.12          # below this, present both rather than pick
  min_winner_trust: 0.40    # below this, abstain entirely
```

## 5. Presenting the resolution

**`min_margin` is the honesty knob and it should be generous.** When two claims score
within 0.12 of each other, the correct answer is not a coin flip presented as fact — it is:

> **Conflicting information found.** The Q3 planning doc (Confluence, updated 12 Jun,
> corroborated by the Linear epic) gives the launch date as **15 Sept**. A later Slack
> message from the PM (3 Jul) says **1 Oct**. These are close in trust — the Confluence
> page is more authoritative, the Slack message is more recent and comes from the
> accountable owner. Both are shown below; the Confluence date is more likely to be the
> planned-of-record value, but this looks like a live disagreement rather than a settled
> fact.
>
> `[Confluence: Q3 Launch Plan §2]` `[Slack #proj-atlas, 3 Jul]`

That paragraph is worth more to a judge than a confident wrong answer, and it is worth
more to a *user* too. Enterprise knowledge systems that state contested facts flatly are
actively dangerous; the ability to say "your organisation disagrees with itself about
this, here's who and when" is the actual product.

Say exactly that in the video. It reframes conflict handling from a benchmark line-item
into a product insight, which is what J-03 and J-05 reward.

## 6. Persisting adjudications

```cypher
CREATE (cs:ConflictSet {
  conflict_id: $cid, predicate: $predicate, subject_id: $subject,
  conflict_type: $type, resolution_status: $status,
  winner_claim_id: $winner, margin: $margin, rationale: $rationale,
  resolved_at: datetime()
})
WITH cs
UNWIND $claim_ids AS claim_id
  MATCH (c:Claim {claim_id: claim_id})
  CREATE (c)-[:IN_CONFLICT_SET]->(cs)
```

`resolution_status ∈ {RESOLVED, CONTESTED, TEMPORAL_SEQUENCE, INSUFFICIENT_EVIDENCE}`.

Adjudicate **once at ingest**, not per query. The query layer reads the stored decision.
This keeps latency down and makes results reproducible across eval runs — which matters,
because a demo that adjudicates differently on the second run is a demo that looks broken.

## 7. Demo query

Have this ready to run live. It finds your most compelling planted conflict:

```cypher
MATCH (cs:ConflictSet)<-[:IN_CONFLICT_SET]-(c:Claim)-[:EVIDENCED_BY]->(:Chunk)
      <-[:HAS_CHUNK]-(d:Document)
WHERE cs.resolution_status IN ['RESOLVED','CONTESTED']
WITH cs, collect(DISTINCT d.source_system) AS systems, count(DISTINCT c) AS n
WHERE size(systems) >= 2 AND n >= 2
RETURN cs.conflict_id, cs.predicate, cs.resolution_status,
       cs.rationale, systems, n
ORDER BY n DESC LIMIT 10
```

Pick the one that crosses the most source systems — cross-system conflicts are the most
visually convincing, because they are exactly what no single-tool search could have
surfaced. Rehearse it. Know which one you are going to show.

## 8. Evaluation

The official question set has a conflict-resolution category — that is your primary
metric. Beyond it:

- **Conflict precision.** Sample 30 detected conflicts, hand-check that they are genuine
  contradictions rather than scope/temporal/granularity artifacts. Report the number
  honestly. Low precision here is more damaging than low recall, because a system that
  cries wolf is one nobody trusts.
- **Taxonomy distribution.** How many detections fall into each class in §2? A healthy
  system finds far more temporal successions than true conflicts. If your true-conflict
  count dominates, your classifier is broken.
- **Adjudication agreement.** On the questions where the gold answer picks a side, does
  your trust function pick the same one? This is directly measurable against the benchmark
  and it is the number to quote.
