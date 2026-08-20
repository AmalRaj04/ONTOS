# ONTOS — demo video script (target 2:50, hard cap 3:00)

Required coverage per the track brief: the problem, what you built, a working demo,
and how you used the HydraDB repo and why it matters. All four must appear.

Real numbers below are placeholders (`[N]`) to be filled in from
`eval/results/scores.json` and `data/er_report.json`/`data/conflicts_report.json`
once M6's eval run completes — do not record before that.

| Time | Content | Screen |
|---|---|---|
| 0:00–0:15 | "244,822 documents across nine systems — Slack, Gmail, Jira, Confluence, GitHub, Linear, HubSpot, Fireflies, Google Drive. Misfiled, duplicated, contradicting each other. Ask a vector database a question and it always gives you an answer — even when there isn't one." | Corpus scale (docs/coverage.md's table), then cut to a confident-but-wrong BM25 baseline answer on an `info_not_found` question |
| 0:15–0:30 | "ONTOS turns that into one ontology in HydraDB. Every fact is a reified Claim node with a link back to the exact document and chunk it came from." | Quick schema diagram (Document → Chunk → Mention → Person, Claim → EVIDENCED_BY → Chunk) |
| 0:30–1:00 | **Entity resolution.** "The hard part isn't extraction, it's knowing a Jira reporter's display name and a completely different email address on Slack are the same person, with no string overlap between them." Show a real resolved Person's alias set from `data/er_alias_map.json`, live in the UI. | `ui/app.py`, "Alias-dependent" demo question, alias line rendered |
| 1:00–1:30 | **Multi-hop.** A question no single document answers — a relationship path through an intermediate Claim. Show the traversal path rendered (anchors, hop count, path summary). | `ui/app.py`, "Multi-hop" demo question, traversal expander open |
| 1:30–2:00 | **Conflict.** "Two sources disagree. We don't pick silently — a trust function scores authority, recency, and cross-system corroboration, and shows its work." Show both claims, the margin, the rationale string. | `ui/app.py`, "Conflict" demo question, conflict banner |
| 2:00–2:20 | **Abstention.** "Now something that genuinely isn't in the corpus." Show the informative refusal — what was searched, why it stopped. | `ui/app.py`, "Absent answer" demo question, warning banner with reason |
| 2:20–2:40 | **HydraDB.** "Object storage is the source of truth; query nodes are disposable." `kill -9` on graph-node, restart, empty cache, same question, same answer. "Nothing re-ingested." | Terminal: kill, restart, re-run the same question |
| 2:40–2:50 | Numbers + repo link. "[N]% overall accuracy, ER ablation shows a [N]-point multi-hop lift with resolution on, [N]% abstention recall on questions with no real answer. Everything's in the repo, including the honest coverage tradeoffs." | `eval/results/scores.json` summary table |

## Production notes

- Every demo query pre-warmed and rehearsed against the live stack before recording — no live extraction on camera (Tier 2/ER are ingest-time, not query-time, so this is naturally satisfied).
- Record system audio + a clean voice track.
- Cut all waiting — nobody needs to watch a query run in real time.
- Real terminal and real Streamlit UI, not slides.
- Unlisted YouTube (or wherever the platform requires) — verify playback logged out, on a phone, on mobile data, before submitting.
- The abstention beat at 2:00 is the differentiator — most submissions will only demo a lookup.

## Recording checklist (do this, in order)

1. Confirm the stack is up: native MinIO, `graph-node`, `graph-indexer` all running (`make hydradb-minio-up` / `hydradb-indexer-up`).
2. Confirm `data/er_alias_map.json`, `data/conflicts_report.json`, `eval/results/scores.json` all exist and are the final versions.
3. Pick one real alias-set example from `data/er_alias_map.json` and one real conflict example from `data/conflicts_report.json`'s `cross_system_examples` — replace the placeholder demo questions in `ui/app.py`'s `DEMO_QUESTIONS` with ones that actually exercise them, then re-verify each renders correctly before recording.
4. Run `streamlit run ui/app.py`, click through all 5 demo questions once, confirm no errors.
5. Rehearse the kill/restart recovery beat once off-camera before recording it live.
6. Record. Recording itself is a user action — this session prepared everything up to this checklist.
