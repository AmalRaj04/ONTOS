"""Resolve question entities against the graph — BUILD-SPEC.md §11 step 1. Uses the
*same* normalization as src/resolution/normalize.py (M3) so question-side and
corpus-side matching can never drift apart; M1 implements the shared normalize step
directly here since resolution.normalize.py doesn't exist yet, and M3 will import
from this module rather than duplicate it.

An anchor that fails to resolve at all is the earliest, cheapest abstention signal —
BUILD-SPEC.md is explicit that this must be surfaced, not silently discarded.
"""

from dataclasses import dataclass

from src.db.client import HydraClient


def normalize_surface(text: str) -> str:
    return " ".join(text.strip().lower().split())


@dataclass
class Anchor:
    surface: str
    normalized: str
    resolved: bool
    subject_ids: list[str]  # provisional subject_id/object_id/surface_norm matches


def resolve_anchors(client: HydraClient, candidate_names: list[str]) -> list[Anchor]:
    anchors = []
    for name in candidate_names:
        norm = normalize_surface(name)
        rows = client.run_read(
            "MATCH (c:Claim) WHERE c.subject_id = $norm OR c.object_id = $norm "
            "RETURN DISTINCT c.subject_id AS s, c.object_id AS o",
            norm=norm,
        )
        subject_ids = set()
        for r in rows:
            if r["s"] == norm:
                subject_ids.add(r["s"])
            if r["o"] == norm:
                subject_ids.add(r["o"])
        anchors.append(
            Anchor(surface=name, normalized=norm, resolved=bool(subject_ids), subject_ids=sorted(subject_ids))
        )
    return anchors


def nearest_known_name(client: HydraClient, normalized: str, limit: int = 3) -> list[str]:
    """Fuzzy near-miss lookup for an informative abstention message (§11 step 4:
    "fuzzy-match the unresolved anchor against canonical names"). M1: cheap
    substring match over subject_id — good enough until M3's real fuzzy scorer
    exists."""
    rows = client.run_read(
        "MATCH (c:Claim) WHERE c.subject_id STARTS WITH $prefix "
        "RETURN DISTINCT c.subject_id AS s LIMIT $limit",
        prefix=normalized[:3] if len(normalized) >= 3 else normalized,
        limit=limit,
    )
    return [r["s"] for r in rows]
