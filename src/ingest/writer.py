"""Batched, idempotent writes. Every pattern here is confirmed live against
graph-node — see docs/cypher-support.md and PROJECT.md decisions #1-#6, #20-#21:

- Standalone node upsert: UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Label, ...
- Edge between two already-existing nodes: both MATCH endpoints need exactly one
  label each, and the relationship itself needs its own integer `id` (same
  hydra_id() surrogate scheme as nodes) for MERGE-idempotency.
- A new child node can't be inline-created alongside a MATCHed existing endpoint in
  one UNWIND CREATE — it must be MERGEd as its own standalone batch first.
- `null` parameter values are rejected outright — sanitized to "" uniformly.

Every write function here takes a flat batch that may span many parent documents at
once (chunks/mentions from hundreds of documents in one call) — grouping by parent
isn't needed since edge rows just carry their own from/to vertex pair. This is what
lets M2's bulk ingest do one round trip per batch instead of one per document.
"""

import json

from src.db.client import HydraClient
from src.schema.ids import hydra_id
from src.schema.models import Document

# Two independent server-side caps, both confirmed live: Bolt messages are capped at
# 2 MiB ("message size exceeds limit of 2097152 bytes", hit by a 250-document batch
# of long Confluence pages), and UNWIND batches are separately capped at 1024 rows
# regardless of byte size ("client_query_batch_items rejected by admission control:
# actual 1605 exceeds limit 1024", hit by mention rows — many mentions per document
# means row count grows faster than document count). INGEST_BATCH_SIZE bounds
# document count only, so neither limit is safe to assume from it. Every batch write
# here re-splits by whichever limit is hit first, so no caller needs to reason about
# payload size or row count.
_MAX_BATCH_BYTES = 1_500_000
_MAX_BATCH_ROWS = 1000


def _size_chunked(rows: list[dict]):
    chunk: list[dict] = []
    chunk_bytes = 0
    for row in rows:
        row_bytes = len(json.dumps(row))
        if chunk and (chunk_bytes + row_bytes > _MAX_BATCH_BYTES or len(chunk) >= _MAX_BATCH_ROWS):
            yield chunk
            chunk, chunk_bytes = [], 0
        chunk.append(row)
        chunk_bytes += row_bytes
    if chunk:
        yield chunk


# A string property value beyond ~32.7KB makes the server panic outright
# ("query executor panicked... corrupt value", not a clean rejection) — confirmed
# live by binary search: 32,743 bytes succeeds, 32,744 crashes the write. This isn't
# documented anywhere (cypher-compat.md's "Values and parameters" section only says
# "integers, floats, booleans and strings", no length note). Truncated well below
# that boundary for safety margin. The only field this realistically affects is
# Document.body (Chunk.text is ~500 words / ~2.5-3KB by construction, well under
# the limit) — full-fidelity text stays queryable via Chunk nodes regardless, so
# truncating the Document-level copy costs nothing citation/retrieval-wise.
_MAX_STRING_BYTES = 30_000
_TRUNCATION_MARKER = "...[truncated, see Chunk nodes for full text]"


def _truncate(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= _MAX_STRING_BYTES:
        return value
    truncated = encoded[: _MAX_STRING_BYTES - len(_TRUNCATION_MARKER)]
    # avoid splitting a multi-byte UTF-8 sequence in half
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]
    return truncated.decode("utf-8", errors="ignore") + _TRUNCATION_MARKER


def _sanitize(rows: list[dict]) -> list[dict]:
    """HydraDB parameters reject `null` outright ("must contain booleans, signed or
    unsigned integers, finite floats, strings, lists, or string-keyed maps" —
    confirmed live). Optional fields map None -> "" so every row in a batch has a
    uniform, settable value for every property key the batch's SET clause names.
    Oversized strings are truncated — see _MAX_STRING_BYTES above."""
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if v is None:
                clean[k] = ""
            elif isinstance(v, str):
                clean[k] = _truncate(v)
            else:
                clean[k] = v
        result.append(clean)
    return result


