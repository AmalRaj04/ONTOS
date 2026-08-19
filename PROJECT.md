# ONTOS — Project Tracker

Living document. Updated at the end of every phase. Mirrors `BUILD-SPEC.md` §13
milestones. See `/Users/amalraj/.claude/plans/go-through-build-spec-md-glowing-blum.md`
for the full phase-by-phase plan this tracker follows.

Deadline: **2026-08-21, 12:29 PM IST**.

## Milestone status

| Milestone | Status | Notes |
|---|---|---|
| M0 — Foundation | **done** | HydraDB running natively, round-trip verified, full P1-P7 probe run live, `docs/cypher-support.md` written, `src/schema/{models.py,ids.py}` committed, license present |
| M0.5 — TBox frozen | **done** | `ontology/tbox.yaml` (8 classes, 15 relations) validated against all 600 questions (500+100), 0 vocabulary gaps; materialized as `:Class`/`:Relation` nodes, spot-checked live |
| M1 — Walking skeleton | **done** | Confluence adapter, Tier 1 (chunk+mention), Tier 2 (LLM claim extraction+TBox gate), full anchor→plan→traverse→gate→synthesize LOOKUP path — all verified against live HydraDB + real Gemini/Groq calls. `tests/test_m1_walking_skeleton.py` reproduces it. |
| M2 — Ingest depth | not started | |
| M3 — Entity resolution | not started | |
| M4 — Conflict resolution | not started | |
| M5 — Query completeness | not started | |
| M6 — Evaluation and proof | not started | |
| M7 — Ship | not started | |

## Decisions / deviations log

Ground-truth findings from the vendored repos that override or sharpen the spec's
prose, per the spec's own §0 rule. Each entry: what, why, where it's applied.

1. **[Critical, found beyond the P1-P7 checklist] HydraDB's node `id` property
   must be a non-negative integer** (confirmed live: a string `id` in an `UNWIND`
   batch is rejected with "must be a non-negative integer"; integers up to 2^62-1
   work). This conflicts with spec §7.1's string content-addressed ID scheme.
   Resolution (`src/schema/ids.py`): `hydra_id(spec_id)` derives a deterministic
   62-bit non-negative integer surrogate from the same blake2b hash family as
   `node_id()`, used only for HydraDB's `id` property (MERGE-upsert identity,
   relationship endpoint matching). The spec's string IDs (`doc_id`, `claim_id`,
   `canonical_id`, ...) are unchanged and still stored as regular node properties
   — confirmed live that `MATCH`/`WHERE` filtering on non-`id` properties works
   normally. Full detail in `docs/cypher-support.md`'s "Critical finding" section.
