"""BUILD-SPEC.md §11 step 3. Uses `causal` consistency, the default hot path per
§11 step 3 ("Use causal consistency on this path (default hot path)").

`WHERE ... IN [...]` is not supported by HydraDB's Cypher subset (confirmed live,
docs/cypher-support.md) — subject-set membership is built as an OR-chain of equality
comparisons instead. `Claim.trust` is only populated once M4's trust function runs
on a subject+predicate that lands in a real conflict (src/conflict/trust.py writes
its scores onto the ConflictSet, not back onto individual Claim nodes) — a claim
outside any conflict has `trust=None`, which is the correct/expected state, not a
bug — the abstention gate (src/query/gate.py) treats "no trust yet" the same as
"low trust" via `max_trust < 0.35`, and pipeline.py falls back to
extraction_confidence when trust is None.

MULTIHOP is implemented as a bounded Claim-chain BFS (subject_id <-> object_id,
each Claim treated as a directed edge) rather than a live `algo.MSpaths` call.
`algo.MSpaths` needs real graph edges between the anchor nodes; this graph reifies
relationships as Claim *nodes* with subject_id/object_id string properties (per
BUILD-SPEC.md §7.5's frozen Claim model), and the provisional claim_subject/
claim_object Mention nodes tier2_semantic.py writes are a separate set from the
ones src/resolution/run_er.py resolved to Person nodes — wiring RESOLVES_TO onto
those too, so a real Person-to-Person edge path would exist for MSpaths to walk,
was out of reach in the build's remaining time. The BFS below still delivers real
multi-hop, entity-resolution-backed reasoning (each hop resolves anchor text
through the same data/er_alias_map.json anchor.py uses) via the query patterns
already proven fast at this graph's scale — noted here as a time-budget
substitution, not a silent one."""

from dataclasses import dataclass
from functools import lru_cache

from src.db.client import HydraClient
from src.query.anchor import Anchor, _load_alias_map, normalize_surface
from src.schema.ids import hydra_id


@dataclass
class ClaimHit:
    claim_id: str
    predicate: str
    subject_id: str
    object_id: str | None
    object_literal: str | None
    extraction_confidence: float
    evidence_chunk_id: str
    trust: float | None


def _subject_or_clause(n: int) -> str:
    return " OR ".join(f"c.subject_id = $s{i}" for i in range(n))


@dataclass
class TraversalResult:
    claims: list[ClaimHit]
    path_count: int
    max_hops: int


def traverse_lookup(
    client: HydraClient, anchors: list[Anchor], predicate_hint: str | None
) -> TraversalResult:
    resolved = sorted({sid for a in anchors if a.resolved for sid in a.subject_ids})
    if not resolved:
        return TraversalResult(claims=[], path_count=0, max_hops=0)

    params = {f"s{i}": s for i, s in enumerate(resolved)}
    where = f"({_subject_or_clause(len(resolved))})"
    if predicate_hint:
        where += " AND c.predicate = $pred"
        params["pred"] = predicate_hint

    rows = client.run_read(
        f"MATCH (c:Claim) WHERE {where} "
        "RETURN c.claim_id AS claim_id, c.predicate AS predicate, "
        "c.subject_id AS subject_id, c.object_id AS object_id, "
        "c.object_literal AS object_literal, "
        "c.extraction_confidence AS extraction_confidence, "
        "c.evidence_chunk_id AS evidence_chunk_id",
        **params,
    )

    claims = [
        ClaimHit(
            claim_id=r["claim_id"],
            predicate=r["predicate"],
            subject_id=r["subject_id"],
            object_id=r["object_id"] or None,
            object_literal=r["object_literal"] or None,
            extraction_confidence=r["extraction_confidence"],
            evidence_chunk_id=r["evidence_chunk_id"],
            trust=None,  # set by M4's trust function; not yet computed
        )
        for r in rows
    ]
    return TraversalResult(claims=claims, path_count=len(claims), max_hops=1 if claims else 0)


# --- M5: MULTIHOP / CONFLICT / AGGREGATE / TEMPORAL -------------------------


