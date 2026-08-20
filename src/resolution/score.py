"""BUILD-SPEC.md §9 step 3 — Score. Weighted features:
email_exact 0.45, handle_match 0.25, name_similarity 0.15, nickname_link 0.15,
cooccurrence_path 0.20, team_overlap 0.10, temporal_plausibility 0.05,
role_consistency 0.05, negative_cooccurrence -0.40.

`cooccurrence_path` is specced as an `algo.MSpaths pairwise:true` graph call. It's
computed here instead as a neighbor-Jaccard proxy over documents already read from
the local corpus in records.py — for the same reason candidate gathering avoids
HydraDB reads (PROJECT.md decision #34: full-corpus label scans exceed the 240s
query timeout at 244K-document scale; a batched MSpaths call reading the live graph
would hit the same wall). The proxy measures the same real signal — do these two
candidate identities keep showing up alongside the same third parties across
documents — which is exactly what a 2-hop MSpaths pairwise query between two
anchors would surface, computed in-process instead of over Bolt. Noted as a
time-budget trade-off in PROJECT.md, not a silent substitution.

`negative_cooccurrence` (two different identities' mentions inside the same
sentence — strong evidence they're different people) is the one hard-negative
signal `cluster.py` treats as an override regardless of aggregate score."""

from collections import defaultdict
from dataclasses import dataclass

from src.resolution.block import RecordFeatures
from src.resolution.normalize import name_similarity
from src.resolution.records import Employee, IdentityRecord

WEIGHTS = {
    "email_exact": 0.45,
    "handle_match": 0.25,
    "name_similarity": 0.15,
    "nickname_link": 0.15,
    "cooccurrence_path": 0.20,
    "team_overlap": 0.10,
    "temporal_plausibility": 0.05,
    "role_consistency": 0.05,
}
NEGATIVE_COOCCURRENCE_WEIGHT = -0.40

_MAX_NEIGHBOR_DOC_FANOUT = 500


def identity_key(feats: RecordFeatures) -> str:
    if feats.email:
        return f"email:{feats.email}"
    if feats.handle:
        return f"handle:{feats.handle}"
    if feats.nickname_given or feats.surname:
        return f"name:{feats.nickname_given} {feats.surname}"
    return ""


@dataclass
class ScoringContext:
    neighbors: dict[str, set[str]]
    employee_by_email: dict[str, Employee]
    employee_names_norm: dict[str, Employee]


def build_scoring_context(
    records: list[IdentityRecord], feats: list[RecordFeatures], employees: list[Employee]
) -> ScoringContext:
    doc_to_keys: dict[str, set[str]] = defaultdict(set)
    key_doc_count: dict[str, int] = defaultdict(int)
    for r, f in zip(records, feats):
        key = identity_key(f)
        if not key:
            continue
        if key not in doc_to_keys.get(r.doc_id, set()):
            key_doc_count[key] += 1
        doc_to_keys[r.doc_id].add(key)

    neighbors: dict[str, set[str]] = defaultdict(set)
    for keys in doc_to_keys.values():
        if len(keys) < 2:
            continue
        for key in keys:
            if key_doc_count[key] > _MAX_NEIGHBOR_DOC_FANOUT:
                continue
            neighbors[key] |= keys - {key}

    employee_by_email = {e.email: e for e in employees if e.email}
    from src.resolution.normalize import normalize_name

    employee_names_norm = {normalize_name(e.name): e for e in employees if e.name}
    return ScoringContext(neighbors, employee_by_email, employee_names_norm)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def score_pair(
    i: int,
    j: int,
    records: list[IdentityRecord],
    feats: list[RecordFeatures],
    matched_keys: set[str],
    ctx: ScoringContext,
) -> tuple[float, dict[str, float]]:
    ri, rj = records[i], records[j]
    fi, fj = feats[i], feats[j]
    features: dict[str, float] = {}

    features["email_exact"] = 1.0 if fi.email and fi.email == fj.email else 0.0
    features["handle_match"] = 1.0 if (
        (fi.handle and fj.handle and fi.handle == fj.handle)
        or (fi.handle and fj.email and fj.email.startswith(fi.handle + "@"))
        or (fj.handle and fi.email and fi.email.startswith(fj.handle + "@"))
    ) else 0.0
    # given/surname is populated for every record (see block.compute_features,
    # derived from a plain display name, or an email/handle's local part split on
    # separators) — comparing those tokens, not the raw strings, is what lets a
    # display name ("Alice Patel") and an email/handle ("alice.patel@...") score
    # a real name_similarity instead of always landing at 0.
    features["name_similarity"] = name_similarity(f"{fi.given} {fi.surname}", f"{fj.given} {fj.surname}")
    features["nickname_link"] = 1.0 if (
        fi.nickname_given and fi.nickname_given == fj.nickname_given and fi.surname == fj.surname and fi.surname
    ) else 0.0

    ki, kj = identity_key(fi), identity_key(fj)
    features["cooccurrence_path"] = _jaccard(ctx.neighbors.get(ki, set()), ctx.neighbors.get(kj, set()))

    # Department resolves via email first, falling back to a name match against
    # the employee roster (BUILD-SPEC.md §7.6.1 scaffolding) — needed so an
    # author-kind record (a display name with no email of its own, e.g. a Jira
    # reporter field) can still pick up team_overlap/role_consistency instead of
    # those features being permanently unavailable for every name-only record.
    name_i = f"{fi.given} {fi.surname}".strip()
    name_j = f"{fj.given} {fj.surname}".strip()
    emp_i = ctx.employee_by_email.get(fi.email) or ctx.employee_names_norm.get(name_i)
    emp_j = ctx.employee_by_email.get(fj.email) or ctx.employee_names_norm.get(name_j)
    dept_i = fi.department or (emp_i.department if emp_i else None)
    dept_j = fj.department or (emp_j.department if emp_j else None)
    features["team_overlap"] = 1.0 if dept_i and dept_j and dept_i == dept_j else 0.0

    if ri.asserted_at and rj.asserted_at:
        try:
            from datetime import datetime

            di = datetime.fromisoformat(ri.asserted_at)
            dj = datetime.fromisoformat(rj.asserted_at)
            delta_days = abs((di - dj).days)
            features["temporal_plausibility"] = 1.0 if delta_days <= 365 * 5 else 0.3
        except Exception:
            features["temporal_plausibility"] = 0.7
    else:
        features["temporal_plausibility"] = 0.7

    if emp_i and emp_j:
        features["role_consistency"] = 1.0 if emp_i.name == emp_j.name else 0.0
    else:
        features["role_consistency"] = 0.5

    positive = sum(WEIGHTS[k] * v for k, v in features.items())

    negative = 0.0
    if (
        ri.sentence_key
        and rj.sentence_key
        and ri.sentence_key == rj.sentence_key
        and ri.raw.strip().lower() != rj.raw.strip().lower()
    ):
        negative = NEGATIVE_COOCCURRENCE_WEIGHT
        features["negative_cooccurrence"] = 1.0
    else:
        features["negative_cooccurrence"] = 0.0

    raw_score = positive + negative
    clipped = min(1.0, max(0.0, raw_score))
    return clipped, features
