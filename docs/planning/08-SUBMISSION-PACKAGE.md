# 08 — Submission Package

Everything non-code required to submit. **Owner: D, from hour 0.**

Six of the nine boxes in the definition of done are not code. Teams lose here — not
because the work is hard, but because it gets deferred to hour 62 and then there is no
time. Start the README and the video script on Tuesday.

---

## 1. README template

The judges' stated need: *"Judges need to be able to open the repo and understand what you
built."* Optimize for a tired person reading at speed.

````markdown
# ONTOS — an enterprise ontology on HydraDB

Hack Hydra 2026 · Track 01: Enterprise Context and Ontology

ONTOS turns ~500,000 noisy enterprise documents — Slack, Gmail, Linear, Google Drive,
HubSpot, Fireflies, GitHub, Jira, Confluence — into a single queryable ontology in
HydraDB, then answers questions over it with citations, explicit conflict adjudication,
and a calibrated ability to say **"that isn't in here."**

[3-min demo video](LINK) · [Architecture](docs/architecture.md) · [Results](eval/results/)

---

## What makes this different

A vector index always returns k results. A graph traversal can return the empty set.

That asymmetry is the whole design. Every fact is a **reified `Claim` node** with an
edge back to the exact source chunk, so ONTOS can tell you not just what it believes
but why, who said it, when, and who disagrees.

| Capability | How |
|---|---|
| **Entity resolution** | "Sam", "@soham", "S. Ratnaparkhi" → one node, via graph co-occurrence + batch `algo.MSpaths` scoring |
| **Conflict adjudication** | Contradictions persist as linked claims; a trust function over authority, recency and independent corroboration picks a winner and shows its reasoning |
| **Multi-hop reasoning** | Bounded traversal between question anchors |
| **Knowing what it doesn't know** | An empty path set over a snapshot-consistent view is *evidence of absence* |

## Results

| Category | ONTOS | BM25 baseline |
|---|---|---|
| Simple lookup | — | — |
| Multi-hop | — | — |
| Conflict resolution | — | — |
| **Unanswerable (correct abstention)** | — | — |

Abstention precision — · recall — · false-abstention rate —
Entity resolution ablation: multi-hop accuracy — → — with ER enabled.

Full numbers, ablations and an honest error analysis: [`eval/results/`](eval/results/).

**Corpus coverage.** 500,132 documents structurally indexed. N semantically enriched
ahead of time; the remainder enriched lazily on first traversal and cached. We did not
LLM-process all 500K documents and do not claim to — see [docs/coverage.md](docs/coverage.md).

## Quickstart

```bash
git clone https://github.com/<you>/ontos && cd ontos
cp .env.example .env          # add your LLM API key
docker compose up -d          # HydraDB + MinIO
make ingest SLICES=5          # ~10 min for a 5-slice subset
make serve                    # http://localhost:8501
```

Full corpus ingest: `make ingest-all` (~4h). Detailed setup: [docs/setup.md](docs/setup.md).

## How HydraDB is used

**ONTOS has no second store.** The graph is not an index over a document database — it
is the database. Documents, chunks, mentions, entities, claims, evidence links and
conflict sets all live in HydraDB, and every answer is the return value of a traversal.

Specifically:

- **`algo.MSpaths` with `pairwise: true`** batch-scores hundreds of entity-resolution
  candidate pairs server-side in one call, instead of client-side query fan-out.
  This is what makes resolution tractable at 500K documents.
- **Bounded variable-length traversal** answers multi-hop questions that no single
  document contains.
- **Snapshot-consistent reads** make abstention trustworthy: an empty result means we
  searched one consistent view of the whole graph, not a partial one.
- **`strong` and `causal` consistency used deliberately** — `strong` after ingest
  barriers and for reproducible eval runs, `causal` on the interactive path.
- **Object-store durability** means the query tier is disposable. Kill the node,
  restart with an empty cache, and the graph is intact — no re-ingest.
  Demonstrated at 2:20 in the video.

Without HydraDB, four things break rather than degrade: batch ER path scoring,
multi-hop traversal, claim-level provenance, and calibrated abstention.

## Architecture

[diagram]

- [`docs/architecture.md`](docs/architecture.md) — full design
- [`docs/ontology.md`](docs/ontology.md) — schema, node and edge types
- [`docs/cypher-support.md`](docs/cypher-support.md) — what we verified in HydraDB's
  OpenCypher subset, and what we worked around