2. **`CREATE INDEX` is not part of HydraDB's Cypher subset — confirmed live**, both
   `CREATE INDEX FOR (n:Label) ON (n.prop)` and `CREATE INDEX ON :Label(prop)`
   syntaxes rejected at parse time. Indexing (CSC generations, property indexes)
   is automatic/server-managed. Per `BUILD-SPEC.md` §7.4's own explicit fallback
   instruction ("if CREATE INDEX is unsupported per §6, delete this file and note
   the gap in docs/cypher-support.md instead"), **`schema.cypher` was not
   created** — full probe log and rationale in `docs/cypher-support.md`.
3. **`MERGE`/`CREATE` cannot be followed by `RETURN` (or any other clause) in the
   same statement — confirmed live**, and a standalone single-node `MERGE`/
   `CREATE` (no relationship) only executes inside an `UNWIND` batch, not as a
   bare statement. `RETURN` also does not support function calls like `type(r)`
   or `length(path)` — only `<binding>.<property>` or an aggregate. All three
   corrections are reflected in the P1/P2/P4 probe results in
   `docs/cypher-support.md`, which required adapting the spec's literal probe
   queries to get a real pass/fail read.
4. **`MERGE` matches only by `id`, no `ON CREATE`/`ON MATCH`.** Writer pattern:
   `MERGE (n {id: $id}) ... SET n += $props` (`src/ingest/writer.py`).
5. **`UNWIND` is a batch-write primitive bound to a `$rows` parameter**, reachable
   only over Bolt/HTTP (not any in-process API). This matches the Python `neo4j`
   driver's shape exactly, so `writer.py` targets it directly. HTTP transport's
   JSON field for parameters is `parameters`, not `params`.
6. **`algo.MSpaths` ground truth** comes from `vendor/hydradb/src/query/
   path_procedure.rs`, which is more complete than `cypher-compat.md` (documents
   `pairwise`, `fairRelationshipVariants` which the compat doc omits). Config keys:
   `sourceLabel/sourceProperty/sourceValues`, `targetLabel/targetProperty/
   targetValues`, `pairwise`, `relTypes`, `relDirection`, `maxLen`, `pathCount`,
   `resultLimit`, `weightProp/costProp/maxCost`. Confirmed live over both HTTP and
   Bolt, exactly as documented.
7. **No `docker-compose.yml` exists in the HydraDB repo.** Authored our own at repo
   root, `hydradb` service building from `vendor/hydradb`'s `Dockerfile` (`runtime`
   target), `minio` service for S3-compatible object storage, env vars sourced from
   `vendor/hydradb/charts/hydradb/templates/configmap.yaml` (`CLOUD_PROVIDER=aws`,
   `AWS_BUCKET_NAME`, `AWS_DEFAULT_REGION`, `AWS_ALLOW_HTTP`, `AWS_ENDPOINT`, plus
   standard `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` for MinIO auth — confirmed
   these are the right names via `scripts/deploy_single_node_k3s.sh` in the same repo).
8. **Primary local-dev bring-up uses `CLOUD_PROVIDER=local` (filesystem-backed),
   per §6's literal instructions**, not the MinIO-backed path, for build speed and
   disk economy (local disk was ~14 GiB free at project start; a from-source Docker
   build plus a native `cargo build` would double release-build disk usage).
   `docker-compose.yml` (MinIO-backed) is written and correct for judges/CI, but
   day-to-day ingest/ER/conflict/query development runs against a natively-built
   `graph-node` with `CLOUD_PROVIDER=local`. Both are the same binary/query engine —
   this only changes the object-store backend, not any Cypher/graph behavior.
   Bring-up requires `brew tap cleishm/neo4j && brew install cleishm/neo4j/
   libcypher-parser cmake llvm suite-sparse`, plus `BINDGEN_EXTRA_CLANG_ARGS`/
   `LIBRARY_PATH` exported (`just` does this automatically; a bare `cargo run`
   does not) — see `docs/cypher-support.md`'s environment notes. `make hydradb-up`
   wraps all of this.
9. **The full EnterpriseRAG-Bench corpus is already vendored locally** at
   `vendor/EnterpriseRAG-Bench/generated_data/sources/**/*.json` (511,958 docs,
   3.7 GB — richer per-doc JSON than the release `.txt` export). `CORPUS_DIR` points
   there directly; no release-zip download/unzip step is implemented since it would
   be redundant.
10. **Generation scaffolding for the TBox backbone (§7.6.1) is present**:
    `vendor/EnterpriseRAG-Bench/generated_data/{company_overview.md,initiatives.md,
    employee_directory.yaml,source_tree.txt,project_list.txt}`. Used as the TBox
    backbone in M0.5 and as an entity-resolution sanity-check set in M3.
11. **`TIER2_SAMPLE_FRACTION` recalibrated.** `.env.example`'s default `0.08` was
    sized for a partial download (~45K docs); against the full 511,958-doc corpus
    now available, 8% is ~41K documents — well past the spec's own stated budget of
    "a few thousand." Recalibrated to target ~4,000-5,000 total Tier-2 documents
    (set in M2/M0.5, exact value recorded when Tier 2 is implemented).
12. **`.gitignore` gap fixed before any commit.** Original `.gitignore` ignored
    `.env` and blanket-ignored `docs/`, but did **not** ignore `vendor/` — a
    careless `git add -A` would have committed HydraDB's AGPL source tree and/or
    the 3.7 GB corpus into this Apache-2.0 repo. Fixed: `vendor/`, `.DS_Store`,
    `data/`, `.hydradb/` now ignored; `docs/` narrowed so `docs/planning/`,
    `docs/architecture.md`, `docs/cypher-support.md`, `docs/coverage.md` (all
    explicitly part of the committed repo layout in spec §4) remain tracked.
