# 06 — Implementation Plan (65 Hours)

Wall-clock plan from Tue 18 Aug ~08:00 IST to Fri 21 Aug 08:00 IST, against a hard cutoff
of Fri 21 Aug 12:29 IST.

**The plan is built so that a submittable system exists at hour 40.** Everything after
that is improvement on a thing that already works. If you find yourself at hour 45 with
nothing submittable, the plan has failed and you should be cutting, not building.

---

## Ground rules

1. **Ship a walking skeleton by hour 16.** End-to-end: one document ingested, one claim
   extracted, one question answered, one citation rendered. Ugly is fine. The seam
   between four workstreams is where projects die, and the only way to find those seams
   is to run the whole pipeline early.
2. **Feature freeze Thu 20:00 IST.** After that: tuning, docs, video, submission. No new
   features. None. The instinct to add one more thing at hour 60 is how teams miss
   deadlines.
3. **Commit constantly.** Small, frequent, honestly dated. Judges read commit history and
   a healthy log of real incremental work is itself evidence of legitimate authorship.
4. **The `main` branch always runs.** Broken `main` at hour 60 with four people merging
   is unrecoverable.

---

## Phase 0 — Foundation (hours 0–8, Tue 08:00–16:00)

Everyone, together, in one room or one call. Do not parallelize yet.

| # | Task | Owner | Notes |
|---|---|---|---|
| 0.1 | Build HydraDB on **every** machine | All | Rust 1.91+, libcypher-parser, GraphBLAS. Slowest step. Start it and read docs while it compiles. |
| 0.2 | `just native-check` + `just smoke` green everywhere | All | Do not proceed past a red smoke test. |
| 0.3 | Local node up, round-tripped write via HTTP **and** Bolt | A | `RUST_MIN_STACK=33554432`. Non-negotiable. |
| 0.4 | **Run the Cypher probe suite** (doc 02 §2) | C | `MERGE`? indexes? Write results to `docs/cypher-support.md`. Blocks everything. |
| 0.5 | Fresh public repo, Apache-2.0 LICENSE, README skeleton | D | First commit today = S-03 satisfied trivially. |
| 0.6 | Download EnterpriseRAG-Bench release artifacts | A | Per-source slices (≤5,000 docs each) exist for partial download — start with a few slices, not the full 500K. |
| 0.7 | Inspect real record shapes for all 9 sources | A | 30 min. Do not write adapters against assumed schemas. |
| 0.8 | **Freeze the graph schema** | All | Doc 02 §4. `schema.cypher` + `models.py` merged to main. |
| 0.9 | Join Discord, ask the two blocking questions | D | (a) `MERGE`/index support, (b) dataset freedom per FAQ. Ask early — answers arrive while you work. |
| 0.10 | `docker-compose.yml`: HydraDB + MinIO | A | Also your judge-facing one-command setup (S-05). |

**Gate:** schema frozen, probe results known, node answering queries. Do not proceed
otherwise — everything downstream inherits these decisions.

---

## Phase 0.5 — TBox authoring (hours 8–13, Tue 16:00–21:00)

**Added after the original plan was written — see BUILD-SPEC.md §7.6.** One person,
not the whole team; the other three start Phase 1's non-schema-dependent work
(adapter scaffolding, UI skeleton, harness loading) in parallel.

| # | Task | Notes |
|---|---|---|
| 0.5.1 | Check `vendor/enterpriserag-bench` for generation scaffolding | Company overview, initiatives, employee directory — may exist in the repo, not just the release download. If found, use it as the TBox backbone and skip most of the manual enumeration below. |
| 0.5.2 | Gather competency questions | All 500 official questions, track brief examples, ~100-200 sampled documents across all 9 sources |
| 0.5.3 | Enumerate classes and relations | Domain, range, functional/non-functional per relation |
| 0.5.4 | Validate against competency questions | Every question phraseable in this vocabulary? Spot-check 50 by hand |
| 0.5.5 | Freeze `ontology/tbox.yaml`, materialize as `:Class`/`:Relation` nodes | Commit. Treat as frozen, same discipline as the graph schema in Phase 0 |

**Gate: TBox frozen before anyone starts Tier 2 semantic extraction.** Tier 1 structural
ingest doesn't depend on this and can proceed in parallel per the table above.

