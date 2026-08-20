"""BUILD-SPEC.md §12 — the ER ablation: eval with resolution disabled (every
mention its own entity) vs enabled, reporting the multi-hop delta. "This is the
single most important number in the project — it's a measured claim about the
track's own stated hard problem."

Runs eval/run_eval.py's question set twice against the live pipeline — once
normally, once with `ONTOS_DISABLE_ER=1` (src/query/anchor.py's ablation switch:
anchor/traversal resolution falls back to literal Claim subject_id/object_id text,
skipping data/er_alias_map.json entirely) — then scores both and reports the delta
specifically on questions our own plan.py classified as MULTIHOP (not the gold
question_type, which has no multi-hop label of its own).

Usage: EVAL_LIMIT=150 python -m eval.ablations.er_ablation
"""

import json
import os
import time

from dotenv import load_dotenv

from src.db.client import HydraClient
from src.llm.router import LLMRouter
from src.query import anchor as anchor_module
from src.query.pipeline import answer_question

WITH_ER_PATH = "eval/results/answers.jsonl"
NO_ER_PATH = "eval/results/answers_no_er.jsonl"
REPORT_PATH = "eval/results/er_ablation.json"


def _run(out_path: str, disable_er: bool, limit: int | None) -> None:
    from eval.run_eval import load_done_qids, load_questions

    if disable_er:
        os.environ["ONTOS_DISABLE_ER"] = "1"
    else:
        os.environ.pop("ONTOS_DISABLE_ER", None)
    anchor_module._load_alias_map.cache_clear()

    questions = load_questions(limit, include_extra=False)
    done = load_done_qids(out_path)
    todo = [q for q in questions if q["question_id"] not in done]
    print(f"[ablation] {'ER-disabled' if disable_er else 'ER-enabled'}: "
          f"{len(todo)}/{len(questions)} to run -> {out_path}", flush=True)

    client = HydraClient()
    router = LLMRouter()
    t0 = time.monotonic()
    with open(out_path, "a") as out:
        for i, q in enumerate(todo, 1):
            qid = q["question_id"]
            try:
                result = answer_question(client, router, qid, q["question"], consistency="strong")
            except Exception as e:
                print(f"[ablation] FAILED {qid}: {e}", flush=True)
                continue
            document_ids = sorted({c["native_id"] for c in result["citations"] if c.get("native_id")})
            row = {
                "question_id": qid,
                "answer": result["answer"] or "",
                "document_ids": document_ids,
                "abstained": result["abstained"],
                "plan_class": result["plan_class"],
                "path_count": result["traversal"]["path_count"],
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            if i % 20 == 0:
                print(f"[ablation] {i}/{len(todo)} ({time.monotonic()-t0:.0f}s)", flush=True)
    client.close()


def _load(path: str) -> dict[str, dict]:
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                row = json.loads(line)
                rows[row["question_id"]] = row
    return rows


def compute_delta() -> dict:
    from eval.score import judge_correctness, load_questions

    with_er = _load(WITH_ER_PATH)
    no_er = _load(NO_ER_PATH)
    questions = load_questions()
    router = LLMRouter()

    common_qids = set(with_er) & set(no_er)
    multihop_qids = {
        qid for qid in common_qids
        if with_er[qid].get("plan_class") == "MULTIHOP" or no_er[qid].get("plan_class") == "MULTIHOP"
    }

    def _accuracy(rows: dict[str, dict], qids: set[str]) -> float:
        if not qids:
            return 0.0
        correct = 0
        for qid in qids:
            q = questions.get(qid)
            if not q:
                continue
            aligned, _ = judge_correctness(router, q["question"], q.get("gold_answer", ""), rows[qid].get("answer", ""))
            if aligned:
                correct += 1
        return correct / len(qids)

    overall_with_er = _accuracy(with_er, common_qids)
    overall_no_er = _accuracy(no_er, common_qids)
    multihop_with_er = _accuracy(with_er, multihop_qids)
    multihop_no_er = _accuracy(no_er, multihop_qids)

    report = {
        "common_questions_scored": len(common_qids),
        "multihop_questions_scored": len(multihop_qids),
        "overall_accuracy_with_er": round(overall_with_er, 3),
        "overall_accuracy_without_er": round(overall_no_er, 3),
        "overall_delta": round(overall_with_er - overall_no_er, 3),
        "multihop_accuracy_with_er": round(multihop_with_er, 3),
        "multihop_accuracy_without_er": round(multihop_no_er, 3),
        "multihop_delta": round(multihop_with_er - multihop_no_er, 3),
    }
    return report


def main() -> None:
    load_dotenv()
    limit = int(os.environ["EVAL_LIMIT"]) if os.environ.get("EVAL_LIMIT") else None

    _run(WITH_ER_PATH, disable_er=False, limit=limit)
    _run(NO_ER_PATH, disable_er=True, limit=limit)

    print("[ablation] scoring both runs...", flush=True)
    report = compute_delta()
    os.makedirs("eval/results", exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    print(f"[ablation] report -> {REPORT_PATH}")


if __name__ == "__main__":
    main()