13. **TBox file path resolved.** Spec §4 says `src/schema/ontology.yaml`; §7.6.2
    says `ontology/tbox.yaml` and calls it the same file. Canonical path:
    `ontology/tbox.yaml`. All code imports from there.
14. **`questions.jsonl`'s actual gold schema**: `{question_id, question_type,
    source_types, question, expected_doc_ids, gold_answer, answer_facts}` — not
    `{question_id, answer, document_ids}`, which is the *candidate submission*
    format. `eval/run_eval.py` reads gold with the real field names.
15. **Reusable eval/baseline code exists** in `vendor/EnterpriseRAG-Bench/src/
    scripts/{answer_evaluation/metrics_based_eval.py,
    answer_generation/bm25_retrieval.py}`. M6 adapts these instead of writing
    scoring logic from scratch.
16. **TBox validated against all 600 questions (500 + 100 extra), not just a
    50-question spot-check** — 0 vocabulary gaps. Full method and results in
    `docs/tbox-validation.md`. Two class-modeling calls worth remembering:
    **GitHub PRs are `Ticket`s** (`tracker: github`), not a new `PullRequest`
    class — their JSON shape (`pr_number, state, reviewers, merge_outcome`)
    matches `Ticket`'s shape, and cross-tracker linkage questions
    (`related_github_prs`/`linked_jira`/`linked_linear`) only work cleanly if
    PRs and Jira/Linear issues share a class. Same reasoning: **no `Incident`
    class** — jira/linear's `issue_type`/labels already cover it.
17. **Relation coverage in the TBox is deliberately partial (~25% of the 600
    questions map to a named relation).** The rest ask for narrative/numeric
    facts embedded in prose (a retention period, a latency percentile, an
    incident root cause) that a typed `Claim` triple can't usefully represent
    — those are answered by chunk-level `LOOKUP`/`MULTIHOP` retrieval +
    citation, not TBox relations. Forcing them into relations would be the
    "speculative cataloguing" §7.6.2 explicitly forbids. `info_not_found` (20
    questions) has no answer by design — that's the intended abstention
    trigger, not a vocabulary gap.
18. **`employee_directory.yaml`'s `manager` field is direct, high-confidence
    ground truth for `REPORTS_TO`** — stronger than anything Tier-2 LLM
    extraction would infer from prose. Prioritize it as a structural (Tier 1)
    source for that relation, not an LLM-extraction target, when M2/M3 build
    the resolution pipeline.
20. **[M1] Batch relationship creation between two already-existing nodes, confirmed
    live, has three extra requirements beyond `cypher-compat.md`'s examples:**
    (a) `UNWIND ... MATCH (x),(y) CREATE (x)-[...]->(y)` requires **both** endpoints
    to already exist — you cannot inline-create one new endpoint alongside a
    matched one in the same UNWIND CREATE (`"requires exactly two endpoint
    nodes"`); a brand-new child node must be `MERGE`d as its own standalone batch
    first, then linked in a second batch. (b) Both `MATCH` endpoint patterns in
    that second batch **must each carry exactly one label**
    (`MATCH (p:Parent {id:...}), (c:Child {id:...})`, not label-less `{id:...}`
    — `"endpoints require exactly one label"`). (c) The relationship itself needs
    its own integer `id` property in the same surrogate scheme as nodes
    (`CREATE (p)-[:REL {id: row.rel_vertex, ...}]->(c)` —
    `"relationship CREATE properties require id: row.<field>"`), and `MERGE`ing
    on that same `id` (instead of `CREATE`) is idempotent and confirmed safe to
    re-run (edge count unchanged on a repeat batch) — this is the pattern
    `writer.py` uses for every edge type, since ingest must be resumable
    (BUILD-SPEC.md §2). Full pattern in `src/ingest/writer.py`.
