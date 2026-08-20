"""BUILD-SPEC.md §10 step 2 — Classify. Before treating a detect.py candidate as a
true conflict, rule out (in order):

1. Temporal succession — gap > 14 days between the two claims' asserted_at with no
   validity overlap (this Claim model has no explicit validity-end field, so
   "overlap" reduces to the gap check) -> the later claim SUPERSEDES the earlier
   one; not a conflict.
2. Granularity — one object value is a case-insensitive substring of the other
   (e.g. "Q3" vs "Q3 2024") -> compatible, not a conflict.

Only the residue goes to the LLM classifier (Groq — a simple, high-volume
classification call, per BUILD-SPEC.md §16's provider split), and only what the LLM
confirms as CONTRADICTION is a true conflict trust.py needs to adjudicate."""

from datetime import datetime

from src.conflict.detect import object_key
from src.llm.router import LLMRouter

SUPERSEDE_GAP_DAYS = 14

_PROMPT = """You are classifying why two factual claims about the same subject and \
predicate disagree. Return JSON only.

Subject: {subject}
Predicate: {predicate}
Claim A ({source_a}, asserted {date_a}): {object_a}
Claim B ({source_b}, asserted {date_b}): {object_b}

Classify the relationship as exactly one of: "CONTRADICTION" (these genuinely
disagree about the same fact), "SCOPE_DIFFERENT" (different qualifiers/contexts,
e.g. different teams or environments, so both can be true), "NEGATION" (one claim
negates a broader statement, not a real factual disagreement), "DUPLICATE" (same
fact worded differently, not a disagreement).

Return JSON: {{"classification": "...", "rationale": "one sentence"}}
"""


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _rule_out_supersession(group_claims: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (residue, superseded) — for each distinct object value, keep only
    the most recent claim if an older claim for a DIFFERENT object value predates
    it by more than SUPERSEDE_GAP_DAYS with nothing in between; the older ones are
    marked superseded rather than conflicting."""
    dated = [(c, _parse_dt(c.get("asserted_at"))) for c in group_claims]
    dated.sort(key=lambda pair: pair[1] or datetime.min)

    residue = []
    superseded = []
    latest_by_object: dict[str, datetime] = {}
    # Walk oldest -> newest; if a later claim's object differs from the latest seen
    # and the gap exceeds the threshold, the earlier ones for other objects are superseded.
    if not dated:
        return [], []
    newest_dt = dated[-1][1]
    for c, dt in dated:
        if dt is None or newest_dt is None:
            residue.append(c)
            continue
        gap_days = (newest_dt - dt).days
        is_newest_object = object_key(c) == object_key(dated[-1][0])
        if not is_newest_object and gap_days > SUPERSEDE_GAP_DAYS:
            superseded.append(c)
        else:
            residue.append(c)
    # Need at least 2 distinct objects remaining to still be a candidate
    if len({object_key(c) for c in residue}) <= 1:
        return [], group_claims
    return residue, superseded


def _rule_out_granularity(group_claims: list[dict]) -> bool:
    """True if every pair of distinct object values is a granularity relationship
    (one string contains the other) — the whole group is compatible, not
    conflicting."""
    values = list({object_key(c).split(":", 1)[-1] for c in group_claims})
    if len(values) < 2:
        return True
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            a, b = values[i], values[j]
            if a not in b and b not in a:
                return False
    return True


def classify_candidate(router: LLMRouter, candidate: dict) -> dict:
    """Returns the candidate augmented with `classification` ("SUPERSEDES",
    "GRANULARITY", "CONTRADICTION", "SCOPE_DIFFERENT", "NEGATION", "DUPLICATE") and
    `claims` narrowed to the residue actually in conflict (empty/irrelevant for
    non-CONTRADICTION classifications)."""
    residue, superseded = _rule_out_supersession(candidate["claims"])
    if not residue:
        return {**candidate, "classification": "SUPERSEDES", "claims": [], "superseded": superseded, "rationale": (
            f"{len(superseded)} earlier claim(s) superseded by a later one >{SUPERSEDE_GAP_DAYS}d apart"
        )}

    if _rule_out_granularity(residue):
        return {**candidate, "classification": "GRANULARITY", "claims": [], "superseded": superseded, "rationale": (
            "distinct object values are a granularity relationship (one contains the other), not a conflict"
        )}

    # Residue: call the LLM once per pair of distinct object values represented
    # (not once per claim — keeps the call count bounded to real ambiguity).
    by_object: dict[str, dict] = {}
    for c in residue:
        by_object.setdefault(object_key(c), c)
    reps = list(by_object.values())
    if len(reps) < 2:
        return {**candidate, "classification": "DUPLICATE", "claims": [], "superseded": superseded, "rationale": "single distinct object value after ruling out supersession/granularity"}

    a, b = reps[0], reps[1]
    prompt = _PROMPT.format(
        subject=candidate["subject"],
        predicate=candidate["predicate"],
        source_a=a.get("source_system", "?"),
        date_a=a.get("asserted_at") or "unknown",
        object_a=a.get("object_literal") or a.get("object_id"),
        source_b=b.get("source_system", "?"),
        date_b=b.get("asserted_at") or "unknown",
        object_b=b.get("object_literal") or b.get("object_id"),
    )
    result = router.complete(prompt, task="conflict_classification")
    classification = result.get("classification", "CONTRADICTION")
    rationale = result.get("rationale", "")

    return {
        **candidate,
        "classification": classification,
        "claims": residue if classification == "CONTRADICTION" else [],
        "superseded": superseded,
        "rationale": rationale,
    }
