"""Identity-record collection for entity resolution (BUILD-SPEC.md §9).

Every ingested Document/Mention already lives in HydraDB, but re-reading them back
out via label scans is slow at 244K-document scale without a property index
(PROJECT.md decision #34). All the information ER needs — Document.author_raw,
Mention(type=email/handle) surfaces and their positions — is a pure, deterministic
function of the same corpus files the adapters already parsed, and node/mention IDs
are content-hashed (src/schema/ids.node_id), so re-deriving them here in Python
yields byte-identical IDs to what bulk_ingest() already wrote. This lets ER run
entirely off the local corpus + each source's checkpoint offset, with HydraDB used
only for the final write (src/resolution/canonicalize.py), not for candidate
gathering — much faster and avoids the label-scan bottleneck for a step that isn't
on the query hot path anyway.

One IdentityRecord is emitted per Person-identifying signal occurrence:
- "author": Document.author_raw (one per document; various shapes per source — see
  src/ingest/adapters/*.py's author_raw= mapping: display name, email, or handle)
- "email_mention" / "handle_mention": each Mention of that type found in the body,
  keeping mention_id/char_offset/sentence_index so co-occurrence (positive: shared
  doc/thread; negative: shared sentence) can be scored without a graph round trip.
"""

import os
import re
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

from src.ingest.adapters import ALL_ADAPTERS
from src.ingest.checkpoint import Checkpoint
from src.ingest.priority import load_priority_documents
from src.ingest.tier1_structural import chunk_body, extract_mentions
from src.schema.ids import node_id
from src.schema.models import Document

_SENTENCE_SPLIT = re.compile(r"[.!?\n]+")
_LOOKS_LIKE_DOMAIN = re.compile(r"\.(com|org|net|io|ai|co|edu|gov|dev)$", re.IGNORECASE)


@dataclass
class IdentityRecord:
    record_id: str
    kind: str  # "author" | "email_mention" | "handle_mention"
    raw: str
    doc_id: str
    source_system: str
    thread_key: str | None
    asserted_at: str | None
    mention_id: str | None = None
    sentence_key: tuple[str, int] | None = None  # (doc_id, sentence_ordinal)


def _sentence_index(body: str, char_offset: int) -> int:
    if not body or char_offset >= len(body):
        return 0
    prefix = body[:char_offset]
    return len(_SENTENCE_SPLIT.findall(prefix))


def _records_for_document(doc: Document) -> list[IdentityRecord]:
    records: list[IdentityRecord] = []
    asserted_at = doc.created_at.isoformat() if doc.created_at else None

    if doc.author_raw and doc.author_raw.strip():
        records.append(
            IdentityRecord(
                record_id=node_id("er_author", doc.doc_id),
                kind="author",
                raw=doc.author_raw.strip(),
                doc_id=doc.doc_id,
                source_system=doc.source_system,
                thread_key=doc.thread_key,
                asserted_at=asserted_at,
            )
        )

    for chunk in chunk_body(doc.body):
        for m in extract_mentions(chunk["text"]):
            if m["mention_type"] not in ("email", "handle"):
                continue
            if m["mention_type"] == "handle" and _LOOKS_LIKE_DOMAIN.search(m["surface"]):
                # extract_mentions()'s handle/email regexes overlap on strings like
                # "git@github.com" (matches email whole, AND handle "@github.com" as
                # a substring) — a handle ending in a TLD is that overlap, not a
                # real @mention, and would otherwise pollute Person clustering with
                # domain "identities". Filtering here, not in tier1_structural.py,
                # since that extraction is already frozen/ingested for the full
                # corpus (PROJECT.md M1/M2) — not worth re-ingesting this late.
                continue
            doc_offset = chunk["char_start"] + m["char_offset"]
            mention_id = node_id("mention", doc.doc_id, str(doc_offset), m["surface"])
            records.append(
                IdentityRecord(
                    record_id=node_id("er_mention", mention_id),
                    kind=f"{m['mention_type']}_mention",
                    raw=m["surface"],
                    doc_id=doc.doc_id,
                    source_system=doc.source_system,
                    thread_key=doc.thread_key,
                    asserted_at=asserted_at,
                    mention_id=mention_id,
                    sentence_key=(doc.doc_id, _sentence_index(doc.body, doc_offset)),
                )
            )
    return records


def _collect_for_source(corpus_dir: str, source: str, adapter_cls) -> list[IdentityRecord]:
    import time

    offset = Checkpoint(source).load()
    if offset <= 0:
        return []
    t0 = time.monotonic()
    adapter = adapter_cls()
    out: list[IdentityRecord] = []
    for idx, doc in enumerate(adapter.iter_documents(corpus_dir)):
        if idx >= offset:
            break
        out.extend(_records_for_document(doc))
    print(f"[er:records] {source}: {offset} docs -> {len(out)} records ({time.monotonic()-t0:.0f}s)", flush=True)
    return out


def collect_identity_records(corpus_dir: str) -> list[IdentityRecord]:
    """Union of: every source's already-ingested prefix (per checkpoint offset,
    exactly mirroring what src/ingest/run_ingest.py wrote — see module docstring),
    plus the question-priority tier (always ingested, may fall outside a source's
    checkpoint prefix). Runs one thread per source — this step is I/O-bound
    (`iter_records()` does `sorted(root.rglob("*.json"))`, an eager directory
    listing, before yielding anything; at ~500K total corpus files across 9
    sources on an external SSD this dominates wall time far more than the regex
    work per document) — reading sources concurrently, not the single-threaded
    sequential scan a first version of this function used, is what keeps this
    step from taking 15+ minutes wall-clock for no CPU-bound reason."""
    from concurrent.futures import ThreadPoolExecutor

    records: list[IdentityRecord] = []

    with ThreadPoolExecutor(max_workers=9) as pool:
        futures = [
            pool.submit(_collect_for_source, corpus_dir, source, adapter_cls)
            for source, adapter_cls in ALL_ADAPTERS.items()
        ]
        for future in futures:
            records.extend(future.result())

    # doc_id is content-hashed from (source_system, native_id) — collisions across
    # different sources' independent checkpoint ranges aren't possible, so this
    # only needs to catch priority-tier docs that duplicate what a source's own
    # prefix already covered.
    seen_doc_ids = {r.doc_id for r in records}

    for doc in load_priority_documents(corpus_dir):
        if doc.doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc.doc_id)
        records.extend(_records_for_document(doc))

    return records


@dataclass
class Employee:
    name: str
    email: str
    department: str
    manager: str | None


def load_employee_directory(corpus_dir: str) -> list[Employee]:
    """Ground-truth roster from EnterpriseRAG-Bench's generation scaffolding
    (BUILD-SPEC.md §7.6.1) — used for team_overlap and role_consistency scoring
    features, and as a sanity-check set for cluster quality (see run_er.py)."""
    path = os.path.join(os.path.dirname(corpus_dir), "employee_directory.yaml")
    if not os.path.exists(path):
        # corpus_dir is .../generated_data/sources ; directory file lives one up
        path = os.path.join(corpus_dir, "..", "employee_directory.yaml")
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    out = []
    for dept, people in (data.get("departments") or {}).items():
        for p in people:
            out.append(
                Employee(
                    name=p.get("name", ""),
                    email=(p.get("email") or "").lower(),
                    department=dept,
                    manager=p.get("manager"),
                )
            )
    return out
