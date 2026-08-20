"""Resolve question entities against the graph — BUILD-SPEC.md §11 step 1. Uses the
*same* normalization as src/resolution/normalize.py (M3) so question-side and
corpus-side matching can never drift apart; M1 implemented a local
`normalize_surface` here since resolution.normalize.py didn't exist yet. M3 built
its normalization in `src/resolution/normalize.py` instead (a richer module —
soundex, nicknames, given/surname splitting — needed for blocking/scoring, not just
a lookup key), so `normalize_surface` below now delegates to it directly rather
than the two staying separate implementations.

An anchor also resolves through `data/er_alias_map.json` (src/resolution/run_er.py's
output: normalized alias string -> canonical Person id) before falling back to
literal Claim subject_id/object_id text — this is what makes anchor resolution
entity-resolution-backed rather than exact-string-only, e.g. a question naming
someone by display name resolves to the same canonical_id their email/handle
mentions across other sources already collapsed into.

An anchor that fails to resolve at all is the earliest, cheapest abstention signal —
BUILD-SPEC.md is explicit that this must be surfaced, not silently discarded.
"""

import json
import os
from dataclasses import dataclass
from functools import lru_cache

from src.db.client import HydraClient
from src.resolution.normalize import normalize_name

ALIAS_MAP_PATH = "data/er_alias_map.json"


def normalize_surface(text: str) -> str:
    return normalize_name(text) or " ".join(text.strip().lower().split())


@lru_cache(maxsize=1)
def _load_alias_map() -> dict[str, str]:
    # ONTOS_DISABLE_ER=1 is eval/run_eval.py's ER-ablation switch (BUILD-SPEC.md
    # §12: "run the eval with resolution disabled... vs enabled, report the
    # multi-hop delta") — every mention stays its own entity, i.e. anchor/
    # traversal resolution falls back to literal Claim subject_id/object_id text.
    if os.environ.get("ONTOS_DISABLE_ER") == "1":
        return {}
    if not os.path.exists(ALIAS_MAP_PATH):
        return {}
    with open(ALIAS_MAP_PATH) as f:
        return json.load(f)


@dataclass
class Anchor:
    surface: str
    normalized: str
    resolved: bool
    subject_ids: list[str]  # provisional subject_id/object_id/surface_norm matches
    canonical_id: str | None = None  # resolved via data/er_alias_map.json, if any


def resolve_anchors(client: HydraClient, candidate_names: list[str]) -> list[Anchor]:
    alias_map = _load_alias_map()
    anchors = []
    for name in candidate_names:
        norm = normalize_surface(name)
        canonical_id = alias_map.get(name.strip().lower()) or alias_map.get(norm)

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

        resolved = bool(subject_ids) or canonical_id is not None
        anchors.append(
            Anchor(
                surface=name,
                normalized=norm,
                resolved=resolved,
                subject_ids=sorted(subject_ids),
                canonical_id=canonical_id,
            )
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
