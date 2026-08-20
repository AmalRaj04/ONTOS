"""BUILD-SPEC.md §10 step 3 — Trust function.

trust = 0.30*authority + 0.25*recency + 0.25*corroboration
      + 0.10*specificity + 0.05*extraction_confidence - 0.15*staleness_penalty

- authority: static per-source-system table (Confluence/Jira/Linear highest,
  Slack lowest) — the spec notes this should invert for "who's currently on this"
  type facts; not implemented given the build's time budget, noted here rather
  than silently applied uniformly.
- recency: exponential decay (half-life 90d) on asserted_at, relative to the most
  recent asserted_at seen anywhere in the conflict group — not wall-clock "now",
  since the corpus's dates are a fixed synthetic window, not real time; what
  matters for adjudication is relative freshness within the group, which this
  preserves.
- corroboration: distinct source_systems asserting the same object value within
  the group (1 source -> 0.5, each additional distinct source -> +0.25, capped at
  1.0) — a simplified stand-in for "collapse NEAR_DUPLICATE_OF clusters to one
  vote, weight cross-system over within-system": distinct source_systems already
  implies cross-system by construction, and multiple claims from the very same
  source_system don't add corroboration weight under this formula, which is the
  behavior the spec is actually protecting against (naive same-system vote
  stuffing).
- specificity: 1.0 for a typed literal object (date/number/status string), 0.6 for
  an entity-reference object — a literal is a more specific, checkable claim.
- staleness_penalty: a slower decay (half-life 365d) than recency's 90d, so it
  only meaningfully penalizes claims that are clearly over a year old relative to
  the group's freshest claim, distinct from recency's faster reward for very new
  claims.

margin (top trust - second trust) < 0.12 -> CONTESTED, present both. Below
min_winner_trust=0.40 -> abstain on the conflict entirely rather than presenting a
weak winner.
"""

from collections import defaultdict
from datetime import datetime

from src.conflict.detect import object_key

AUTHORITY = {
    "confluence": 0.95,
    "gdrive": 0.85,
    "jira": 0.85,
    "linear": 0.85,
    "github": 0.80,
    "hubspot": 0.80,
    "gmail": 0.65,
    "fireflies": 0.55,
    "slack": 0.45,
}
RECENCY_HALF_LIFE_DAYS = 90
STALENESS_HALF_LIFE_DAYS = 365
MARGIN_THRESHOLD = 0.12
MIN_WINNER_TRUST = 0.40


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _decay(days: float, half_life: float) -> float:
    return 0.5 ** (max(days, 0) / half_life)


def score_object_value(claims_for_value: list[dict], reference_dt: datetime | None) -> tuple[float, dict]:
    sources = {c.get("source_system", "?") for c in claims_for_value}
    corroboration = min(1.0, 0.5 + 0.25 * (len(sources) - 1))

    dated = [(c, _parse_dt(c.get("asserted_at"))) for c in claims_for_value]
    dated = [(c, dt) for c, dt in dated if dt is not None] or [(claims_for_value[0], None)]
    best_claim, best_dt = max(dated, key=lambda pair: pair[1] or datetime.min)

    authority = AUTHORITY.get(best_claim.get("source_system"), 0.5)

    if best_dt is not None and reference_dt is not None:
        days = (reference_dt - best_dt).days
    else:
        days = 180  # unknown date: moderate penalty, not zero and not max
    recency = _decay(days, RECENCY_HALF_LIFE_DAYS)
    staleness_penalty = 1.0 - _decay(days, STALENESS_HALF_LIFE_DAYS)

    specificity = 1.0 if best_claim.get("object_literal") else 0.6
    extraction_confidence = float(best_claim.get("extraction_confidence", 0.5))

    trust = (
        0.30 * authority
        + 0.25 * recency
        + 0.25 * corroboration
        + 0.10 * specificity
        + 0.05 * extraction_confidence
        - 0.15 * staleness_penalty
    )
    trust = max(0.0, min(1.0, trust))
    breakdown = {
        "authority": authority,
        "recency": recency,
        "corroboration": corroboration,
        "specificity": specificity,
        "extraction_confidence": extraction_confidence,
        "staleness_penalty": staleness_penalty,
        "sources": sorted(sources),
    }
    return trust, breakdown


def adjudicate(candidate: dict) -> dict:
    """candidate: a classify.py CONTRADICTION-classified group. Returns
    resolution_status (RESOLVED/CONTESTED/UNRESOLVED), winner (object value or
    None), margin, and a rationale string — persisted verbatim as the ConflictSet's
    rationale (BUILD-SPEC.md §10 step 4: "adjudicate once, not per query")."""
    by_value: dict[str, list[dict]] = defaultdict(list)
    for c in candidate["claims"]:
        by_value[object_key(c)].append(c)

    all_dts = [_parse_dt(c.get("asserted_at")) for c in candidate["claims"]]
    all_dts = [d for d in all_dts if d is not None]
    reference_dt = max(all_dts) if all_dts else None

    scored = []
    for value, claims_for_value in by_value.items():
        trust, breakdown = score_object_value(claims_for_value, reference_dt)
        scored.append((value, trust, breakdown, claims_for_value))
    scored.sort(key=lambda t: t[1], reverse=True)

    top = scored[0]
    second = scored[1] if len(scored) > 1 else None
    margin = top[1] - second[1] if second else top[1]

    if second and margin < MARGIN_THRESHOLD:
        status = "CONTESTED"
        winner = None
        rationale = (
            f"Top two object values for {candidate['subject']}/{candidate['predicate']} "
            f"scored {top[1]:.2f} ({top[0]}) vs {second[1]:.2f} ({second[0]}) — margin "
            f"{margin:.2f} < {MARGIN_THRESHOLD}, presenting both rather than picking a winner."
        )
    elif top[1] < MIN_WINNER_TRUST:
        status = "UNRESOLVED"
        winner = None
        rationale = (
            f"Best-supported value ({top[0]}) only reached trust {top[1]:.2f} < "
            f"{MIN_WINNER_TRUST} — abstaining rather than presenting a weak winner."
        )
    else:
        status = "RESOLVED"
        winner = top[0]
        rationale = (
            f"{top[0]} wins with trust {top[1]:.2f} "
            f"(authority={top[2]['authority']:.2f}, recency={top[2]['recency']:.2f}, "
            f"corroboration={top[2]['corroboration']:.2f}, sources={top[2]['sources']}) "
            f"vs next-best {second[1]:.2f}" if second else f"{top[0]} is the only supported value, trust {top[1]:.2f}"
        )

    return {
        **candidate,
        "resolution_status": status,
        "winner": winner,
        "margin": round(margin, 4),
        "trust_rationale": rationale,
        "scored_values": [
            {"value": v, "trust": round(t, 4), "breakdown": b, "claim_count": len(c)}
            for v, t, b, c in scored
        ],
    }