21. **[M1] HydraDB parameters reject `null` outright, confirmed live**
    (`"parameter $rows must contain booleans, signed or unsigned integers,
    finite floats, strings, lists, or string-keyed maps"` — no null in that
    list). Every `Document` field the frozen §7.5 model marks `Optional` (
    `title`, `author_raw`, `thread_key`, `uri`, `declared_container`,
    `created_at`) needs a non-null placeholder before it reaches HydraDB.
    `src/ingest/writer.py`'s `_sanitize()` maps `None -> ""` uniformly across
    every batch row before the write, so a batch's static `SET` clause always
    has a settable value for every row regardless of which fields happen to
    be null on which document.
19. **HydraDB property values can't hold lists or nested maps** (confirmed
    live via `ontology/materialize.py`: `UNWIND row N field domain must be
    scalar`). `tbox.yaml`'s list-valued `domain`/`range` fields (e.g. `STATUS`
    domain `[Ticket, Project, Customer]`) are comma-joined to strings for
    graph storage; `source_forms` (nested per-source maps) is JSON-encoded to
    a string. `ontology/tbox.yaml` remains the structured source of truth —
    the graph materialization exists to make the ontology queryable per
    §7.6, not to replace the YAML.

22. **[M1] Corpus-wide: no source uses a fixed `body`/`content` field — every
    document names its own content fields via `content_field_names` (and title
    via `title_field_name`), and this varies enormously within a single
    source**, not just between sources. Confirmed by sampling ~300-2000 docs
    per source: confluence alone has 800+ plain `{body}` pages but also
    hundreds of 10-20-field RFC/runbook-style docs
    (`summary, goals, architecture_overview, rollout_plan, ...`); slack splits
    across `messages`/`text`/`thread`; linear/jira/github spread real content
    across `description, comments, investigation_notes, resolution,
    review_comments, release_notes, ...`. An adapter that reads a hardcoded
    `body` or `content` key silently drops most of the corpus's actual text
    (caught it immediately: the first Confluence doc tried came back with
    `body length: 0`, because its real content lived in a field named
    `content`, not `body`). **Fix, applied once for all nine adapters:**
    `src/ingest/adapters/common.py`'s `assemble_body()`/`get_title()` read
    `content_field_names`/`title_field_name` from each record dynamically and
    concatenate in the document's own declared order (multi-field docs get
    lightweight `## fieldname` section headers, which also helps Tier 2
    extraction see document structure). Non-string field values (message
    lists, comment lists) are flattened via common message-shape heuristics
    (`text`/`body`/`message`/`content` + `author`/`user`/`sender` keys) rather
    than assumed to be plain strings.

