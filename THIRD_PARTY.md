# Third-party software and data

## HydraDB

- Source: https://github.com/hydra-db/hydradb
- License: **AGPL-3.0**
- Used as: a separate graph-database process/container, communicated with only over
  Bolt (port 7687) or its HTTP query API (port 8443) via the official `neo4j` Python
  driver. Its source is **never vendored, forked, or statically linked** into this
  repository — `vendor/hydradb/` is a local reference clone for development only
  (git-ignored, not distributed with this repo). `docker-compose.yml` builds a HydraDB
  container image from that external clone at build time; the image runs as an
  independent service, not as part of this codebase.
- No modifications were made to HydraDB's source.

## EnterpriseRAG-Bench

- Source: https://github.com/onyx-dot-app/EnterpriseRAG-Bench
- Citation: Sun, Y., Rahmfeld, J., Weaver, C., Desai, R., Huang, W., Butler, M. H.
  (2026). *EnterpriseRAG-Bench: A RAG Benchmark for Company Internal Knowledge.*
  arXiv:2605.05253 [cs.IR].
- License: MIT
- Used as: the ingested corpus (documents, questions, generation scaffolding) and,
  for evaluation, an adapted version of its own `answer_evaluation/
  metrics_based_eval.py` and `answer_generation/bm25_retrieval.py` scoring/baseline
  code — used unmodified in intent, adapted only to call into this repo's ingest/query
  code. `vendor/EnterpriseRAG-Bench/` (including the corpus under `generated_data/`)
  is a local reference clone, git-ignored, not distributed with this repo.

## AI coding assistance

This repository was built with assistance from **Claude Code** (Anthropic), per the
hackathon rules' disclosure requirement. All code was reviewed before commit.