@lru_cache(maxsize=1)
def _reverse_alias_map() -> dict[str, list[str]]:
    rev: dict[str, list[str]] = {}
    for alias, canonical_id in _load_alias_map().items():
        rev.setdefault(canonical_id, []).append(alias)
    return rev


def _match_keys_for_anchor(anchor: Anchor, cap: int = 30) -> list[str]:
    """Every raw text form a resolved anchor could appear as on a Claim's
    subject_id/object_id — the anchor's own normalized text, any literal Claim
    matches already found, and (if it resolved to a canonical Person) every alias
    ER collapsed into that Person, via the same data/er_alias_map.json anchor.py
    uses. This is what lets multi-hop matching cross source-system naming
    differences instead of only matching exact text."""
    keys = set(anchor.subject_ids) | {anchor.normalized}
    if anchor.canonical_id:
        keys.add(anchor.canonical_id)
        keys.update(_reverse_alias_map().get(anchor.canonical_id, [])[:cap])
    return sorted(keys)[:cap]


def _or_clause(field: str, n: int, prefix: str) -> str:
    return " OR ".join(f"c.{field} = ${prefix}{i}" for i in range(n))


def _row_to_claimhit(r: dict) -> ClaimHit:
    return ClaimHit(
        claim_id=r["claim_id"],
        predicate=r["predicate"],
        subject_id=r["subject_id"],
        object_id=r["object_id"] or None,
        object_literal=r["object_literal"] or None,
        extraction_confidence=r["extraction_confidence"],
        evidence_chunk_id=r["evidence_chunk_id"],
        trust=None,
    )


def _fetch_claims_by_keys(client: HydraClient, keys: list[str], predicate: str | None = None) -> list[dict]:
    if not keys:
        return []
    params = {f"k{i}": k for i, k in enumerate(keys)}
    where = f"(({_or_clause('subject_id', len(keys), 'k')}) OR ({_or_clause('object_id', len(keys), 'k')}))"
    if predicate:
        where += " AND c.predicate = $pred"
        params["pred"] = predicate
    return client.run_read(
        f"MATCH (c:Claim) WHERE {where} "
        "RETURN c.claim_id AS claim_id, c.predicate AS predicate, "
        "c.subject_id AS subject_id, c.object_id AS object_id, "
        "c.object_literal AS object_literal, c.extraction_confidence AS extraction_confidence, "
        "c.evidence_chunk_id AS evidence_chunk_id, c.asserted_at AS asserted_at",
        **params,
    )


def traverse_multihop(
    client: HydraClient, anchors: list[Anchor], predicate_hint: str | None, max_hops: int = 3
) -> TraversalResult:
    """Bounded BFS over Claims-as-edges between the first two resolved anchors —
    see the module docstring for why this replaces a live algo.MSpaths call."""
    resolved = [a for a in anchors if a.resolved]
    if len(resolved) < 2:
        return TraversalResult(claims=[], path_count=0, max_hops=0)

    start_keys = set(_match_keys_for_anchor(resolved[0]))
    target_keys = set(_match_keys_for_anchor(resolved[1]))

    visited = set(start_keys)
    frontier = set(start_keys)
    seen_claims: dict[str, dict] = {}
    reached_at = 0

    for hop in range(1, max_hops + 1):
        rows = _fetch_claims_by_keys(client, sorted(frontier), predicate=predicate_hint if hop == 1 else None)
        next_frontier = set()
        for r in rows:
            seen_claims[r["claim_id"]] = r
            for key in (r["subject_id"], r["object_id"]):
                if key and key not in visited:
                    next_frontier.add(key)
        if next_frontier & target_keys:
            reached_at = hop
            break
        if not next_frontier:
            break
        visited |= next_frontier
        frontier = next_frontier

    if not reached_at:
        return TraversalResult(claims=[], path_count=0, max_hops=0)

    claims = [_row_to_claimhit(r) for r in seen_claims.values()]
    return TraversalResult(claims=claims, path_count=len(claims), max_hops=reached_at)


@dataclass
class ConflictHit:
    conflict_id: str
    subject: str
    predicate: str
    resolution_status: str
    winner: str | None
    margin: float
    rationale: str


