"""BUILD-SPEC.md §12 — 20-30 sampled failures, categorized, written to
eval/results/error_analysis.md. Reads eval/score.py's per-question output.

Usage: python -m eval.error_analysis [--scores eval/results/scores.json] [--n 30]
"""

import argparse
import json
import os
import random


def categorize(row: dict) -> str:
    if row["abstained"] and row["question_type"] != "info_not_found":
        return "false_abstention"
    if not row["abstained"] and row["question_type"] == "info_not_found":
        return "confabulation"
    if row["document_recall"] is not None and row["document_recall"] < 0.5:
        return "low_document_recall"
    if not row["correct"]:
        return "wrong_answer"
    return "other"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scores", default="eval/results/scores.json")
    parser.add_argument("--out", default="eval/results/error_analysis.md")
    parser.add_argument("--n", type=int, default=30)
    args = parser.parse_args()

    with open(args.scores) as f:
        scores = json.load(f)

    failures = [r for r in scores["per_question"] if not r["correct"]]
    random.seed(42)
    sample = random.sample(failures, min(args.n, len(failures)))

    by_category: dict[str, list[dict]] = {}
    for r in sample:
        by_category.setdefault(categorize(r), []).append(r)

    lines = ["# Error analysis\n", f"{len(sample)} of {len(failures)} total failures sampled "
             f"(out of {scores['overall']['total_questions_scored']} questions scored).\n"]
    for category, rows in sorted(by_category.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"\n## {category} ({len(rows)})\n")
        for r in rows:
            lines.append(
                f"- `{r['question_id']}` ({r['question_type']}): "
                f"abstained={r['abstained']}, recall={r['document_recall']}, "
                f"reason: {r['judge_reason']}"
            )

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[error_analysis] {len(sample)} failures categorized -> {args.out}")
    for category, rows in by_category.items():
        print(f"  {category}: {len(rows)}")


if __name__ == "__main__":
    main()