- [`docs/coverage.md`](docs/coverage.md) — exactly what was processed

## Limitations

We would rather you read these from us than discover them.

- Semantic enrichment is not exhaustive across 500K documents; the lazy path covers the
  gap on demand, with a first-query latency cost.
- The trust function's authority weights are hand-set priors, not learned.
- Entity resolution is evaluated intrinsically plus on 50 hand-labelled pairs; there is
  no full ER ground truth in the benchmark.
- [others, honestly]

## Team

| Name | Contribution |
|---|---|

## License and attribution

ONTOS is licensed under Apache-2.0 (see [LICENSE](LICENSE)).

**HydraDB is AGPL-3.0.** ONTOS runs HydraDB as a separate service and communicates over
Bolt and HTTP. It does not incorporate, link, or modify HydraDB source. Full third-party
attribution — datasets, libraries, models — in [THIRD_PARTY.md](THIRD_PARTY.md).

Dataset: [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench)
by Onyx (MIT), arXiv:2605.05253.
````

The **Limitations** section is not a weakness. Every judge knows a 65-hour project has
gaps; the only question is whether you know where they are.

## 2. Demo video script — 2:50

Required coverage: the problem, what you built, a working demo, and how you used the
HydraDB repo and why it matters. All four must appear or you have failed a stated
requirement.

Anything past 3:00 may not be reviewed. **Record to 2:50.**

| Time | Content | Screen |
|---|---|---|
| 0:00–0:15 | "Half a million documents across nine systems. Misfiled, duplicated, contradicting each other. Ask it a question and a vector database always gives you an answer — even when there isn't one." | Corpus scale, then a confident wrong answer from a naive RAG baseline |
| 0:15–0:30 | "ONTOS turns that into one ontology in HydraDB. Every fact is a node with a link to the document it came from." | Schema diagram, 3 seconds, then move on |
| 0:30–1:00 | **Entity resolution.** "The hard part isn't extraction. It's knowing that Sam, @soham and S. Ratnaparkhi are one person — three names with no string similarity, connected only through the graph." Show the merge, show the alias set, show the evidence. | Live query, alias list, `algo.MSpaths` co-occurrence |
| 1:00–1:30 | **Multi-hop.** A question no single document answers. Show the traversal path rendered. "This path crosses four documents in three systems." | Live query, path visible |
| 1:30–2:00 | **Conflict.** "Two sources disagree about the launch date. We don't pick silently." Show both claims, the trust reasoning, the verdict. | Live query, conflict banner |
| 2:00–2:20 | **Abstention.** "Now something that isn't in the corpus." Show the informative refusal and the near-miss suggestion. "A vector index can't do this — cosine similarity has no zero. A bounded traversal does." | Live query, refusal, side-by-side with baseline confabulating |
| 2:20–2:40 | **HydraDB.** "Object storage is the source of truth, query nodes are disposable." `kill -9`. Restart. Empty cache. Same answer. "We didn't re-ingest anything." | Terminal, then the same query |
| 2:40–2:50 | Numbers + repo link. "Multi-hop goes from X to Y with entity resolution on. Everything's in the repo, including what doesn't work." | Results table |

**Production notes.**

- Every demo query pre-warmed and rehearsed. No live extraction on camera.
- Record system audio + a clean voice track. Bad audio reads as unserious.
- **Cut all waiting.** Nobody needs to watch a query run.
- Real terminal and real UI, not slides. The whole point is that it works.
- Unlisted YouTube is fine per the rules, as long as judges can watch without requesting
  access. **Verify that logged out, on a phone, on mobile data.**
- The abstention beat at 2:00 is your differentiator. Almost every other submission will
  demo a lookup. Very few will demo a correct, informative refusal.

## 3. Submission form answers

The form asks for these. Draft them Wednesday, not Friday.

**Project name.** ONTOS

**Short description** (~2 sentences)
> ONTOS builds a queryable ontology from ~500,000 noisy enterprise documents across nine
> systems in HydraDB, resolving entities across inconsistent surface forms and
> adjudicating contradictory statements with visible reasoning. It answers multi-hop
> questions with citations — and correctly says "not in the corpus" when the answer
> isn't there.

