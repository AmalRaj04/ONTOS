"""Grounded generation — BUILD-SPEC.md §11 step 5. Hard rules: only use provided
claims, cite every statement, surface conflicts explicitly rather than picking
silently, and re-check absence even though the gate already ran (defence in depth).
Rule 4 below is quoted verbatim from planning doc 05 §6; the others are this file's
implementation of the principles BUILD-SPEC.md §11 step 5 states directly — doc 05's
exact wording for rules 1/2/3/5 wasn't in context when this was written and should be
verified against docs/planning/05-QUERY-ANSWERING-AND-ABSTENTION.md §6 before M5 if
the exact phrasing matters (M1 rely on the stated principles, not a re-read).
"""

from src.llm.router import LLMRouter
from src.query.traverse import ClaimHit

_PROMPT = """Answer the question using ONLY the claims listed below. Follow these
rules:
1. Use only the provided claims — do not use outside knowledge.
2. Cite the claim(s) that support every statement you make.
3. If claims conflict, surface the conflict explicitly rather than silently picking one.
4. If the claims do not answer the question, say so plainly. Do not construct a
   plausible-sounding answer from adjacent facts.
5. Keep the answer concise — one to three sentences.

Return JSON under the key "items" with exactly one object:
{{"answer": str, "cited_claim_ids": [str, ...]}}

QUESTION: {question}

CLAIMS:
{claims_text}"""


def _format_claims(claims: list[ClaimHit]) -> str:
    lines = []
    for c in claims:
        obj = c.object_literal if c.object_literal else c.object_id
        lines.append(f"- [{c.claim_id}] {c.subject_id} {c.predicate} {obj}")
    return "\n".join(lines)


def synthesize_answer(router: LLMRouter, question: str, claims: list[ClaimHit]) -> dict:
    prompt = _PROMPT.format(question=question, claims_text=_format_claims(claims))
    result = router.complete(prompt, task="answer_synthesis")
    items = result.get("items", [])
    parsed = items[0] if items else {}
    return {
        "answer": parsed.get("answer", ""),
        "cited_claim_ids": parsed.get("cited_claim_ids") or [],
    }
