"""BUILD-SPEC.md §12 scoring. Reads eval/run_eval.py's answers JSONL against the
real questions.jsonl gold schema and produces:

- Per-category accuracy (question_type breakdown).
- Document recall (answer document_ids vs expected_doc_ids — pure set math, no
  LLM needed).
- Answer correctness via a single LLM-judge call per question (Groq — cheap/
  high-volume, per BUILD-SPEC.md §16's provider split; a lighter-weight version
  of vendor/EnterpriseRAG-Bench's own 3-judge consensus flow in
  src/scripts/answer_evaluation/metrics_based_eval.py, which depends on that
  repo's own LLM client/env config and a multi-run document-correction flow —
  out of reach to fully adapt in the build's remaining time, so this reimplements
  the judge call directly against our own LLMRouter instead).
- Three abstention numbers (precision, recall, false-abstention-rate), using the
  `info_not_found` question_type (20 questions, `expected_doc_ids: []`) as the
  should-abstain ground truth — every other category has a real gold answer.
- Confabulation rate: fraction of `info_not_found` questions answered *without*
  abstaining — a direct, LLM-free proxy for "confidently invented an answer
  where none exists," which is what BUILD-SPEC.md's planning doc 07 names as the
  metric a vector-only baseline is worst on.

Usage: python -m eval.score [--answers eval/results/answers.jsonl] [--out eval/results/scores.json]
"""

import argparse
import json
import os
from collections import defaultdict

from dotenv import load_dotenv

from src.llm.router import LLMRouter

QUESTIONS_PATH = "vendor/EnterpriseRAG-Bench/questions.jsonl"
EXTRA_QUESTIONS_PATH = "vendor/EnterpriseRAG-Bench/extra_questions.jsonl"

_JUDGE_PROMPT = """You are grading whether a candidate answer is aligned with a gold \
answer, for the same question. Return JSON only.

QUESTION: {question}

GOLD ANSWER: {gold_answer}

CANDIDATE ANSWER: {candidate}

Judge whether the candidate is substantively aligned with the gold answer (same \
core facts, not necessarily identical wording). Return JSON:
{{"aligned": true|false, "reason": "one sentence"}}
"""


def load_questions() -> dict[str, dict]:
    questions = {}
    for path in (QUESTIONS_PATH, EXTRA_QUESTIONS_PATH):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                q = json.loads(line)
                questions[q["question_id"]] = q
    return questions


def load_answers(path: str) -> dict[str, dict]:
    answers = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            answers[row["question_id"]] = row
    return answers


def judge_correctness(router: LLMRouter, question: str, gold_answer: str, candidate: str) -> tuple[bool, str]:
    if not candidate:
        return False, "no answer given (abstained or empty)"
    prompt = _JUDGE_PROMPT.format(question=question, gold_answer=gold_answer, candidate=candidate)
    result = router.complete(prompt, task="eval_judge")
    return bool(result.get("aligned", False)), str(result.get("reason", ""))


def score_all(answers_path: str) -> dict:
    load_dotenv()
    questions = load_questions()
    answers = load_answers(answers_path)
    router = LLMRouter()

    per_question = []
    for qid, ans in answers.items():
        q = questions.get(qid)
        if not q:
            continue
        expected = set(q.get("expected_doc_ids") or [])
        got = set(ans.get("document_ids") or [])
        recall = (len(got & expected) / len(expected)) if expected else None

        aligned, reason = judge_correctness(router, q["question"], q.get("gold_answer", ""), ans.get("answer", ""))

        per_question.append(
            {
                "question_id": qid,
                "question_type": q.get("question_type"),
                "abstained": ans.get("abstained", False),
                "document_recall": recall,
                "correct": aligned,
                "judge_reason": reason,
            }
        )

    by_type = defaultdict(list)
    for r in per_question:
        by_type[r["question_type"]].append(r)

    category_scores = {}
    for qtype, rows in by_type.items():
        n = len(rows)
        recalls = [r["document_recall"] for r in rows if r["document_recall"] is not None]
        category_scores[qtype] = {
            "count": n,
            "accuracy_pct": round(100 * sum(1 for r in rows if r["correct"]) / n, 1) if n else 0.0,
            "avg_document_recall_pct": round(100 * sum(recalls) / len(recalls), 1) if recalls else None,
            "abstention_rate_pct": round(100 * sum(1 for r in rows if r["abstained"]) / n, 1) if n else 0.0,
        }

    info_not_found = by_type.get("info_not_found", [])
    should_abstain_total = len(info_not_found)
    other_rows = [r for r in per_question if r["question_type"] != "info_not_found"]
    should_not_abstain_total = len(other_rows)

    abstained_should = sum(1 for r in info_not_found if r["abstained"])
    abstained_should_not = sum(1 for r in other_rows if r["abstained"])
    total_abstained = abstained_should + abstained_should_not

    abstention_precision = (abstained_should / total_abstained) if total_abstained else None
    abstention_recall = (abstained_should / should_abstain_total) if should_abstain_total else None
    false_abstention_rate = (abstained_should_not / should_not_abstain_total) if should_not_abstain_total else None
    confabulation_rate = (
        sum(1 for r in info_not_found if not r["abstained"]) / should_abstain_total
        if should_abstain_total
        else None
    )

    n_total = len(per_question)
    overall = {
        "total_questions_scored": n_total,
        "overall_accuracy_pct": round(100 * sum(1 for r in per_question if r["correct"]) / n_total, 1) if n_total else 0.0,
        "abstention_precision": round(abstention_precision, 3) if abstention_precision is not None else None,
        "abstention_recall": round(abstention_recall, 3) if abstention_recall is not None else None,
        "false_abstention_rate": round(false_abstention_rate, 3) if false_abstention_rate is not None else None,
        "confabulation_rate": round(confabulation_rate, 3) if confabulation_rate is not None else None,
    }

    return {"overall": overall, "by_category": category_scores, "per_question": per_question}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", default="eval/results/answers.jsonl")
    parser.add_argument("--out", default="eval/results/scores.json")
    args = parser.parse_args()

    result = score_all(args.answers)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result["overall"], indent=2))
    print(f"[score] full breakdown -> {args.out}")


if __name__ == "__main__":
    main()
