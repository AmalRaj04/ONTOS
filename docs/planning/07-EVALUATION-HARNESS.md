# 07 — Evaluation Harness

**Owner:** D. **Budget:** ~6 hours, spread across phases.

Judging criterion J-04 is "quality of results", and the judges also said they care about
working products over benchmark scores. Both are true at once: you need real numbers, and
you need them to be *honest* numbers with an error analysis attached. An honest 61% with a
categorized failure breakdown reads as competence. An unsupported 78% reads as noise.

---

## 1. What you are scoring against

EnterpriseRAG-Bench ships 500 questions across ten categories, plus `extra_questions.jsonl`
containing 100 additional metadata-dependent questions excluded from the core benchmark.

The expected output format is JSONL, one line per question, each carrying a
`question_id`, an `answer`, and the list of `document_ids` retrieved. Match that format
exactly — do not invent your own and convert later.

```jsonl
{"question_id": "q_0001", "answer": "...", "document_ids": ["doc_a", "doc_b"]}
```

Read the repo's evaluation harness before writing yours. If it ships a scorer, use it —
your own reimplementation will differ subtly and every difference is a number you cannot
defend. Note also that the benchmark applies gold-set corrections during evaluation and
writes flagged questions to a separate updated-questions file; be aware your gold set may
shift between runs.

**The extra metadata-dependent questions are an opportunity.** They are excluded from the
core benchmark, which means most teams will ignore them — and they test exactly what a
graph with rich structural metadata is good at. Running them is cheap, differentiating,
and gives you a second number nobody else reports.

## 2. Report per category, always

A single aggregate number hides everything interesting. Your per-category table is the
argument for the graph approach:

```
category                  n    correct   acc     baseline*  delta
─────────────────────────────────────────────────────────────────
simple_lookup            120      —       —         —         —
multi_document           95       —       —         —         —
multi_hop                70       —       —         —         —
conflict_resolution      45       —       —         —         —
unanswerable             50       —       —         —         —
constrained_retrieval    40       —       —         —         —
temporal                 35       —       —         —         —
aggregation              25       —       —         —         —
...
─────────────────────────────────────────────────────────────────
* baseline = BM25 + LLM over the same corpus, no graph
```

Category names and counts are illustrative — take the real taxonomy from the released
question file.

**Build the BM25 baseline.** Two hours of work, and it converts every number you report
from an absolute you cannot contextualize into a *delta* you can defend. Where the graph
wins big — multi-hop, conflict, unanswerable — that delta is your entire thesis rendered
as a table. Where it does not win (simple lookup, almost certainly), saying so out loud
is what makes the rest credible.

## 3. Abstention metrics — report three, never one

The unanswerable category needs its own treatment, because a single accuracy number can
be gamed in both directions and judges know it.

```python
# On the unanswerable subset
abstention_recall    = abstained_correctly / total_unanswerable
# On the answerable subset
false_abstention     = abstained_wrongly / total_answerable
# Overall
abstention_precision = abstained_correctly / total_abstentions
```

Report all three, plus the confabulation rate — how often the system produced a confident
answer to a question with no answer. That last number is the one that matters most for a
real enterprise product, and it is the number a vector-only baseline will be catastrophic
on. Put the comparison in the video.

## 4. Ablations — the numbers that prove your design

Each ablation isolates one component. These are worth more than the headline score,
because each one is a claim about *why* the system works.

| Ablation | What it proves | Expected |
|---|---|---|
| **ER off** (every surface form its own entity) | Entity resolution's value | Large drop on multi-hop and lookup |
| **Conflict adjudication off** (take highest-trust claim silently) | Adjudication's value | Drop on conflict category |
| **Abstention gate off** (always answer) | The gate's value | Unanswerable score collapses |
| **Claims flattened to edge properties** | The reification decision (doc 02 §4.4) | Conflict handling becomes impossible |
| **1-hop only** (`maxLen: 1`) | Multi-hop traversal's value | Drop on multi-hop |
| **BM25 baseline** | The graph approach overall | Graph wins on hop/conflict/absent |

You will not have time for all six. **Do the first, third and last** — ER off, gate off,
BM25 baseline. Those three tell the whole story.