**Everything below shifts by roughly 5 hours** relative to the original hour markers —
treat "hour 8" in Phase 1 as "hour 13," and so on through the rest of this document. The
milestone gates (walking skeleton, full corpus ingested, etc.) still apply in the same
order; only the wall-clock labels move. The TBox is not on the cut list further down —
BUILD-SPEC.md's M0.5 treats it as non-optional, not a stretch goal.

---

## Phase 1 — Walking skeleton (hours 8–16, Tue 16:00–Wed 00:00)

Now split. Each person owns a lane; interfaces are the frozen Pydantic models.

**A — Ingest.** Two adapters only (Slack + Confluence — most and least structured, so
they bracket the difficulty). `Document` → batched `UNWIND` writer. Measure throughput
on 5,000 docs and extrapolate; if the projection exceeds 6 hours for 500K, fix it now,
not at hour 40.

**B — Extraction.** Claim extraction prompt, structured JSON out, `Claim` + `EVIDENCED_BY`
+ `Mention` written. Run on 200 documents. Read the output by hand. Fix the prompt. This
manual read is worth more than any automated metric at this stage.

**C — Query.** `LOOKUP` path end to end: anchor → claim match → assemble → synthesize →
JSON contract (doc 05 §7). Stub the gate to always-answer for now.

**D — Harness + shipping.** Load the 500 questions. Build the scoring loop and results
writer against C's contract. Start the README. **Draft the video script now, at hour 12,
while the system is still aspirational** — the script tells you what the demo must do,
which is genuinely useful information to have early.

**Gate (hour 16): one question answered from real corpus data with a real citation.**
If not, stop and fix the seam before adding anything.

---

## Phase 2 — Depth (hours 16–34, Wed 00:00–18:00)

Sleep is in here. Take it — hour 55 with four exhausted people is when the real mistakes
happen.

**A — Ingest.** Remaining seven adapters. Near-duplicate detection (SimHash + LSH,
`NEAR_DUPLICATE_OF` clusters). **Launch the full 500K structural ingest as a background
job** — it runs while you build everything else. Checkpoint it so a crash costs minutes,
not hours.

**B — Resolution.** The main event. Doc 03 end to end: normalize → block → candidates →
score (with `algo.MSpaths` batch co-occurrence) → cluster with guards → LLM adjudication
on the uncertain band → canonicalize. **Implement negative co-occurrence before you run
clustering at scale** (doc 03 §5) — without it your clusters collapse and you will not
notice until the demo.

**C — Query.** `MULTIHOP` via `algo.MSpaths`. **The abstention gate (doc 05 §5)** — the
highest-value component in the project; give it real hours, not scraps. `CONFLICT` and
`AGGREGATE` plan classes.

**D — Eval + UI.** Full harness running with per-category breakdown. First real baseline
numbers. Demo UI skeleton with the four elements from doc 05 §8.

**Gate (hour 34): full corpus structurally ingested; ER producing canonical entities;
abstention gate firing correctly on hand-made absent questions.**

---

## Phase 3 — Conflict + integration (hours 34–46, Wed 18:00–Thu 06:00)

**B — Conflict.** Doc 04: structural detection, taxonomy classifier (temporal/scope/
granularity vs true conflict), trust function, `ConflictSet` persistence. Run over the
graph. **Hand-inspect 30 detections** — false conflicts are worse than missed ones.

**C — Query.** Conflict-aware synthesis. Temporal questions. Lazy extraction path.

**A — Ingest.** Tier 2 enrichment over the question-set neighbourhoods. Verify structural
ingest completed and node/edge counts are sane.

**D — Eval.** Full 500-question run. **The ER ablation** (doc 03 §10) — resolution off vs
on, multi-hop delta. This is your headline number; make sure it exists.

**Gate (hour 46): every capability demonstrable end to end. This is the submittable
system.** From here everything is improvement, and you could ship if you had to.

---

## Phase 4 — Tune and prove (hours 46–58, Thu 06:00–18:00)

No new features. Tuning and evidence only.

- Threshold sweep: ER `tau`, abstention thresholds, trust weights. Small grid, measured.
- Abstention calibration on the unanswerable subset — precision, recall, false-abstention
  rate, all three reported.
