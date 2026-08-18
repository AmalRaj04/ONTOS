# 01 — Requirements Traceability

Every requirement from the track brief, the submission rules, the judging criteria and the
disqualification list, mapped to where it is satisfied. This is the document you re-read
on Thursday night before freezing.

**How to use:** each row has an ID. Reference the ID in commit messages and PR titles.
On Thursday, walk the table top to bottom and mark every row green or explicitly accepted
as a gap. A gap you have named is a risk; a gap you have not is a disqualification.

---

## A. Track 01 functional requirements

Derived from the track brief verbatim. These are what the *track* judges score.

| ID | Requirement | Where satisfied | Evidence for judges |
|---|---|---|---|
| T-01 | Ingest ~500K docs from nine sources | Doc 02 §3, `src/ingest/` | Node count query in demo; `stats` CLI command |
| T-02 | Handle misfiled documents | Doc 02 §6.1 — source-declared location treated as a *claim*, not ground truth | Show a doc whose content contradicts its filing |
| T-03 | Handle near-duplicates | Doc 03 §5 — MinHash/LSH dedupe into `NEAR_DUPLICATE_OF` clusters, one canonical representative | Duplicate cluster visualized; corroboration counting explicitly de-weights duplicates |
| T-04 | Handle contradictory statements | Doc 04 in full | Live conflict adjudication in demo |
| T-05 | Produce a **clean, queryable ontology** in HydraDB | Doc 02 §4 schema; `schema.cypher` | Judge runs Cypher against the live graph |
| T-06 | Answer simple lookups | Doc 05 §2 — plan class `LOOKUP` | Eval category scores |
| T-07 | Answer multi-hop reasoning questions | Doc 05 §3 — `algo.MSpaths` traversal | Traversal path rendered in UI |
| T-08 | Conflict resolution at answer time | Doc 04 §4 trust function | Answer shows both claims + which won + why |
| T-09 | **Correctly recognize the answer is absent** | Doc 05 §5 abstention gate | Dedicated eval metric; dedicated demo beat |
| T-10 | Entity resolution across surface forms | Doc 03 in full | `MATCH (p:Person)-[:ALIAS_OF]-(m) RETURN m.surface` live |
| T-11 | Ontology alignment | Doc 03 §7 — type unification and relation canonicalization | Schema before/after counts |
| T-12 | HydraDB does **real work**, not decoration | Doc 02 §7 — the graph is the only store; no shadow vector DB holds the answer | "What would this lose without HydraDB?" — answer in README §7 |

**T-12 is the one that gets teams disqualified.** The rules say HydraDB must do real work
and that you must "be ready to say where it is used and what the project would lose
without it." Doc 02 §7 exists solely to answer that question in one paragraph. Memorize it.

## B. Hackathon submission requirements

Hard gates. Missing any one of these is a disqualification, independent of quality.

| ID | Requirement | Owner | Done when |
|---|---|---|---|
| S-01 | Public GitHub repository | D | Verified in a logged-out incognito window |
| S-02 | Complete source code in repo | D | Fresh clone builds |
| S-03 | **No participant commits before 2026-08-12** | All | `git log --reverse \| head` shows first commit ≥ 12 Aug |
| S-04 | Clear README | D | Doc 07 §1 template filled |
| S-05 | Setup and run instructions | A + D | A teammate's clean machine reproduces it |
| S-06 | Explanation of how HydraDB is used | C | README §7 |
| S-07 | Environment/dependency information | A | `.env.example`, pinned `requirements.txt`/`uv.lock` |
| S-08 | Attribution for third-party libs, APIs, datasets | D | `THIRD_PARTY.md` |
| S-09 | **Open-source license** | D | `LICENSE` at repo root, Apache-2.0 |
| S-10 | Demo video ≤ 3:00 | D | Timer verified; 2:50 target |
| S-11 | Video watchable without access request | D | Opened logged-out from a phone |
| S-12 | Submission form filed before deadline | D | Confirmation screenshot saved |

### S-03 deserves paranoia

The rules state organizers can and do read commit history. Concretely:

- Start a **genuinely new repository**. Do not `git init` over an existing directory that
  has a `.git` you forgot about.
- Do not import code you wrote before 12 Aug. If you have a library you love, either
  publish it separately as a dependency with honest attribution, or rewrite it.
- Never rewrite history to fake dates. It is detectable, and it converts a
  "well-executed project" into a disqualification and a reputation cost.
- Forked or vendored *dependency* history does not count against you — that is normal
  open-source use, and the FAQ has a question on exactly this. Your own authored commits
  are what matter.

## C. Judging criteria

Five published criteria. What each one actually rewards, and where you earn it.

| ID | Criterion | Where you win it | Where teams lose it |
|---|---|---|---|
| J-01 | Technical execution | ER + conflict adjudication are the hard parts and you did them properly | Extraction-only pipelines that skip resolution |
| J-02 | **Use of HydraDB and graph-native approaches** | Doc 02 §7 — `algo.MSpaths`, consistency modes, object-store recovery demo | Using the graph as a document store with a vector DB doing the real retrieval |
| J-03 | Product completeness and usability | Demo UI, one-command setup, honest README | A notebook with no product around it |
| J-04 | Quality of results | Eval harness with real per-category numbers | Cherry-picked examples, no measurement |
| J-05 | Originality | Reified claims + graph-native abstention + lazy extraction | Textbook GraphRAG with the serial numbers filed off |

The published line — *"We care about working, thoughtful products, not just benchmark
scores"* — is a direct instruction. It means J-03 and J-05 are live, and it means an
honest 61% with a working product and a clear-eyed error analysis beats a claimed 78%
with nothing runnable behind it.

## D. Best Use of HydraDB ($500, judged separately)

This award is winnable independently of placement, and it is the highest expected value
per hour of any target in the hackathon. Published criteria, and the specific thing you
point at for each:

| Published criterion | Your answer |
|---|---|
| A particularly strong graph data model | Reified `Claim` nodes with provenance edges — facts are addressable, versioned and contestable, which is impossible in a property-on-edge model |
| A novel retrieval or reasoning approach | **Traversal-bounded abstention**: an empty path set is treated as positive evidence of absence, giving a calibrated "not in corpus" that similarity search structurally cannot produce |
| Interesting use of relationships, traversal or context | `algo.MSpaths` with `pairwise: true` used for *batch entity-resolution scoring* — many candidate pairs evaluated in one server-side call instead of client-side fan-out |
| Hard with vector or relational approaches | Transitive alias closure, blast-radius-style multi-hop reachability, and the conflict lattice; each is a traversal that a top-k index cannot express |

Write these four sentences into README §7 verbatim. That section is what the separate
judging panel reads.

## E. Disqualification checklist

Walk this list at Thursday 20:00 IST. Out loud. With someone else watching.

- [ ] No work started before 12 Aug 2026 → S-03
- [ ] Repository is public, not private → S-01
- [ ] `LICENSE` file present in the repo root → S-09
- [ ] Demo video exists and is under 3:00 → S-10
- [ ] HydraDB used meaningfully → T-12
- [ ] Submitted before 20 Aug 23:59 PT / **21 Aug 12:29 IST** → S-12
- [ ] Code of conduct respected → n/a barring incident

Plus the failure mode the rules call out by name — *"Open your repo, video and demo links
yourself before you submit. Broken links are the most common way people lose."* Do this
from a **logged-out browser on a phone on mobile data**, not from the laptop where you
are signed into everything. A repo that is public-to-you and 404 to a judge is
indistinguishable from no submission.

## F. Multi-track eligibility

You are entering one track. Worth knowing anyway:

- A team may enter multiple tracks with **meaningfully distinct** projects. The same
  project with minor modifications across tracks is explicitly disallowed.
- A team can be a finalist in more than one track but takes home at most one of the top
  three awards.
- Every team stays eligible for Best Use of HydraDB regardless of track.

With ~65 hours, a second track entry is not a realistic consideration. One track, done
well, is strictly the better play — the top submission from each track advances to the
final round, so depth in Track 01 is what buys you a shot at the Grand Champion.
