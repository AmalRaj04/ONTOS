# TBox validation against the competency questions

Per BUILD-SPEC.md §7.6: "The TBox is complete when every one of [the competency
questions] can be phrased entirely in its vocabulary — not when the corpus has been
exhaustively catalogued." All 500 `questions.jsonl` + 100 `extra_questions.jsonl`
(600 total) were read in full against `ontology/tbox.yaml`.

## Method

For each of the 11 question-type buckets (`basic, semantic, intra_document_reasoning,
project_related, constrained, conflicting_info, completeness, miscellaneous,
high_level, info_not_found, metadata`), checked: (1) does the question's subject/
object entity type fall within the 8 TBox classes (or the generic Document/Chunk
provenance layer)? (2) where the question asks for a specific fact, is that fact
shape one of the TBox's relations, or is it a narrative/numeric value embedded in
document prose that belongs to chunk-level retrieval instead?

## Result: 600/600 phraseable, 0 vocabulary gaps requiring a new class or relation

**Class coverage: 100%.** Every question's subject entity is a Person, Team,
Project, Product, Customer, Ticket, Meeting, or Thread — or is about a generic
Document (company overview, a runbook, a postmortem), which is the structural
provenance layer, not a TBox class, and needs no vocabulary entry to be queried.
No question required an entity type outside these.

**Relation coverage is intentionally partial, and that's correct, not a gap.**
Roughly a quarter of the 600 questions (most of `metadata`, and the ownership/
status/date/review-shaped questions inside `basic`/`semantic`/`project_related`)
map directly onto `OWNS`, `STATUS`, `DEADLINE`, `PRIORITY`, `TIER`, `REVIEWED_BY`,
`ATTENDED`, `BLOCKS`, `REPORTS_TO`, `MEMBER_OF`. The majority of the remaining
questions (most of `basic`, `semantic`, `constrained`, `conflicting_info`,
`completeness`, `intra_document_reasoning`, all of `miscellaneous` and
`high_level`) ask for a narrative or numeric fact embedded in document prose — a
retention period, a latency percentile, an incident's root cause, a config
threshold, an office policy. These are not relation triples with a stable object
type; forcing them into named TBox relations would be exactly the "speculative
cataloguing" §7.6.2 says not to do. They are answered by `LOOKUP`/`MULTIHOP`
traversal to the relevant `Document`/`Chunk` (via entity anchor or Tier-2 claim
evidence) plus a quoted citation — the mission-critical path for those is chunk
retrieval, not Claim-typed extraction. `info_not_found` (20 questions) is designed
to have no answer at all; anchors may resolve but no claim/path will, which is the
intended abstention trigger, not a vocabulary failure.

## One real design decision (not "obviously right" from a naive top-down read)

**GitHub PRs are modeled as `Ticket` (`tracker: github`), not a new class.** The
naive read of "PR" suggests a `PullRequest` class. But GitHub's actual JSON shape
(`pr_number, state, reviewers, merge_outcome`) is structurally identical to
`Ticket`'s `{tracker, key, state}`, and a large fraction of `metadata`/`basic`
questions cross-reference PRs and Jira/Linear tickets interchangeably
(`related_github_prs`, `linked_jira`, `linked_linear`). A second class would
duplicate `Ticket` and complicate exactly the cross-tracker linkage (`LINKED_TO`)
the questions need. Same reasoning applies to "incidents": jira/linear already
carry `issue_type`/labels for incidents, so incidents are `Ticket`s, not a new
`Incident` class — consistent with BUILD-SPEC.md §7.2 being frozen and §7.6.2's
instruction not to invent new node labels beyond it.

## What would have been over-fit without the document sample

A purely top-down read of BUILD-SPEC.md §8.4's 10 example predicates
(`OWNS, WORKS_ON, MEMBER_OF, LAUNCH_DATE, STATUS, REPORTS_TO, DEADLINE, BLOCKS,
PRICE, HEADCOUNT`) would have under-served `PRIORITY` (extremely common across
jira/linear/hubspot), `ATTENDED` (fireflies meeting questions), `REVIEWED_BY`
(github/confluence review questions), `TIER` (hubspot account tier questions),
and `LINKED_TO` (cross-tracker reference questions) — all added because the
question set and source JSON field samples demonstrably need them, per §7.6.2
step 4's validate-and-fix-gaps loop.