- **The object-store recovery demo** (doc 02 §7). Rehearse it: kill the node, restart,
  same answer, empty cache. Twenty seconds, highest signal-per-second in the video.
- Pick and rehearse the five demo questions (doc 05 §9) from real eval results.
- Error analysis: sample 20 failures, categorize them, write it up. **A judge who sees an
  honest error analysis trusts every other number you report.**
- **A clean-machine reproduction test.** One person clones the public repo into a fresh
  container and follows the README exactly. Whatever breaks, breaks for a judge too.

**Hard stop: Thu 20:00 IST. Feature freeze. Tag `v1.0`.**

---

## Phase 5 — Ship (hours 58–65, Thu 18:00 – Fri 08:00)

Everything here is required, none of it is code, and every hour is load-bearing.

| Task | Time | Owner |
|---|---|---|
| README final (doc 07 §1) | 2h | D |
| `THIRD_PARTY.md`, `NOTICE`, license check | 30m | D |
| `docs/` — architecture, cypher-support, eval results, error analysis | 1h | C |
| **Record demo video** | 2h | All |
| Edit to ≤ 2:50 | 1h | D |
| Upload, verify **logged-out on mobile data** | 30m | D |
| Fill submission form | 45m | D |
| **Full disqualification checklist** (doc 01 §E), read aloud | 30m | All |
| Submit | — | D |

**Budget 2 hours for recording.** First takes are always bad, something in the demo will
misbehave on camera, and 3:00 is a hard ceiling — the rules say anything past it may not
be reviewed. Target 2:50.

---

## Cut list — in order

When you fall behind, cut from the top. Decide these now, calmly, rather than at 3am.

1. HERB dataset. **Already cut.**
2. Lazy extraction at query time → precomputed only. Keep the code path, disable it.
3. Demo UI → CLI with pretty terminal output. Video still works.
4. `AGGREGATE` and `TEMPORAL` plan classes → fold into `LOOKUP`.
5. Corpus-mined nickname lexicon → public list only.
6. Fine-grained per-predicate authority weights → flat source table.
7. Full 500-question eval → stratified 150-question sample, **clearly labelled as such.**
8. Adapters for the three lowest-volume sources → document the gap honestly.

**Never cut:** entity resolution, conflict adjudication, the abstention gate, citations,
the README, or the video. Those five are the track, and a submission missing any one of
them is a submission that scored zero on a named requirement.

---

## Risk register

| Risk | P | Impact | Mitigation |
|---|---|---|---|
| HydraDB build fails on someone's machine | Med | High | Docker image day one; nobody blocked on a local toolchain |
| `MERGE` unsupported, discovered late | Med | **Critical** | Probe at hour 1. Client-side dedupe fallback designed already (doc 02 §2) |
| Ingest slower than projected | Med | High | Measure at hour 12 on 5K docs and extrapolate. Per-source slices allow partial corpus |
| LLM rate limits during extraction | High | Med | Batch, backoff, checkpoint, run overnight. Never a synchronous dependency |
| ER over-merges into giant clusters | **High** | High | Negative co-occurrence + size caps + bridge detection (doc 03 §6). Assume it will happen once |
| Eval scores disappointing | Med | Low | **Report them honestly.** Judges said products over scores. Error analysis converts a weak number into evidence of rigour |
| Video over 3:00 | High | **Critical** | Script to 2:30, record to 2:50. Rules say over-length may not be reviewed |
| Repo accidentally private | Low | **Critical** | Verify logged-out, on a phone, on mobile data |
| Someone burns out | Med | High | Sleep is in the plan. Enforce it. |

---

## If you are solo

Same phases, ruthless cuts. Realistic target:

- Four adapters, not nine (Slack, Confluence, Jira, Gmail — highest signal, most diverse).
- 50K documents, not 500K. **Say so plainly in the README**; a stated, reasoned limit
  reads as engineering judgment, an implied 500K that isn't there reads as dishonesty.
- ER without LLM adjudication — rules and graph signals only.
- Conflict detection with a flat trust table.
- CLI, no UI.
- **Keep the abstention gate at full fidelity.** It is the differentiator and it is cheap.

A solo submission that does entity resolution and abstention properly over 50K documents
is stronger than a four-person one that ingests 500K and answers everything confidently
including the questions with no answer.
