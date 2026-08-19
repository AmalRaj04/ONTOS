"""BUILD-SPEC.md §11 step 3. M1: LOOKUP only (direct claim match). MULTIHOP
(algo.MSpaths pairwise) and CONFLICT (direct ConflictSet read) are M5 work — M4 has
to exist first to produce ConflictSets, and the entity-resolution-backed anchor
matching MULTIHOP needs is M3 work. Uses `causal` consistency, the default hot path
per §11 step 3 ("Use causal consistency on this path (default hot path)").

`WHERE ... IN [...]` is not supported by HydraDB's Cypher subset (confirmed live,
docs/cypher-support.md) — subject-set membership is built as an OR-chain of equality
comparisons instead. `Claim.trust` is only populated once M4's trust function runs;
until then every claim's trust is None, which is the correct/expected state, not a
bug — the abstention gate (src/query/gate.py) treats "no trust yet" the same as "low
trust" via `max_trust < 0.35`.
"""

from dataclasses import dataclass

from src.db.client import HydraClient
from src.query.anchor import Anchor


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