23. **[M1] Both providers' model names in wide circulation are stale — confirmed
    live.** Gemini rejects `gemini-2.5-flash` (`"no longer available to new
    users... use models/gemini-3.6-flash"`); Groq's `/models` list has no
    `llama-3.1-*` at all any more. Working models as of this build:
    `gemini-3.6-flash` (Gemini) and `openai/gpt-oss-20b` (Groq, the fast/
    high-volume model §16's table calls for). Both hardcoded as constants in
    `src/llm/providers.py`, not buried in call sites, so a future provider
    deprecation is a one-line fix. Also: Groq's `response_format:
    {"type":"json_object"}` mode 400s unless the literal word "JSON" appears
    somewhere in the prompt — every prompt in `src/query/plan.py` /
    `src/ingest/tier2_semantic.py` says "as JSON" for this reason.
24. **[M1] `WHERE ... IN [...]` is unsupported (confirmed in Phase 0, hit for
    real here)** — `src/query/traverse.py`'s subject-set membership check is
    built as an OR-chain of `c.subject_id = $sN` equality comparisons instead
    of `IN`. Also newly confirmed: **a `MATCH` node pattern that carries a
    label needs to be a named variable**, even for a pure pass-through hop
    (`(:Chunk)` mid-pattern fails with `"node labels and non-id properties
    require a named node"`; `(ch:Chunk)` is required).
25. **[M1] `AUTHORED_BY` (Document→Person) is deliberately not written yet.**
    §7.1 assigns Person IDs only "at canonicalization" (M3) — writing
    `AUTHORED_BY` at Tier 1 would require either a premature, throwaway
    Person node or pointing the edge at a Mention despite §7.3 typing it
    Document→Person. Tier 1 stores `author_raw` on the Document (already a
    frozen §7.5 field) and nothing else; M3's `canonicalize.py` is where
    `AUTHORED_BY` gets materialized once a real Person node exists. Tier 2's
    `ASSERTS`/`ABOUT` edges use the provisional-Mention pattern §8.4 already
    sanctions explicitly, so no analogous gap there.
26. **[M1] The walking skeleton ended up demonstrating real abstention, not
    just a happy path** — "Who is the CFO of Ganymede Robotics?" (an entity
    genuinely absent from the 2-document graph ingested so far) correctly
    triggers `should_abstain` with `"entity not found under any known
    alias"`, in the same test run as the successful LOOKUP. Worth keeping as
    the first line of the M7 demo script once more of the corpus is in.

## Node/edge counts

(populated starting M2)

## What's implemented (by module)

- `src/schema/models.py` — `Document`/`Claim` Pydantic models, verbatim from §7.5.
- `src/schema/ids.py` — `node_id()`/`opaque_id()` (§7.1, verbatim) plus `hydra_id()`,
  the surrogate-integer mapping required by decision #1 above.
- `docker-compose.yml` — HydraDB (built from `vendor/hydradb`'s Dockerfile) + MinIO.
  Not exercised end-to-end yet (native bring-up used instead, decision #8); to
  validate before Phase 7 ship.
- `Makefile` — `make hydradb-up`/`hydradb-down`/`venv`. Native bring-up confirmed
  working end-to-end via `make hydradb-up`.
- `THIRD_PARTY.md`, `.gitignore` (fixed), `docs/cypher-support.md`, `requirements.txt`.
- No `schema.cypher` (see decision #2) — nothing else needed at bootstrap since
  HydraDB has no user-facing DDL of any kind.
- `src/db/client.py` — Bolt session wrapper (causal/strong consistency modes).
- `src/ingest/simhash.py`, `src/ingest/adapters/{base.py,common.py,confluence.py}`,
  `src/ingest/{writer.py,tier1_structural.py,tier2_semantic.py}` — full Tier 1/Tier 2
  path for one source (Confluence). `common.py`'s `assemble_body()`/`get_title()`
  are shared by all nine adapters, not Confluence-specific (decision #22).
- `src/llm/{providers.py,router.py,cache.py}` — Gemini+Groq dual-provider pattern,
  content-addressed cache, task-based routing.
- `src/query/{anchor.py,plan.py,traverse.py,gate.py,synthesize.py,pipeline.py}` —
  full LOOKUP path, output contract per §11. MULTIHOP/CONFLICT/AGGREGATE/TEMPORAL
  classify correctly (`plan.py`) but fall through to an informative
  not-yet-implemented abstention in `pipeline.py` until M5.
- `tests/test_m1_walking_skeleton.py` — reproduces the full walking skeleton
  (ingest one real doc, extract claims, answer one LOOKUP question with citation,
  demonstrate one real abstention). Hits live HydraDB + real LLM APIs; not a fast
  unit test, a manual/CI-smoke verification.

## Environment

- `cargo`/`rustc` 1.92 (Homebrew), Docker 29.1.3, Python 3.12
  (`/Library/Frameworks/Python.framework/Versions/3.12`), `just` installed via
  Homebrew in M0. Native-library deps for building HydraDB: `cmake`, `llvm`,
  `suite-sparse`, and `cleishm/neo4j/libcypher-parser` (non-default tap).
- Project virtualenv at `.venv` (Python 3.12), deps in `requirements.txt`
  (`neo4j`, `pydantic`, `google-genai`, `groq`, `datasketch`, `networkx`, `tqdm`,
  `streamlit`, `python-dotenv`) — all import-tested successfully.
- `.env` has real `GEMINI_API_KEY` / `GROQ_API_KEY` / `HYDRADB_AUTH_TOKEN` set.
- Git remote `origin` → `github.com/AmalRaj04/ONTOS.git`, branch `main`.
