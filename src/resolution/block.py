"""BUILD-SPEC.md §9 step 2 — Block. Candidate pairs from exact email, email
local-part, handle, surname, soundex, given-name+team, nickname-expansion, and graph
co-membership (mentions sharing a document/thread).

Blocking keys are inverted indices (key -> record indices); candidate pairs are all
same-bucket pairs, deduplicated across keys. Buckets past `_MAX_BUCKET` are skipped
(a key that groups hundreds of unrelated records — e.g. a common surname with no
other corroborating signal — isn't informative blocking, it's just combinatorics)."""

from collections import defaultdict
from itertools import combinations

from src.resolution.normalize import (
    email_local_part,
    expand_nickname,
    looks_like_email,
    looks_like_handle,
    soundex,
    split_name,
)
from src.resolution.records import Employee, IdentityRecord

_MAX_BUCKET = 250


class RecordFeatures:
    __slots__ = ("email", "handle", "given", "surname", "nickname_given", "department")

    def __init__(self):
        self.email: str | None = None
        self.handle: str | None = None
        self.given: str = ""
        self.surname: str = ""
        self.nickname_given: str = ""
        self.department: str | None = None


def _email_department(employees: list[Employee]) -> dict[str, str]:
    return {e.email: e.department for e in employees if e.email}


_NAME_SEP = str.maketrans(".-_", "   ")


def compute_features(records: list[IdentityRecord], employees: list[Employee]) -> list[RecordFeatures]:
    """Every record also gets a best-effort given/surname derived from whichever
    signal it has (a plain display name directly; an email/handle's local part
    split on separators, e.g. "alice.patel@..." -> given="alice" surname="patel").
    Without this, an email/handle record and a plain-name record could never share
    a surname/soundex/nickname blocking bucket or score a name_similarity — which
    would silently drop the single most useful cross-system link this corpus has
    (a Jira reporter's display name vs. the same person's email cc'd elsewhere)."""
    email_dept = _email_department(employees)
    out = []
    for r in records:
        f = RecordFeatures()
        if r.kind == "email_mention" or looks_like_email(r.raw):
            f.email = r.raw.strip().lower()
            f.department = email_dept.get(f.email)
            local = email_local_part(f.email).translate(_NAME_SEP)
            f.given, f.surname = split_name(local)
        elif r.kind == "handle_mention" or looks_like_handle(r.raw):
            f.handle = r.raw.strip().lstrip("@").lower()
            local = f.handle.translate(_NAME_SEP)
            f.given, f.surname = split_name(local)
        else:
            f.given, f.surname = split_name(r.raw)
        f.nickname_given = expand_nickname(f.given) if f.given else ""
        out.append(f)
    return out


def _add(buckets: dict[str, list[int]], key: str | None, idx: int):
    if not key:
        return
    buckets.setdefault(key, []).append(idx)


def build_blocking_index(records: list[IdentityRecord], feats: list[RecordFeatures]) -> dict[str, dict[str, list[int]]]:
    keys: dict[str, dict[str, list[int]]] = defaultdict(dict)
    email_bucket: dict[str, list[int]] = {}
    local_bucket: dict[str, list[int]] = {}
    handle_bucket: dict[str, list[int]] = {}
    surname_bucket: dict[str, list[int]] = {}
    soundex_bucket: dict[str, list[int]] = {}
    nickname_bucket: dict[str, list[int]] = {}
    given_team_bucket: dict[str, list[int]] = {}
    doc_bucket: dict[str, list[int]] = {}
    thread_bucket: dict[str, list[int]] = {}

    for idx, (r, f) in enumerate(zip(records, feats)):
        if f.email:
            _add(email_bucket, f.email, idx)
            _add(local_bucket, email_local_part(f.email), idx)
        if f.handle:
            _add(handle_bucket, f.handle, idx)
        if f.surname:
            _add(surname_bucket, f.surname, idx)
            _add(soundex_bucket, soundex(f.surname), idx)
        if f.nickname_given:
            _add(nickname_bucket, f.nickname_given, idx)
            if f.department:
                _add(given_team_bucket, f"{f.nickname_given}::{f.department}", idx)
        _add(doc_bucket, r.doc_id, idx)
        if r.thread_key:
            _add(thread_bucket, f"{r.source_system}::{r.thread_key}", idx)

    return {
        "email": email_bucket,
        "email_local": local_bucket,
        "handle": handle_bucket,
        "surname": surname_bucket,
        "soundex": soundex_bucket,
        "nickname": nickname_bucket,
        "given_team": given_team_bucket,
        "doc": doc_bucket,
        "thread": thread_bucket,
    }


def generate_candidate_pairs(records: list[IdentityRecord], feats: list[RecordFeatures]) -> dict[tuple[int, int], set[str]]:
    """Returns {(i, j): {blocking_key_names_that_matched}} for i < j. The set of
    matched keys is passed to score.py so it can weight multi-key agreement."""
    index = build_blocking_index(records, feats)
    pairs: dict[tuple[int, int], set[str]] = defaultdict(set)
    for key_name, buckets in index.items():
        for bucket in buckets.values():
            n = len(bucket)
            if n < 2 or n > _MAX_BUCKET:
                continue
            for i, j in combinations(sorted(bucket), 2):
                pairs[(i, j)].add(key_name)
    return pairs
