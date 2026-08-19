"""Batched, idempotent writes. Every pattern here is confirmed live against
graph-node — see docs/cypher-support.md and PROJECT.md decisions #1-#6, #20:

- Standalone node upsert: UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Label, ...
- Edge between two already-existing nodes: both MATCH endpoints need exactly one
  label each, and the relationship itself needs its own integer `id` (same
  hydra_id() surrogate scheme as nodes) for MERGE-idempotency.
- A new child node can't be inline-created alongside a MATCHed existing endpoint in
  one UNWIND CREATE — it must be MERGEd as its own standalone batch first.

`INGEST_BATCH_SIZE` (default 500) governs caller-side batching; every function here
just executes whatever batch it's given in one round trip.
"""

from src.db.client import HydraClient
from src.schema.ids import hydra_id
from src.schema.models import Document


def _sanitize(rows: list[dict]) -> list[dict]:
    """HydraDB parameters reject `null` outright ("must contain booleans, signed or
    unsigned integers, finite floats, strings, lists, or string-keyed maps" —
    confirmed live). Optional Document fields (title, author_raw, thread_key, uri,
    declared_container, created_at) map None -> "" so every row in a batch has a
    uniform, settable value for every property key the batch's SET clause names."""
    return [{k: ("" if v is None else v) for k, v in row.items()} for row in rows]


def upsert_nodes(client: HydraClient, label: str, rows: list[dict]) -> None:
    """rows: list of {"vertex": int, **properties}. `vertex` is the hydra_id
    surrogate; every other key becomes a node property via SET n.<key> = row.<key>."""
    if not rows:
        return
    rows = _sanitize(rows)
    prop_keys = [k for k in rows[0] if k != "vertex"]
    set_clause = ", ".join(f"n.{k} = row.{k}" for k in prop_keys)
    query = f"UNWIND $rows AS row MERGE (n {{id: row.vertex}}) SET n:{label}, {set_clause}"
    client.run_write(query, rows)


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
    client.run_write(query, rows)


def write_documents(client: HydraClient, docs: list[Document]) -> None:
    rows = []
    for d in docs:
        row = d.model_dump(mode="json")
        row["vertex"] = hydra_id(d.doc_id)
        rows.append(row)
    upsert_nodes(client, "Document", rows)


def write_chunks(client: HydraClient, doc_id: str, chunks: list[dict]) -> None:
    """chunks: list of {"chunk_id", "ordinal", "text", "char_start", "char_end"}."""
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

    doc_vertex = hydra_id(doc_id)
    edge_rows = [
        {
            "from_vertex": doc_vertex,
            "to_vertex": hydra_id(c["chunk_id"]),
            "rel_vertex": hydra_id(f"has_chunk:{doc_id}:{c['chunk_id']}"),
            "ordinal": c["ordinal"],
        }
        for c in chunks
    ]
    upsert_edges(client, "Document", "Chunk", "HAS_CHUNK", edge_rows)


def write_mentions(client: HydraClient, chunk_id: str, mentions: list[dict]) -> None:
    """mentions: list of {"mention_id", "surface", "surface_norm", "char_offset",
    "mention_type"}."""
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

    chunk_vertex = hydra_id(chunk_id)
    edge_rows = [
        {
            "from_vertex": chunk_vertex,
            "to_vertex": hydra_id(m["mention_id"]),
            "rel_vertex": hydra_id(f"mentions:{chunk_id}:{m['mention_id']}"),
        }
        for m in mentions
    ]
    upsert_edges(client, "Chunk", "Mention", "MENTIONS", edge_rows)
