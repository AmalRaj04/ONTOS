"""Tier 2 — semantic, LLM-backed, targeted subset. BUILD-SPEC.md §8.4.

M1 scope: single-document, manually-triggered extraction (the selection logic in
§8.4's steps 1-3 — stratified sampling, question-neighbourhood pull, lazy enrichment
— is M2/M5 work). The TBox validation gate and the provisional-mention write pattern
are the real content of this file and are not simplified for M1.
"""

import json
from pathlib import Path

import yaml

from src.db.client import HydraClient
from src.ingest.writer import upsert_edges, upsert_nodes
from src.llm.router import LLMRouter
from src.schema.ids import hydra_id, node_id
from src.schema.models import Claim, Document

TBOX_PATH = Path(__file__).parent.parent.parent / "ontology" / "tbox.yaml"
UNMAPPED_PATH = Path("data/claims_unmapped.jsonl")

_PROMPT_TEMPLATE = """Extract factual claims from this document as JSON.
Each claim: {{"predicate": str, "subject": str, "object": str,
             "polarity": "affirm"|"negate", "confidence": 0.0-1.0,
             "evidence_span": [start_char, end_char]}}
Use predicates from this list where possible: {predicates}.
Only extract claims explicitly stated in the text. Do not infer.
Return a JSON array under the key "items". Empty array if no clear claims exist.

DOCUMENT [{source_system}, {title}]:
{body}"""


def _load_tbox() -> dict:
    with open(TBOX_PATH) as f:
        return yaml.safe_load(f)


def _is_literal_range(relation_spec: dict) -> bool:
    range_val = relation_spec.get("range")
    return isinstance(range_val, str) and range_val.startswith("literal(")


def extract_claims_raw(router: LLMRouter, doc: Document, tbox: dict) -> list[dict]:
    prompt = _PROMPT_TEMPLATE.format(
        predicates=", ".join(tbox["relations"].keys()),
        source_system=doc.source_system,
        title=doc.title or "",
        body=doc.body[:12000],  # keep well inside free-tier context/token budgets
    )
    result = router.complete(prompt, task="claim_extraction")
    return result.get("items", [])


def _write_unmapped(doc_id: str, raw_claim: dict, reason: str) -> None:
    UNMAPPED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(UNMAPPED_PATH, "a") as f:
        f.write(json.dumps({"doc_id": doc_id, "claim": raw_claim, "reason": reason}) + "\n")


def process_document(client: HydraClient, router: LLMRouter, doc: Document, chunk_id: str) -> dict:
    """Extract, TBox-validate, and write claims for one document. `chunk_id` is the
    Chunk to attach EVIDENCED_BY to (M1: the document's first chunk; M2's real
    pipeline attaches to whichever chunk the evidence_span actually falls in)."""
    tbox = _load_tbox()
    raw_claims = extract_claims_raw(router, doc, tbox)

    written = 0
    dropped = 0
    claim_node_rows = []
    evidenced_by_rows = []
    asserts_rows = []
    about_rows = []
    mention_node_rows = []

    for raw in raw_claims:
        predicate = raw.get("predicate")
        subject = (raw.get("subject") or "").strip()
        obj = (raw.get("object") or "").strip()
        if not predicate or not subject:
            _write_unmapped(doc.doc_id, raw, "missing predicate or subject")
            dropped += 1
            continue

        # TBox validation gate (§7.6/§8.4): never write an untyped predicate.
        relation_spec = tbox["relations"].get(predicate)
        if relation_spec is None:
            _write_unmapped(doc.doc_id, raw, f"predicate '{predicate}' not in tbox.yaml")
            dropped += 1
            continue

        subject_id = subject.lower()
        is_literal = _is_literal_range(relation_spec)
        object_id = None if is_literal else (obj.lower() if obj else None)
        object_literal = obj if is_literal else None

        claim_id = node_id("claim", subject_id, predicate, obj or "", doc.doc_id)
        span = raw.get("evidence_span") or [0, 0]

        claim = Claim(
            claim_id=claim_id,
            predicate=predicate,
            subject_id=subject_id,
            object_id=object_id,
            object_literal=object_literal,
            polarity=raw.get("polarity", "affirm"),
            asserted_at=doc.created_at,
            extraction_confidence=float(raw.get("confidence", 0.5)),
            evidence_chunk_id=chunk_id,
        )
        row = claim.model_dump(mode="json")
        row["vertex"] = hydra_id(claim_id)
        row["char_start"] = span[0] if len(span) > 0 else 0
        row["char_end"] = span[1] if len(span) > 1 else 0
        claim_node_rows.append(row)

        evidenced_by_rows.append(
            {
                "from_vertex": hydra_id(claim_id),
                "to_vertex": hydra_id(chunk_id),
                "rel_vertex": hydra_id(f"evidenced_by:{claim_id}:{chunk_id}"),
                "char_start": row["char_start"],
                "char_end": row["char_end"],
            }
        )

        # ASSERTS/ABOUT point at provisional Mention nodes, resolved properly in M3
        # (§8.4: "provisional entity mentions (resolved properly in the next stage)").
        subj_mention_id = node_id("mention", doc.doc_id, "claim-subject", subject_id)
        mention_node_rows.append(
            {
                "vertex": hydra_id(subj_mention_id),
                "mention_id": subj_mention_id,
                "surface": subject,
                "surface_norm": subject_id,
                "char_offset": row["char_start"],
                "mention_type": "claim_subject",
            }
        )
        asserts_rows.append(
            {
                "from_vertex": hydra_id(claim_id),
                "to_vertex": hydra_id(subj_mention_id),
                "rel_vertex": hydra_id(f"asserts:{claim_id}:{subj_mention_id}"),
            }
        )
        if object_id is not None:
            obj_mention_id = node_id("mention", doc.doc_id, "claim-object", object_id)
            mention_node_rows.append(
                {
                    "vertex": hydra_id(obj_mention_id),
                    "mention_id": obj_mention_id,
                    "surface": obj,
                    "surface_norm": object_id,
                    "char_offset": row["char_start"],
                    "mention_type": "claim_object",
                }
            )
            about_rows.append(
                {
                    "from_vertex": hydra_id(claim_id),
                    "to_vertex": hydra_id(obj_mention_id),
                    "rel_vertex": hydra_id(f"about:{claim_id}:{obj_mention_id}"),
                }
            )
        written += 1

    upsert_nodes(client, "Mention", mention_node_rows)
    upsert_nodes(client, "Claim", claim_node_rows)
    upsert_edges(client, "Claim", "Chunk", "EVIDENCED_BY", evidenced_by_rows)
    upsert_edges(client, "Claim", "Mention", "ASSERTS", asserts_rows)
    upsert_edges(client, "Claim", "Mention", "ABOUT", about_rows)

    return {"claims_written": written, "claims_dropped": dropped}
