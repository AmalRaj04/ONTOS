"""BUILD-SPEC.md §9 step 6 — Canonicalize. Writes Person nodes with opaque IDs;
every original Mention is linked via RESOLVES_TO and never deleted (mentions remain
the alias-set evidence for the demo and the provenance chain for citations).

`author`-kind identity records (Document.author_raw) don't back onto an existing
Mention node — no chunk-level regex pass produces them, since author_raw is a
structured field, not free text (see records.py). A synthetic Mention node is
written for each one here (mention_type="author") so the same RESOLVES_TO contract
holds uniformly for every identity signal, and it's linked back to its Document via
the same MENTIONS relationship type Chunk-level mentions use (upsert_edges is
label-generic, so Document as the from-endpoint works unchanged)."""

from collections import Counter, defaultdict

from src.db.client import HydraClient
from src.ingest.writer import upsert_edges, upsert_nodes
from src.resolution.block import RecordFeatures
from src.resolution.records import Employee, IdentityRecord
from src.resolution.score import ScoringContext, identity_key
from src.schema.ids import hydra_id, node_id, opaque_id


def _pick_canonical_name(
    member_records: list[IdentityRecord], member_feats: list[RecordFeatures], ctx: ScoringContext
) -> tuple[str, str | None]:
    """Prefer an employee-roster name (ground truth) if any member resolves to
    one; else the most common non-email/non-handle display-name raw value; else
    the most common raw value overall."""
    for r, f in zip(member_records, member_feats):
        if f.email and f.email in ctx.employee_by_email:
            emp = ctx.employee_by_email[f.email]
            return emp.name, emp.email

    name_candidates = [
        r.raw for r, f in zip(member_records, member_feats) if not f.email and not f.handle
    ]
    if name_candidates:
        name = Counter(name_candidates).most_common(1)[0][0]
    else:
        name = Counter(r.raw for r in member_records).most_common(1)[0][0]

    email_candidates = [f.email for f in member_feats if f.email]
    primary_email = Counter(email_candidates).most_common(1)[0][0] if email_candidates else None
    return name, primary_email


def canonicalize_clusters(
    client: HydraClient,
    records: list[IdentityRecord],
    feats: list[RecordFeatures],
    clusters: list[list[int]],
    ctx: ScoringContext,
) -> dict:
    person_nodes: list[dict] = []
    synthetic_mention_nodes: list[dict] = []
    doc_to_synth_mention_edges: list[dict] = []
    resolves_to_edges: list[dict] = []
    # normalized alias string -> canonical Person id. Persisted by run_er.py to
    # data/er_alias_map.json so M4's detect.py (src/conflict/detect.py) can map a
    # Claim's raw subject/object text to the same canonical entity ER already
    # resolved it to, without a second live-graph traversal — this is what lets
    # conflict detection group claims across sources that named the same person
    # differently (an email in one system, a display name in another), which is
    # the entire point of running ER before conflict detection at all.
    alias_map: dict[str, str] = {}

    cluster_sizes = []
    for member_idxs in clusters:
        member_records = [records[i] for i in member_idxs]
        member_feats = [feats[i] for i in member_idxs]
        name, primary_email = _pick_canonical_name(member_records, member_feats, ctx)

        canonical_id = opaque_id("Person")
        alias_surfaces = sorted({r.raw for r in member_records})
        source_systems = sorted({r.source_system for r in member_records})
        cluster_sizes.append(len(member_idxs))
        for alias in alias_surfaces:
            alias_map[alias.strip().lower()] = canonical_id

        person_nodes.append(
            {
                "vertex": hydra_id(canonical_id),
                "canonical_id": canonical_id,
                "name": name,
                "primary_email": primary_email or "",
                "alias_count": len(alias_surfaces),
                "source_systems": ",".join(source_systems),
                "cluster_size": len(member_idxs),
            }
        )

        for r in member_records:
            if r.mention_id:
                resolves_to_edges.append(
                    {
                        "from_vertex": hydra_id(r.mention_id),
                        "to_vertex": hydra_id(canonical_id),
                        "rel_vertex": hydra_id(f"resolves_to:{r.mention_id}:{canonical_id}"),
                    }
                )
            else:
                synth_mention_id = node_id("author_mention", r.doc_id)
                synthetic_mention_nodes.append(
                    {
                        "vertex": hydra_id(synth_mention_id),
                        "mention_id": synth_mention_id,
                        "surface": r.raw,
                        "surface_norm": r.raw.strip().lower(),
                        "char_offset": 0,
                        "mention_type": "author",
                    }
                )
                doc_to_synth_mention_edges.append(
                    {
                        "from_vertex": hydra_id(r.doc_id),
                        "to_vertex": hydra_id(synth_mention_id),
                        "rel_vertex": hydra_id(f"mentions:{r.doc_id}:{synth_mention_id}"),
                    }
                )
                resolves_to_edges.append(
                    {
                        "from_vertex": hydra_id(synth_mention_id),
                        "to_vertex": hydra_id(canonical_id),
                        "rel_vertex": hydra_id(f"resolves_to:{synth_mention_id}:{canonical_id}"),
                    }
                )

    upsert_nodes(client, "Person", person_nodes)
    upsert_nodes(client, "Mention", synthetic_mention_nodes)
    upsert_edges(client, "Document", "Mention", "MENTIONS", doc_to_synth_mention_edges)
    upsert_edges(client, "Mention", "Person", "RESOLVES_TO", resolves_to_edges)

    return {
        "person_count": len(person_nodes),
        "synthetic_mention_count": len(synthetic_mention_nodes),
        "resolves_to_edge_count": len(resolves_to_edges),
        "cluster_sizes": cluster_sizes,
        "max_cluster_size": max(cluster_sizes) if cluster_sizes else 0,
        "singleton_count": sum(1 for s in cluster_sizes if s == 1),
        "alias_map": alias_map,
    }