def upsert_nodes(client: HydraClient, label: str, rows: list[dict]) -> None:
    """rows: list of {"vertex": int, **properties}. `vertex` is the hydra_id
    surrogate; every other key becomes a node property via SET n.<key> = row.<key>."""
    if not rows:
        return
    rows = _sanitize(rows)
    prop_keys = [k for k in rows[0] if k != "vertex"]
    set_clause = ", ".join(f"n.{k} = row.{k}" for k in prop_keys)
    query = f"UNWIND $rows AS row MERGE (n {{id: row.vertex}}) SET n:{label}, {set_clause}"
    for sub_batch in _size_chunked(rows):
        client.run_write(query, sub_batch)


def upsert_edges(
    client: HydraClient,
    from_label: str,
    to_label: str,
    rel_type: str,
    rows: list[dict],
) -> None:
    """rows: list of {"from_vertex": int, "to_vertex": int, "rel_vertex": int,
    **edge_properties}. Both endpoints must already exist (upsert_nodes first)."""
    if not rows:
        return
    rows = _sanitize(rows)
    prop_keys = [k for k in rows[0] if k not in ("from_vertex", "to_vertex", "rel_vertex")]
    set_clause = (", " + ", ".join(f"r.{k} = row.{k}" for k in prop_keys)) if prop_keys else ""
    query = (
        f"UNWIND $rows AS row "
        f"MATCH (a:{from_label} {{id: row.from_vertex}}), (b:{to_label} {{id: row.to_vertex}}) "
        f"MERGE (a)-[r:{rel_type} {{id: row.rel_vertex}}]->(b)"
        f"{(' SET' + set_clause[1:]) if set_clause else ''}"
    )
    for sub_batch in _size_chunked(rows):
        client.run_write(query, sub_batch)


def write_documents(client: HydraClient, docs: list[Document]) -> None:
    rows = []
    for d in docs:
        row = d.model_dump(mode="json")
        row["vertex"] = hydra_id(d.doc_id)
        rows.append(row)
    upsert_nodes(client, "Document", rows)


def write_chunks(client: HydraClient, chunks: list[dict]) -> None:
    """chunks: list of {"doc_id", "chunk_id", "ordinal", "text", "char_start",
    "char_end"} — may span many documents in one call."""
    node_rows = [
        {
            "vertex": hydra_id(c["chunk_id"]),
            "chunk_id": c["chunk_id"],
            "ordinal": c["ordinal"],
            "text": c["text"],
            "char_start": c["char_start"],
            "char_end": c["char_end"],
        }
        for c in chunks
    ]
    upsert_nodes(client, "Chunk", node_rows)

    edge_rows = [
        {
            "from_vertex": hydra_id(c["doc_id"]),
            "to_vertex": hydra_id(c["chunk_id"]),
            "rel_vertex": hydra_id(f"has_chunk:{c['doc_id']}:{c['chunk_id']}"),
            "ordinal": c["ordinal"],
        }
        for c in chunks
    ]
    upsert_edges(client, "Document", "Chunk", "HAS_CHUNK", edge_rows)


def write_mentions(client: HydraClient, mentions: list[dict]) -> None:
    """mentions: list of {"chunk_id", "mention_id", "surface", "surface_norm",
    "char_offset", "mention_type"} — may span many chunks in one call."""
    node_rows = [
        {
            "vertex": hydra_id(m["mention_id"]),
            "mention_id": m["mention_id"],
            "surface": m["surface"],
            "surface_norm": m["surface_norm"],
            "char_offset": m["char_offset"],
            "mention_type": m["mention_type"],
        }
        for m in mentions
    ]
    upsert_nodes(client, "Mention", node_rows)

    edge_rows = [
        {
            "from_vertex": hydra_id(m["chunk_id"]),
            "to_vertex": hydra_id(m["mention_id"]),
            "rel_vertex": hydra_id(f"mentions:{m['chunk_id']}:{m['mention_id']}"),
        }
        for m in mentions
    ]
    upsert_edges(client, "Chunk", "Mention", "MENTIONS", edge_rows)
