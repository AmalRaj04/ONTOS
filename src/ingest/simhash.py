"""64-bit SimHash for near-duplicate detection (BUILD-SPEC.md §8.3 step 5). Computed
at Tier 1 write time and stored on every Document; src/ingest/dedupe.py (M2) buckets
these via LSH and confirms candidates with Jaccard before writing NEAR_DUPLICATE_OF."""

import hashlib
import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _shingles(text: str, k: int = 4) -> list[str]:
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < k:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)]


def compute_simhash(text: str) -> int:
    """63-bit result (not the full 64) — Bolt protocol integers are signed i64, and
    a raw 64-bit hash can exceed that range (confirmed live:
    neo4j._codec.packstream raises OverflowError on values >= 2**63)."""
    bits = [0] * 63
    for shingle in _shingles(text):
        h = int.from_bytes(hashlib.blake2b(shingle.encode(), digest_size=8).digest(), "big")
        for i in range(63):
            bits[i] += 1 if (h >> i) & 1 else -1
    result = 0
    for i, b in enumerate(bits):
        if b > 0:
            result |= 1 << i
    return result


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