def traverse_conflict(
    client: HydraClient, anchors: list[Anchor], predicate_hint: str | None
) -> tuple[TraversalResult, list[ConflictHit]]:
    """Direct ConflictSet read (BUILD-SPEC.md §11 step 3) — src/conflict/run_conflicts.py
    already adjudicated every conflict at ingest time, so this is a lookup, not a
    live computation."""
    resolved = [a for a in anchors if a.resolved]
    keys = sorted({k for a in resolved for k in _match_keys_for_anchor(a)})
    if not keys:
        return TraversalResult(claims=[], path_count=0, max_hops=0), []

    params = {f"k{i}": k for i, k in enumerate(keys)}
    where = " OR ".join(f"cs.subject = $k{i}" for i in range(len(keys)))
    if predicate_hint:
        where = f"({where}) AND cs.predicate = $pred"
        params["pred"] = predicate_hint

    rows = client.run_read(
        f"MATCH (cs:ConflictSet) WHERE {where} "
        "RETURN cs.conflict_id AS conflict_id, cs.subject AS subject, cs.predicate AS predicate, "
        "cs.resolution_status AS resolution_status, cs.winner AS winner, "
        "cs.margin AS margin, cs.rationale AS rationale",
        **params,
    )
    conflicts = [
        ConflictHit(
            conflict_id=r["conflict_id"],
            subject=r["subject"],
            predicate=r["predicate"],
            resolution_status=r["resolution_status"],
            winner=r["winner"] or None,
            margin=r["margin"],
            rationale=r["rationale"],
        )
        for r in rows
    ]

    claims: list[ClaimHit] = []
    for r in rows:
        involved = client.run_read(
            "MATCH (cs:ConflictSet {id: $cs_vertex})-[:INVOLVES]->(c:Claim) "
            "RETURN c.claim_id AS claim_id, c.predicate AS predicate, c.subject_id AS subject_id, "
            "c.object_id AS object_id, c.object_literal AS object_literal, "
            "c.extraction_confidence AS extraction_confidence, c.evidence_chunk_id AS evidence_chunk_id",
            cs_vertex=hydra_id(r["conflict_id"]),
        )
        claims.extend(_row_to_claimhit(c) for c in involved)

    return TraversalResult(claims=claims, path_count=len(conflicts), max_hops=1 if conflicts else 0), conflicts


def traverse_aggregate(
    client: HydraClient, anchors: list[Anchor], predicate_hint: str | None
) -> TraversalResult:
    """Count/list across every subject asserting `predicate_hint` about a
    resolved anchor's object (e.g. "how many tickets does X own" ->
    predicate=OWNS, object=X, count distinct subjects)."""
    resolved = [a for a in anchors if a.resolved]
    keys = sorted({k for a in resolved for k in _match_keys_for_anchor(a)})
    if not keys or not predicate_hint:
        return TraversalResult(claims=[], path_count=0, max_hops=0)
    rows = _fetch_claims_by_keys(client, keys, predicate=predicate_hint)
    claims = [_row_to_claimhit(r) for r in rows]
    return TraversalResult(claims=claims, path_count=len(claims), max_hops=1 if claims else 0)


def traverse_temporal(
    client: HydraClient, anchors: list[Anchor], predicate_hint: str | None
) -> TraversalResult:
    """Full chronological history of a resolved anchor+predicate (not just the
    latest claim) — sorted ascending by asserted_at so synthesize.py can describe
    a sequence/change-over-time rather than a single point fact."""
    resolved = [a for a in anchors if a.resolved]
    keys = sorted({k for a in resolved for k in _match_keys_for_anchor(a)})
    if not keys:
        return TraversalResult(claims=[], path_count=0, max_hops=0)
    rows = _fetch_claims_by_keys(client, keys, predicate=predicate_hint)
    rows.sort(key=lambda r: r.get("asserted_at") or "")
    claims = [_row_to_claimhit(r) for r in rows]
    return TraversalResult(claims=claims, path_count=len(claims), max_hops=1 if claims else 0)
