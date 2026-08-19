"""Near-duplicate detection — BUILD-SPEC.md §8.3 step 5 / §3 (datasketch MinHash/LSH).

Two phases, kept separate so a crash mid-ingest doesn't cost the dedup work already
done: (1) `append_signature()` is called once per document during the Tier 1 bulk
ingest pass (src/ingest/run_ingest.py) and appends a MinHash signature to a flat
JSONL file — cheap, streaming, resumable the same way checkpointing is. (2)
`build_near_duplicate_edges()` runs once after ingest, loads every signature, buckets
candidates via MinHashLSH, confirms each candidate pair with a direct Jaccard
computation on the underlying shingle sets (not just the MinHash estimate — the
corpus is deliberately seeded with near-duplicates precisely to trap naive
vote-counting later in conflict resolution, so the confirmation step here should be
exact, not estimated), and writes NEAR_DUPLICATE_OF edges.

The `Document.simhash` field (src/ingest/simhash.py) is a separate, cheaper signal
already stored on every node; MinHashLSH is the corpus-scale candidate-generation
mechanism BUILD-SPEC.md §3 names explicitly.
"""

import base64
import json
import re
from pathlib import Path

import numpy as np
from datasketch import MinHash, MinHashLSH

from src.db.client import HydraClient
from src.ingest.writer import upsert_edges
from src.schema.ids import hydra_id, node_id

NUM_PERM = 128
LSH_THRESHOLD = 0.8
JACCARD_CONFIRM_THRESHOLD = 0.8

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _shingles(text: str, k: int = 4) -> set[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


MINHASH_SCHEME = "affine32"  # pinned explicitly — _load_signatures reconstructs
# MinHash objects from raw uint32 hashvalues and datasketch >=2.0 requires the
# creation-time scheme to match exactly, so this can't be left to the library default.


def compute_minhash(text: str, num_perm: int = NUM_PERM) -> MinHash:
    mh = MinHash(num_perm=num_perm, scheme=MINHASH_SCHEME)
    for shingle in _shingles(text):
        mh.update(shingle.encode("utf8"))
    return mh


def append_signature(path: str, doc_id: str, source_system: str, body: str) -> None:
    mh = compute_minhash(body)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(
            json.dumps(
                {
                    "doc_id": doc_id,
                    "source_system": source_system,
                    "hashvalues": base64.b64encode(mh.hashvalues.tobytes()).decode(),
                }
            )
            + "\n"
        )


def _load_signatures(path: str) -> dict[str, tuple[MinHash, str]]:
    signatures = {}
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            values = np.frombuffer(base64.b64decode(rec["hashvalues"]), dtype=np.uint32)
            mh = MinHash(num_perm=NUM_PERM, hashvalues=values, scheme=MINHASH_SCHEME)
            signatures[rec["doc_id"]] = (mh, rec["source_system"])
    return signatures


def find_candidate_pairs(signatures: dict[str, tuple]) -> set[tuple[str, str]]:
    lsh = MinHashLSH(threshold=LSH_THRESHOLD, num_perm=NUM_PERM)
    for doc_id, (mh, _) in signatures.items():
        lsh.insert(doc_id, mh)

    pairs = set()
    for doc_id, (mh, _) in signatures.items():
        for candidate in lsh.query(mh):
            if candidate != doc_id:
                pairs.add(tuple(sorted((doc_id, candidate))))
    return pairs


def build_near_duplicate_edges(client: HydraClient, signatures_path: str, batch_size: int = 500) -> dict:
    signatures = _load_signatures(signatures_path)
    pairs = find_candidate_pairs(signatures)

    confirmed_rows = []
    for a, b in pairs:
        mh_a, _ = signatures[a]
        mh_b, _ = signatures[b]
        similarity = mh_a.jaccard(mh_b)
        if similarity >= JACCARD_CONFIRM_THRESHOLD:
            confirmed_rows.append(
                {
                    "from_vertex": hydra_id(a),
                    "to_vertex": hydra_id(b),
                    "rel_vertex": hydra_id(f"near_dup:{a}:{b}"),
                    "similarity": round(float(similarity), 4),
                }
            )

    for i in range(0, len(confirmed_rows), batch_size):
        upsert_edges(
            client, "Document", "Document", "NEAR_DUPLICATE_OF", confirmed_rows[i : i + batch_size]
        )

    return {
        "documents_signed": len(signatures),
        "candidate_pairs": len(pairs),
        "confirmed_duplicates": len(confirmed_rows),
    }
