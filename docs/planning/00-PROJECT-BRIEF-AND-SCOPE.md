# 00 — Project Brief and Scope

**Project codename:** ONTOS
**Track:** Hack Hydra — Track 01, Enterprise Context and Ontology
**Deadline:** 2026-08-20, 23:59 PT — **2026-08-21, 12:29 PM IST**
**Document owner:** team lead
**Status:** authoritative. If another doc disagrees with this one, this one wins.

---

## 1. The timeline you actually have

| Marker | Time (IST) | Hours remaining |
|---|---|---|
| Now | Tue 18 Aug, ~08:00 | ~76 |
| Feature freeze | Thu 20 Aug, 20:00 | ~12 |
| Video + form done | Fri 21 Aug, 08:00 | ~4 |
| **Hard deadline** | **Fri 21 Aug, 12:29** | **0** |

Treat the deadline as **Fri 21 Aug, 08:00 IST**. The four-hour margin is for the
thing that always goes wrong — a video upload that fails, a repo that turns out to be
private, a form field that demands something you don't have.

The nine-day build window in the rules is not available to you. The rules only require
that no commit predates **12 Aug 2026**, which is trivially satisfied. Starting on day
seven is legal. It is not a disadvantage you can spend your way out of, so the scope
below is built for ~65 working hours and not one hour more.

## 2. What we are building, in one paragraph

ONTOS ingests the EnterpriseRAG-Bench corpus — ~500,000 documents across Slack, Gmail,
Linear, Google Drive, HubSpot, Fireflies, GitHub, Jira and Confluence — and turns it into
a single queryable ontology inside HydraDB. Every fact in the graph is reified as a
**Claim** node that carries its own provenance edge back to the exact source document.
Entities that appear under many surface forms ("Sam", "@soham", "S. Ratnaparkhi") are
collapsed into one canonical node by a graph-native resolution pass. Statements that
contradict each other are not silently overwritten; they are linked by an explicit
`CONTRADICTS` edge and adjudicated at query time by a trust function over source
authority, recency and corroboration. A question-answering layer plans graph traversals
over that ontology, assembles a cited evidence set, and — critically — **returns "not in
the corpus" when traversal yields nothing**, rather than inventing an answer.

## 3. The thesis judges need to hear

The track brief says extraction is easy and resolution is hard. That is correct but
incomplete. The sharper claim, and the one ONTOS is built to demonstrate:

> A vector index **always returns k results**. A graph traversal can return the empty set.

That single asymmetry is why this problem is graph-shaped rather than similarity-shaped,
and it cascades into all four of the question types the track names:

| Track requirement | Why the graph wins |
|---|---|
| Simple lookup | Parity with vector search. No advantage claimed. |
| Multi-hop reasoning | Chained traversal. Top-k similarity cannot chain; each hop compounds the recall loss of the last. |
| Conflict resolution | Requires claim-level provenance as first-class structure. A chunk embedding has nowhere to put "who said this, when, and with what authority". |
| Knowing the answer is absent | A bounded traversal that terminates with zero paths is *evidence of absence*. Cosine similarity has no zero. |

Every architectural decision in doc 02 traces back to that table. When a judge asks "why
not just use a vector DB", this is the answer, and the abstention benchmark numbers in
doc 06 are the proof.

## 4. Scope: what is in, what is out

### In scope — non-negotiable

1. **Full-corpus structural ingest.** All ~500K documents land in HydraDB as `Document`
   nodes with real metadata edges (author, channel/thread, timestamp, source system).
   No LLM required. This is what lets you truthfully say "500,000 documents in HydraDB",
   and it is the substrate everything else traverses.
2. **Semantic extraction over a defined slice**, plus lazy extension at query time.
   See §5 — this is the single most important scoping decision in the project.
3. **Entity resolution** producing canonical `Person`, `Project`, `Team`, `Customer`,
   `Product` nodes with `ALIAS_OF` edges preserving every surface form.
4. **Reified claims with provenance.** Every extracted fact is a node, not an edge
   property. Non-negotiable — the conflict and abstention features are both impossible
   without it.
5. **Conflict detection and adjudication** with an auditable trust function.
6. **QA pipeline with a hard abstention gate.**
7. **Evaluation harness** running the official 500-question set, reporting per-category
   scores including abstention precision/recall.
8. **A demo UI** thin enough to build in four hours that shows the answer, the traversal
   path, the cited documents, and the conflict adjudication when one fires.

### Out of scope — say no to these

- **Salesforce HERB.** See §6. Not required, not worth the hours.
- Fine-tuning any model.
- A custom embedding model or a learned re-ranker.
- Multi-tenancy, auth, RBAC, or anything resembling production access control.
- A polished frontend. Function over form; the video is where you sell it.
- Distributed HydraDB deployment on Kubernetes. Single local node is correct and
  defensible. The Helm chart exists; you do not need it, and reaching for it will
  eat a day.
- Beating the EnterpriseRAG-Bench leaderboard. Explicitly out. The track judges say
  they care about "working, thoughtful products, not just benchmark scores". Score
  honestly, report honestly, and spend the marginal hour on the product instead.

## 5. The scoping decision that determines whether you finish

**You cannot run LLM extraction over 500,000 documents in 65 hours.** Assume ~1,500
tokens per doc, batched, on a cheap model: that is on the order of 750M input tokens.
Cost aside, the wall-clock and rate limits alone sink it. Any plan that requires it is a
plan that fails at hour 50.

The resolution is a **two-tier graph**, and it is not a compromise — it is a better
architecture that happens to also be affordable:

**Tier 1 — Structural, whole corpus, no LLM.**
Every document becomes a node. Authorship, thread membership, channel, repo, ticket
linkage, timestamps, explicit @-mentions, email headers, and ticket-ID references
(`ENG-4412`, `#proj-atlas`) are all extractable with deterministic parsing. This alone
produces a dense, genuinely useful graph over the entire corpus. Roughly 6 hours of work,
zero model cost, and it is the layer that makes the "500K documents" claim honest.

**Tier 2 — Semantic, targeted, LLM-extracted.**
Claims, typed entity relations and contradiction candidates, extracted over:
- a stratified random sample across all nine sources (calibration and honesty),
- plus the neighbourhoods that Tier 1 traversal identifies as relevant to the
  question set,
- plus **lazy on-demand extraction at query time** for any neighbourhood not yet
  enriched, cached back into the graph permanently.

The lazy path is the intellectually honest version of "we didn't process everything",
and it is architecturally *correct*: extraction is expensive, traversal is cheap, so let
cheap traversal decide where to spend the expensive operation. Say exactly this in the
README and the video. Judges respect a stated, reasoned limit far more than a vague
implication that you processed everything.

**Precompute enough that the demo never waits.** Warm the cache over the full question
set's traversal neighbourhoods before you record. Lazy extraction is the story; a
thirty-second pause on stage is not.

## 6. Do you need both datasets? No.

**Use EnterpriseRAG-Bench. Skip HERB.**

EnterpriseRAG-Bench is the dataset the track was written against — same nine sources,
same ~500K scale, same named noise characteristics (misfiled documents, near-duplicates,
conflicting information), same question taxonomy down to the abstention category. The
track brief is close to a paraphrase of its abstract.

HERB is a different benchmark: ~39K artifacts, a different source mix, generated by a
different pipeline for multi-hop deep search. Good work, wrong shape for this track.

The rules do not require either. They require that HydraDB does real work. Adding a
second corpus costs you a full ingest adapter set, a second schema mapping and a second
eval harness, in exchange for a bullet point. **If you find yourself with eight spare
hours on Thursday — and you will not — HERB becomes a generalization claim: "the same
ontology builder, pointed at a structurally different corpus, unmodified."** That is a
strong claim. It is a stretch goal, and it is the first thing to cut.

Confirm in the Hack Hydra Discord that dataset choice is free — the FAQ has a question
titled "Do I have to use the suggested datasets?" whose answer was not visible in the
page text you have. Ask early; it costs one message.

## 7. Licensing — read this before your first commit

**HydraDB is AGPL-3.0.** This has real consequences and most teams will get it wrong.

- **Run HydraDB as a separate process** (its own container or a `cargo run` node) and
  talk to it over Bolt or HTTP. Your code is then an independent client program
  communicating with an AGPL service across a process boundary.
- **Do not vendor, fork, statically link or copy HydraDB source into your application
  binary.** That is the path where AGPL's copyleft has a strong argument for reaching
  your code.
- License **your** repository Apache-2.0 or MIT, and add a `NOTICE`/`THIRD_PARTY.md`
  stating plainly: ONTOS is a client of HydraDB; HydraDB is AGPL-3.0; ONTOS does not
  incorporate or modify HydraDB source.
- If you *do* patch HydraDB — a bug fix, a missing Cypher feature — keep it in a
  **separate fork repo under AGPL-3.0**, upstream it as a PR (the maintainers explicitly
  welcome PRs, and this is a strong signal to judges), and depend on it as an external
  service. Do not mix it into your submission repo.

The rules disqualify submissions with no open-source license. They do not disqualify you
for getting the interaction with AGPL wrong — but a judging panel from the company that
published that AGPL repo will notice either way.

*This is engineering guidance on how to structure the project, not legal advice. If the
licensing outcome matters to you beyond the hackathon, get a real opinion.*

## 8. Team allocation

For a team of four, the parallel seams are clean and the interfaces are narrow:

| Owner | Surface | Depends on |
|---|---|---|
| **A — Ingest** | Nine source adapters, Tier 1 structural graph, batched `UNWIND` writer | Schema frozen (doc 02 §4) |
| **B — Resolution** | Entity resolution, conflict detection, trust function | A's canonical Document/Mention nodes |
| **C — Query** | Planner, traversal execution, evidence assembly, abstention gate | Schema frozen; can stub against fixtures |
| **D — Eval + shipping** | Harness, metrics, demo UI, README, video, form | Everyone; owns the deadline |

**Freeze the graph schema by hour 8.** It is the only hard coupling between the four
workstreams, and every hour it stays fluid is an hour three people spend guessing. Ship
`schema.cypher` and the Pydantic models early, then treat changes to them as
breaking-change PRs requiring the whole team's assent.

If you are solo: cut the demo UI to a CLI, cut lazy extraction to precomputed-only, and
follow the solo track marked in doc 05.

## 9. Definition of done

The project is done when, and only when, all of these are simultaneously true:

- [ ] A judge can clone the public repo and follow the README to a working system.
- [ ] The system answers a multi-hop question with a visible traversal path and citations.
- [ ] The system correctly abstains on a question whose answer is absent, and says why.
- [ ] The system surfaces a real contradiction from the corpus and shows its adjudication.
- [ ] Entity resolution demonstrably merges at least one alias cluster you did not
      hand-pick, and the graph exposes the alias set.
- [ ] `eval/results.json` contains real numbers from the official 500-question set.
- [ ] The repo has an open-source license, a `THIRD_PARTY.md`, and no commits before
      12 Aug 2026.
- [ ] The demo video is ≤ 3:00, publicly watchable without a login, and covers the four
      required points.
- [ ] The submission form is filed.

Nine boxes. Six of them are not code. Budget accordingly — see doc 05.
