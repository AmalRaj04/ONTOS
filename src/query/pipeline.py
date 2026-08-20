"""Ties anchor -> plan -> traverse -> gate -> synthesize together and produces the
output contract from BUILD-SPEC.md §11. All five plan classes (LOOKUP, MULTIHOP,
CONFLICT, AGGREGATE, TEMPORAL) dispatch to their src/query/traverse.py branch
(M5) — see that module's docstring for the MULTIHOP/CONFLICT implementation notes.
"""

import time

from src.db.client import HydraClient
from src.llm.router import LLMRouter
from src.query.anchor import nearest_known_name, resolve_anchors
from src.query.gate import AbstentionSignals, should_abstain
from src.query.plan import classify_question
from src.query.synthesize import synthesize_answer
from src.query.traverse import (
    TraversalResult,
    traverse_aggregate,
    traverse_conflict,
    traverse_lookup,
    traverse_multihop,
    traverse_temporal,
)
from src.schema.ids import hydra_id


def _citation_for_claim(client: HydraClient, claim) -> dict | None:
    rows = client.run_read(
        "MATCH (ch:Chunk {id: $chunk_vertex})<-[:HAS_CHUNK]-(d:Document) "
        "RETURN d.doc_id AS doc_id, d.native_id AS native_id, d.source_system AS source_system, "
        "d.title AS title, ch.chunk_id AS chunk_id, "
        "ch.char_start AS char_start, ch.char_end AS char_end",
        chunk_vertex=hydra_id(claim.evidence_chunk_id),
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "doc_id": r["doc_id"],
        # native_id == the corpus's own dataset_doc_uuid ("dsid_...") for every
        # real record (src/ingest/adapters/*.py all set native_id from
        # rec["dataset_doc_uuid"]) — this is what eval/run_eval.py uses to build
        # the submission's document_ids against BUILD-SPEC.md §12's gold schema,
        # without a second doc_id<->dsid mapping table.
        "native_id": r["native_id"],
        "source_system": r["source_system"],
        "title": r["title"],
        "chunk_id": r["chunk_id"],
        "quote_span": [r["char_start"], r["char_end"]],
    }


def _run_traversal(client: HydraClient, plan, anchors) -> tuple[TraversalResult, list]:
    if plan.plan_class == "MULTIHOP":
        return traverse_multihop(client, anchors, plan.predicate_hint), []
    if plan.plan_class == "CONFLICT":
        result, conflicts = traverse_conflict(client, anchors, plan.predicate_hint)
        return result, conflicts
    if plan.plan_class == "AGGREGATE":
        return traverse_aggregate(client, anchors, plan.predicate_hint), []
    if plan.plan_class == "TEMPORAL":
        return traverse_temporal(client, anchors, plan.predicate_hint), []
    return traverse_lookup(client, anchors, plan.predicate_hint), []


def answer_question(
    client: HydraClient,
    router: LLMRouter,
    question_id: str,
    question: str,
    consistency: str = "causal",
) -> dict:
    start = time.monotonic()

    plan = classify_question(router, question)
    anchors = resolve_anchors(client, plan.entities) if plan.entities else []

    traversal, conflict_hits = _run_traversal(client, plan, anchors)

    unresolved = any(not a.resolved for a in anchors)
    max_trust = max(
        (c.trust if c.trust is not None else c.extraction_confidence for c in traversal.claims),
        default=0.0,
    )
    evidence_sources = len({c.evidence_chunk_id for c in traversal.claims})
    signals = AbstentionSignals(
        unresolved_anchors=unresolved,
        claim_count=len(traversal.claims),
        path_count=traversal.path_count,
        max_trust=max_trust,
        evidence_sources=evidence_sources,
    )
    decision = should_abstain(signals)

    # A CONFLICT question whose gate would otherwise abstain on weak evidence is
    # still answerable if the conflict itself was found (surfacing the contested
    # claim explicitly *is* the correct answer here, not the abstention case
    # gate.py was designed for single-claim LOOKUP evidence).
    if decision.abstain and not (plan.plan_class == "CONFLICT" and conflict_hits):
        return _abstention_response(
            question_id, plan.plan_class, anchors, decision.reason, start, client, consistency
        )

    synthesis = synthesize_answer(router, question, traversal.claims)
    citations = []
    for claim in traversal.claims:
        if claim.claim_id in synthesis["cited_claim_ids"] or not synthesis["cited_claim_ids"]:
            citation = _citation_for_claim(client, claim)
            if citation:
                citations.append(citation)

    conflicts_payload = [
        {
            "conflict_id": c.conflict_id,
            "status": c.resolution_status,
            "winner": c.winner,
            "margin": c.margin,
            "rationale": c.rationale,
        }
        for c in conflict_hits
    ]

    return {
        "question_id": question_id,
        "plan_class": plan.plan_class,
        "answer": synthesis["answer"],
        "abstained": False,
        "confidence": round(max_trust, 2),
        "citations": citations,
        "traversal": {
            "anchors": [a.normalized for a in anchors],
            "path_count": traversal.path_count,
            "max_hops": traversal.max_hops,
            "path_summary": f"{len(traversal.claims)} claim(s) via {plan.plan_class} "
            f"on {plan.predicate_hint or 'any predicate'}",
        },
        "conflicts": conflicts_payload,
        "graph_stats": {
            "claims_considered": len(traversal.claims),
            "documents_touched": len({c["doc_id"] for c in citations}),
            "consistency": consistency,
            "latency_ms": int((time.monotonic() - start) * 1000),
        },
    }


def _abstention_response(question_id, plan_class, anchors, reason, start, client, consistency) -> dict:
    near_misses = []
    for a in anchors:
        if not a.resolved:
            near_misses.extend(nearest_known_name(client, a.normalized))
    full_reason = reason
    if near_misses:
        full_reason += f" (nearest known names: {', '.join(near_misses[:3])})"
    return {
        "question_id": question_id,
        "plan_class": plan_class,
        "answer": None,
        "abstained": True,
        "confidence": 0.0,
        "citations": [],
        "traversal": {
            "anchors": [a.normalized for a in anchors],
            "path_count": 0,
            "max_hops": 0,
            "path_summary": full_reason,
        },
        "conflicts": [],
        "graph_stats": {
            "claims_considered": 0,
            "documents_touched": 0,
            "consistency": consistency,
            "latency_ms": int((time.monotonic() - start) * 1000),
        },
    }