The ER ablation in particular is your single best video line: *"multi-hop accuracy goes
from X to Y when entity resolution is enabled"* is a measured claim about the exact thing
the track brief calls the hard part.

## 5. Harness structure

```
eval/
├── run_eval.py            # main loop, resumable
├── baselines/bm25.py
├── ablations/
│   ├── no_er.py
│   ├── no_abstention.py
│   └── one_hop.py
├── questions/             # official set, unmodified
├── results/
│   ├── main_run.jsonl
│   ├── main_run_summary.json
│   ├── ablation_*.json
│   └── error_analysis.md
└── README.md              # how a judge reproduces this
```

```python
def run_eval(questions, system, out_path, resume=True):
    done = load_done(out_path) if resume else set()
    for q in tqdm(questions):
        if q.id in done:
            continue
        t0 = time.perf_counter()
        try:
            r = system.answer(q.text, consistency="strong")   # reproducible snapshot
        except Exception as e:
            r = Answer.error(str(e))
        append_jsonl(out_path, {
            "question_id": q.id,
            "answer": r.answer,
            "document_ids": [c.doc_id for c in r.citations],
            "_abstained": r.abstained,
            "_confidence": r.confidence,
            "_latency_ms": (time.perf_counter() - t0) * 1000,
            "_path_count": r.traversal.path_count,
            "_conflicts": len(r.conflicts),
        })
```

Three properties that matter more than they look:

**Resumable.** You will interrupt this run. Repeatedly. A non-resumable harness costs you
an hour every time.

**`consistency="strong"`.** Evaluation runs against a known-fresh snapshot, so a re-run
gives the same numbers. Reproducibility is a submission requirement for the leaderboard
and a credibility requirement for judges.

**Underscore-prefixed diagnostic fields.** They are not part of the official output
contract but they are where your error analysis comes from. Strip them if submitting to
the leaderboard; keep them locally.

## 6. Error analysis

Two hours on Thursday. The highest-leverage two hours in the entire evaluation budget.

Sample 20–30 failures, stratified across categories, and categorize each:

| Failure mode | Root cause | Fixable in time? |
|---|---|---|
| Retrieval miss | Relevant doc not enriched (Tier 2 gap) | Yes — warm that neighbourhood |
| ER under-merge | Alias not caught, path severed | Maybe — lower `tau`, add block key |
| ER over-merge | Wrong entities collapsed | Yes — negative signal, size cap |
| Extraction miss | Claim never extracted | No — prompt work |
| Wrong adjudication | Trust weights off | Yes — reweight |
| False abstention | Gate too strict | Yes — threshold |
| Confabulation | Gate too loose | Yes — threshold |
| Genuinely hard | Requires reasoning beyond the graph | No — say so |

Write it up as `eval/results/error_analysis.md`. Publish it in the repo.

This is counterintuitive and it is correct: **documenting your failures makes your
successes credible.** A judge reading a clear-eyed breakdown of what does not work
believes the numbers for what does. A submission with no error analysis invites the
assumption that you did not look.

## 7. What to claim, and how

**Do:**
- Report the exact corpus subset you enriched, with counts. "500,132 documents
  structurally indexed; 47,300 semantically enriched; remainder enriched lazily on demand."
- State the model and version used for extraction and synthesis.
- Report ablations and the baseline.
- Report the abstention tradeoff as three numbers.
- Note that per-source slices allow partial corpus runs and say which you used.

**Do not:**
- Imply full semantic coverage you do not have. It is the easiest thing for a judge to
  check and the most expensive to be caught on.
- Report a single headline number with no denominator.
- Submit to the public leaderboard without meeting its reproducibility bar — open-source
  submissions require a reproduction guide, and a failed verification is worse than no
  submission.
- Compare against a strawman baseline you tuned badly. If BM25 beats you somewhere, say so.

## 8. Minimum viable evaluation

If you are down to two hours and everything is on fire, this is the floor:

1. Run 150 stratified questions, not 500. Label it clearly as a stratified sample.
2. Report per-category accuracy on those.
3. Report the three abstention numbers.
4. Run the ER-off ablation on the multi-hop slice only.
5. Write ten lines of error analysis.

That is enough to be credible. Fabricating a full 500-question run you did not do is not
a shortcut — the leaderboard requires reproducibility, and any number you cannot
reproduce on request is a number that will eventually be checked.
