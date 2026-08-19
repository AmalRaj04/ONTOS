"""Tier 1 — structural, no LLM, full corpus. BUILD-SPEC.md §8.3.

For every Document: write the node, chunk the body, extract deterministic mentions
via regex, write structural edges. Near-duplicate detection (step 5) is
src/ingest/dedupe.py, run as a separate corpus-wide pass after ingest (M2), not
per-document here.

`bulk_ingest()` is the real M2 entry point — it batches chunk/mention writes across
however many documents are passed in, so run_ingest.py can do one round trip per
INGEST_BATCH_SIZE documents instead of one per document. `ingest_document()` is a
thin single-document wrapper kept for M1's walking-skeleton test.
"""

import re

from src.db.client import HydraClient
from src.ingest.writer import write_chunks, write_documents, write_mentions
from src.schema.ids import node_id
from src.schema.models import Document

CHUNK_WORDS = 500

_MENTION_PATTERNS = [
    ("handle", re.compile(r"@[A-Za-z0-9_.\-]+")),
    ("email", re.compile(r"[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+")),
    ("ticket_id", re.compile(r"\b[A-Z]{2,}-\d+\b")),
    ("channel", re.compile(r"#[A-Za-z0-9_\-]+")),
]


def chunk_body(body: str, chunk_words: int = CHUNK_WORDS) -> list[dict]:
    """Paragraph-aware ~500-word windows (BUILD-SPEC.md §8.3 step 2: "simple
    paragraph/token-window split... is sufficient")."""
    if not body:
        return []
    words = list(re.finditer(r"\S+", body))
    if not words:
        return []
    chunks = []
    ordinal = 0
    i = 0
    while i < len(words):
        window = words[i : i + chunk_words]
        char_start = window[0].start()
        char_end = window[-1].end()
        chunks.append(
            {
                "ordinal": ordinal,
                "text": body[char_start:char_end],
                "char_start": char_start,
                "char_end": char_end,
            }
        )
        ordinal += 1
        i += chunk_words
    return chunks


def extract_mentions(text: str) -> list[dict]:
    """Deterministic, regex-only (BUILD-SPEC.md §8.3 step 3). char_offset is
    relative to the start of `text` (the caller adds chunk.char_start for a
    document-absolute offset when needed)."""
    mentions = []
    seen = set()
    for mention_type, pattern in _MENTION_PATTERNS:
        for m in pattern.finditer(text):
            surface = m.group(0)
            key = (mention_type, m.start(), surface)
            if key in seen:
                continue
            seen.add(key)
            mentions.append(
                {
                    "surface": surface,
                    "surface_norm": surface.strip().lower(),
                    "char_offset": m.start(),
                    "mention_type": mention_type,
                }
            )
    return mentions


def bulk_ingest(client: HydraClient, docs: list[Document]) -> dict:
    """One batch of documents through the full Tier 1 path in a bounded number of
    round trips, regardless of batch size."""
    if not docs:
        return {"doc_count": 0, "chunk_count": 0, "mention_count": 0}

    write_documents(client, docs)

    all_chunks: list[dict] = []
    for doc in docs:
        for c in chunk_body(doc.body):
            c["doc_id"] = doc.doc_id
            c["chunk_id"] = node_id("chunk", doc.doc_id, str(c["ordinal"]))
            all_chunks.append(c)
    write_chunks(client, all_chunks)

    all_mentions: list[dict] = []
    for c in all_chunks:
        for m in extract_mentions(c["text"]):
            doc_offset = c["char_start"] + m["char_offset"]
            m["char_offset"] = doc_offset
            m["chunk_id"] = c["chunk_id"]
            m["mention_id"] = node_id("mention", c["doc_id"], str(doc_offset), m["surface"])
            all_mentions.append(m)
    write_mentions(client, all_mentions)

    return {
        "doc_count": len(docs),
        "chunk_count": len(all_chunks),
        "mention_count": len(all_mentions),
    }


def ingest_document(client: HydraClient, doc: Document) -> dict:
    """Single-document convenience wrapper — kept for M1's walking-skeleton test.
    M2's real ingest uses bulk_ingest() directly for batching."""
    summary = bulk_ingest(client, [doc])
    return {
        "doc_id": doc.doc_id,
        "chunk_count": summary["chunk_count"],
        "mention_count": summary["mention_count"],
    }