**Problem being addressed**
> Enterprise knowledge is scattered across nine systems that describe the same people,
> projects and decisions in incompatible ways. Documents are misfiled, duplicated, and
> flatly contradict each other. Vector RAG cannot chain reasoning across documents,
> cannot adjudicate contradictions because embeddings have nowhere to carry provenance,
> and — worst for real use — always returns k results, so it confabulates confidently
> when the answer simply is not there.

**What you built**
> A full ingest-to-answer pipeline. Nine source adapters produce a structural graph over
> the entire corpus. LLM extraction produces reified Claim nodes, each linked to the
> exact source chunk. Entity resolution collapses surface forms into canonical entities
> using graph co-occurrence evidence batch-scored through HydraDB's `algo.MSpaths`.
> Contradictions persist as linked claims and are adjudicated by a trust function over
> source authority, recency and independent corroboration. A query layer plans
> traversals, assembles cited evidence, and applies an abstention gate that treats an
> empty bounded traversal as evidence of absence.

**How it uses the HydraDB OSS repo**
> HydraDB is the only datastore; there is no shadow vector index. `algo.MSpaths` with
> `pairwise: true` batch-scores entity-resolution candidates server-side. Bounded
> variable-length traversal answers multi-hop questions. Snapshot-consistent reads make
> abstention trustworthy. We use `strong` consistency for reproducible evaluation runs
> and `causal` on the interactive path. Object-store durability means we restart query
> nodes with an empty cache and lose nothing — demonstrated in the video.

**Tech stack**
> HydraDB (OpenCypher over Bolt 5.x via the Neo4j Python driver), Python 3.12, MinIO,
> Docker Compose, [LLM], Streamlit, datasketch (MinHash/LSH), networkx.

**Team members and contributions** — specific, per person. "Helped with backend" is a
wasted field.

**Deployed link** — if you have one. Not required. Do not risk a broken link for it; a
404 on a form field is a bad first impression.

## 4. THIRD_PARTY.md

```markdown
# Third-party attribution

## Data
- **EnterpriseRAG-Bench** — Onyx (onyx-dot-app), MIT. arXiv:2605.05253.
  ~500K synthetic enterprise documents, 500 questions. Used unmodified.

## Software
- **HydraDB** — AGPL-3.0. Run as a separate service over Bolt/HTTP.
  Not incorporated, linked, or modified.
- **neo4j-python-driver** — Apache-2.0
- **datasketch** — MIT · **networkx** — BSD-3 · **Streamlit** — Apache-2.0
- [complete list]

## Models
- [provider/model] for claim extraction, entity adjudication, and answer synthesis.

## AI coding assistants
- [tool] was used during development. Permitted under the hackathon rules.
```

Declare AI assistant use. The rules permit it explicitly. Declaring it costs nothing and
omitting it looks like concealment if it comes up.

## 5. Final checklist — Thursday 20:00 IST, read aloud

**Repository**
- [ ] Public — verified logged out, on a phone, on mobile data
- [ ] `LICENSE` at root
- [ ] First commit ≥ 12 Aug 2026 (`git log --reverse | head`)
- [ ] README complete, all links resolve
- [ ] `THIRD_PARTY.md` present
- [ ] `.env.example`, no real secrets committed (`git log -p | grep -i "api[_-]key"`)
- [ ] Fresh clone → quickstart → works, on a clean machine

**Video**
- [ ] ≤ 3:00, ideally 2:50
- [ ] Covers: problem, what you built, working demo, HydraDB usage and why
- [ ] Watchable logged out without an access request
- [ ] Audio clear

**Form**
- [ ] Every field filled
- [ ] Repo and video links pasted and each one clicked to verify
- [ ] Team members and individual contributions listed
- [ ] Submitted before **21 Aug 12:29 IST**
- [ ] Confirmation screenshotted

**Content**
- [ ] Demo shows ER, multi-hop, conflict, and abstention
- [ ] `eval/results/` has real numbers
- [ ] Error analysis published
- [ ] README §7 answers "what would this lose without HydraDB"

## 6. The last hour

Do not touch code. Read the README as a stranger would. Click every link. Watch the video
end to end once. Submit with time to spare — the form closes on time and late entries are
not accepted unless an extension is announced.

Then post in Discord that you shipped. Judging runs through 24 Aug; winners are announced
after.
