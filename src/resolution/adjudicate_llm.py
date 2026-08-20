"""BUILD-SPEC.md §9 step 5 — Adjudicate. LLM call only for pairs in the uncertain
score band (~0.45-0.72), with graph evidence counts in the prompt. Per the spec,
this should land at roughly 3-8% of candidates; a much higher fraction signals the
scorer needs tuning, not more LLM budget — run_er.py logs the actual fraction so
that check is visible, not assumed."""

from src.llm.router import LLMRouter
from src.resolution.records import IdentityRecord

LOWER_BAND = 0.45
UPPER_BAND = 0.72

_PROMPT = """You are adjudicating whether two identity mentions from an enterprise \
knowledge graph refer to the SAME real person. Answer as JSON only.

Mention A: "{raw_a}" (from {source_a}, type {kind_a})
Mention B: "{raw_b}" (from {source_b}, type {kind_b})

Evidence:
- email_exact match: {email_exact}
- handle_match: {handle_match}
- name_similarity (token overlap): {name_similarity:.2f}
- shared-context (cooccurrence_path) score: {cooccurrence_path:.2f}
- same team/department: {team_overlap}
- role_consistency (matches employee roster): {role_consistency:.2f}
- automated pairwise score: {score:.2f} (borderline band, needs a human-like call)

Return JSON: {{"same_person": true|false, "confidence": 0.0-1.0, "reason": "..."}}
"""


def in_uncertain_band(score: float) -> bool:
    return LOWER_BAND <= score < UPPER_BAND


def adjudicate_pair(
    router: LLMRouter,
    ri: IdentityRecord,
    rj: IdentityRecord,
    score: float,
    features: dict[str, float],
) -> tuple[bool, float, str]:
    prompt = _PROMPT.format(
        raw_a=ri.raw,
        source_a=ri.source_system,
        kind_a=ri.kind,
        raw_b=rj.raw,
        source_b=rj.source_system,
        kind_b=rj.kind,
        email_exact=bool(features.get("email_exact")),
        handle_match=bool(features.get("handle_match")),
        name_similarity=features.get("name_similarity", 0.0),
        cooccurrence_path=features.get("cooccurrence_path", 0.0),
        team_overlap=bool(features.get("team_overlap")),
        role_consistency=features.get("role_consistency", 0.5),
        score=score,
    )
    result = router.complete(prompt, task="er_adjudication")
    same = bool(result.get("same_person", False))
    confidence = float(result.get("confidence", 0.5))
    reason = str(result.get("reason", ""))
    return same, confidence, reason
