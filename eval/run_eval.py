"""BUILD-SPEC.md §12 M6 evaluation harness. Resumable, writes JSONL in the
benchmark's expected submission format ({"question_id","answer","document_ids"}),
`strong` consistency (not `causal` — the query pipeline's default hot-path
consistency; §12 requires `strong` when called from eval so results are
reproducible).

`document_ids` are built from each citation's Document.native_id, which equals
the corpus's own dataset_doc_uuid ("dsid_...") for every real record (every
adapter in src/ingest/adapters/*.py sets native_id from rec["dataset_doc_uuid"]) —
exactly the id space `expected_doc_ids` in questions.jsonl uses, so no separate
doc_id<->dsid mapping table is needed.

Usage:
    python -m eval.run_eval                          # full 500 official questions
    EVAL_LIMIT=150 python -m eval.run_eval            # stratified subset (by question_type)
    EVAL_INCLUDE_EXTRA=1 python -m eval.run_eval      # also run the 100 extra_questions.jsonl
    ONTOS_DISABLE_ER=1 EVAL_OUT=eval/results/answers_no_er.jsonl python -m eval.run_eval
                                                       # ER-ablation run (see anchor.py)
"""

import json
import os
import random
import time

from dotenv import load_dotenv

from src.db.client import HydraClient
from src.llm.router import LLMRouter
from src.query.pipeline import answer_question

QUESTIONS_PATH = "vendor/EnterpriseRAG-Bench/questions.jsonl"
EXTRA_QUESTIONS_PATH = "vendor/EnterpriseRAG-Bench/extra_questions.jsonl"
DEFAULT_OUT = "eval/results/answers.jsonl"


def load_questions(limit: int | None, include_extra: bool) -> list[dict]:
    questions = []
    with open(QUESTIONS_PATH) as f:
        for line in f:
            questions.append(json.loads(line))
    if include_extra and os.path.exists(EXTRA_QUESTIONS_PATH):
        with open(EXTRA_QUESTIONS_PATH) as f:
            for line in f:
                questions.append(json.loads(line))

    if limit is None or limit >= len(questions):
        return questions

    # Stratified by question_type so a limited run still has real per-category
    # numbers instead of just whatever's first in file order (the file is
    # sorted by category, so a naive head(limit) would miss most categories).
    by_type: dict[str, list[dict]] = {}
    for q in questions:
        by_type.setdefault(q.get("question_type", "unknown"), []).append(q)
    random.seed(42)
    total = len(questions)
    sampled = []
    for qtype, group in by_type.items():
        share = max(1, round(limit * len(group) / total))
        sampled.extend(random.sample(group, min(share, len(group))))
    random.shuffle(sampled)
    return sampled[:limit]


def load_done_qids(out_path: str) -> set[str]:
    if not os.path.exists(out_path):
        return set()
    done = set()
    with open(out_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["question_id"])
            except Exception:
                continue
    return done


def main() -> None:
    load_dotenv()
    limit = int(os.environ["EVAL_LIMIT"]) if os.environ.get("EVAL_LIMIT") else None
    include_extra = os.environ.get("EVAL_INCLUDE_EXTRA") == "1"
    out_path = os.environ.get("EVAL_OUT", DEFAULT_OUT)

    questions = load_questions(limit, include_extra)
    done = load_done_qids(out_path)
    todo = [q for q in questions if q["question_id"] not in done]
    print(f"[eval] {len(questions)} questions selected, {len(done)} already done, "
          f"{len(todo)} to run -> {out_path}", flush=True)
    if os.environ.get("ONTOS_DISABLE_ER") == "1":
        print("[eval] ONTOS_DISABLE_ER=1 — ER ablation run (alias_map disabled)", flush=True)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    client = HydraClient()
    router = LLMRouter()

    t0 = time.monotonic()
    n = 0
    abstained = 0
    with open(out_path, "a") as out:
        for q in todo:
            qid = q["question_id"]
            try:
                result = answer_question(client, router, qid, q["question"], consistency="strong")
            except Exception as e:
                print(f"[eval] FAILED {qid}: {e}", flush=True)
                continue
            document_ids = sorted({c["native_id"] for c in result["citations"] if c.get("native_id")})
            row = {
                "question_id": qid,
                "answer": result["answer"] or "",
                "document_ids": document_ids,
                # extra fields beyond the submission schema — harmless for the
                # benchmark's own scorer (which reads question_id/answer/
                # document_ids), useful for our own error analysis (M6 DoD).
                "abstained": result["abstained"],
                "plan_class_path_count": result["traversal"]["path_count"],
                "conflicts": result["conflicts"],
                "latency_ms": result["graph_stats"]["latency_ms"],
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            n += 1
            if result["abstained"]:
                abstained += 1
            if n % 20 == 0:
                elapsed = time.monotonic() - t0
                print(f"[eval] {n}/{len(todo)} answered ({abstained} abstained) elapsed={elapsed:.0f}s", flush=True)

    client.close()
    print(f"[eval] DONE {n} answered, {abstained} abstained, elapsed={time.monotonic()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
