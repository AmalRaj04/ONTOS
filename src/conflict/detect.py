"""BUILD-SPEC.md §10 step 1 — Detect. Candidates are Claim pairs sharing
subject+predicate with different objects, restricted to predicates marked
`functional: true` in ontology/tbox.yaml — non-functional predicates (WORKS_ON,
MEMBER_OF, BLOCKS, ATTENDED, REVIEWED_BY, LINKED_TO) legitimately take many values,
so excluding them is what keeps the false-positive rate manageable.

Reads from data/claims.jsonl (written by src/ingest/run_tier2.py) rather than a
live graph scan — same reasoning as src/resolution/records.py: full-corpus label
scans are slow without a property index at this graph's scale (PROJECT.md decision
#34), and Tier 2's claim volume is small enough to hold in memory directly.

Subjects are resolved through data/er_alias_map.json (src/resolution/run_er.py's
output) when a match exists, so two claims naming the same person differently
across sources ("Alice Patel" vs "alice.patel@redwood.com") still group together —
this is what makes conflict detection benefit from entity resolution at all (the
M6 ER-ablation number this project reports)."""

import json
import os
from collections import defaultdict

import yaml

CLAIMS_PATH = "data/claims.jsonl"
ALIAS_MAP_PATH = "data/er_alias_map.json"
TBOX_PATH = "ontology/tbox.yaml"


def load_claims(path: str = CLAIMS_PATH) -> list[dict]:
    if not os.path.exists(path):
        return []
    claims = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                claims.append(json.loads(line))
    return claims


def load_alias_map(path: str = ALIAS_MAP_PATH) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def functional_predicates(tbox_path: str = TBOX_PATH) -> set[str]:
    with open(tbox_path) as f:
        tbox = yaml.safe_load(f)
    return {name for name, spec in tbox["relations"].items() if spec.get("functional")}


def resolve_subject(subject_id: str, alias_map: dict[str, str]) -> str:
    return alias_map.get(subject_id.strip().lower(), subject_id.strip().lower())


def object_key(claim: dict) -> str:
    """Normalized identity of a claim's object, for grouping distinct values."""
    if claim.get("object_literal"):
        return f"literal:{claim['object_literal'].strip().lower()}"
    if claim.get("object_id"):
        return f"entity:{claim['object_id'].strip().lower()}"
    return "unknown"


def detect_conflict_candidates(
    claims: list[dict] | None = None, alias_map: dict[str, str] | None = None
) -> list[dict]:
    """Returns groups: {"subject", "predicate", "claims": [...]} for every
    (resolved subject, functional predicate) with >1 distinct object value —
    the raw candidate set classify.py then rules SUPERSEDES/scope/granularity
    out of before anything is treated as a true conflict."""
    claims = claims if claims is not None else load_claims()
    alias_map = alias_map if alias_map is not None else load_alias_map()
    functional = functional_predicates()

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in claims:
        if c.get("predicate") not in functional:
            continue
        if c.get("polarity") == "negate":
            continue
        subj = resolve_subject(c["subject_id"], alias_map)
        groups[(subj, c["predicate"])].append(c)

    candidates = []
    for (subj, predicate), group_claims in groups.items():
        distinct_objects = {object_key(c) for c in group_claims}
        distinct_objects.discard("unknown")
        if len(distinct_objects) > 1:
            candidates.append({"subject": subj, "predicate": predicate, "claims": group_claims})
    return candidates
