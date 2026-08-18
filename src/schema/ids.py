"""Node ID scheme — BUILD-SPEC.md §7.1, plus the HydraDB surrogate-integer mapping
documented in docs/cypher-support.md ("Critical finding: node id must be a non-negative
integer").

HydraDB's own vertex identity property, `id`, must be a non-negative integer (confirmed
live against the running graph-node — see docs/cypher-support.md). The spec's content-
addressed string IDs below remain the canonical, human/content-meaningful identifiers
used everywhere in application code and stored as ordinary node properties (`doc_id`,
`mention_id`, `claim_id`, `canonical_id`, ...); `hydra_id()` derives the deterministic
integer surrogate used only for HydraDB's `id` property (MERGE upsert identity and
relationship endpoint matching).
"""

import hashlib
from uuid import uuid4


def node_id(kind: str, *parts: str) -> str:
    raw = "\x1f".join([kind, *(p.strip().lower() for p in parts)])
    return f"{kind}:{hashlib.blake2b(raw.encode(), digest_size=12).hexdigest()}"


def opaque_id(kind: str) -> str:
    """Entity IDs (Person/Project/Team/...) are opaque, assigned at canonicalization —
    resolution is a revisable hypothesis, not a fact (BUILD-SPEC.md §7.1)."""
    return f"{kind}:{uuid4().hex}"


_SURROGATE_MASK = 0x3FFF_FFFF_FFFF_FFFF  # 62 bits — stays inside a signed Bolt i64


def hydra_id(spec_id: str) -> int:
    """Deterministic non-negative integer surrogate for HydraDB's `id` property,
    derived from the same blake2b hash family as node_id()/opaque_id(). Same input
    always yields the same surrogate, which is what makes MERGE-by-id idempotent."""
    digest = hashlib.blake2b(spec_id.strip().lower().encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & _SURROGATE_MASK
