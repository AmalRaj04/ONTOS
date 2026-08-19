"""Tier 1 — structural, no LLM, full corpus. BUILD-SPEC.md §8.3.

For every Document: write the node, chunk the body, extract deterministic mentions
via regex, write structural edges. Near-duplicate detection (step 5, dedupe.py) runs
separately over the whole corpus in M2, not per-document here.
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
    chunks = []
    words = list(re.finditer(r"\S+", body))
    if not words:
        return []
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


def ingest_document(client: HydraClient, doc: Document) -> dict:
    """Write one Document through the full Tier 1 path. Returns a summary dict for
    logging/testing (chunk count, mention count)."""
    write_documents(client, [doc])

    chunk_dicts = chunk_body(doc.body)
    for c in chunk_dicts:
        c["chunk_id"] = node_id("chunk", doc.doc_id, str(c["ordinal"]))
    write_chunks(client, doc.doc_id, chunk_dicts)

    total_mentions = 0
    for c in chunk_dicts:
        raw_mentions = extract_mentions(c["text"])
        for m in raw_mentions:
            doc_offset = c["char_start"] + m["char_offset"]
            m["char_offset"] = doc_offset
            m["mention_id"] = node_id("mention", doc.doc_id, str(doc_offset), m["surface"])
        if raw_mentions:
            write_mentions(client, c["chunk_id"], raw_mentions)
        total_mentions += len(raw_mentions)

    return {
        "doc_id": doc.doc_id,
        "chunk_count": len(chunk_dicts),
        "mention_count": total_mentions,
    }
