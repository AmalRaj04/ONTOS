"""BUILD-SPEC.md §9 step 1 — Normalize. Shared by both the corpus side (ER) and the
question side (src/query/anchor.py imports this same module, per §11 step 1: "same
normalization code as §9... can never drift apart")."""

import re

_HONORIFICS = {"mr", "mrs", "ms", "dr", "prof", "sir", "madam"}

# Seed nickname lexicon (given-name equivalence classes). BUILD-SPEC.md §9 step 1
# also asks for corpus-mined nicknames (signature blocks, email headers); given the
# build's time budget this seed list is used as-is rather than expanded via mining —
# noted as a scope trade-off, not a silent gap.
_NICKNAME_GROUPS = [
    {"robert", "bob", "rob", "bobby"},
    {"william", "bill", "will", "billy"},
    {"richard", "rick", "dick", "rich"},
    {"michael", "mike", "mikey"},
    {"elizabeth", "liz", "beth", "eliza", "betty"},
    {"katherine", "kate", "katie", "kathy", "catherine"},
    {"jennifer", "jen", "jenny"},
    {"christopher", "chris"},
    {"matthew", "matt"},
    {"andrew", "andy", "drew"},
    {"daniel", "dan", "danny"},
    {"joseph", "joe", "joey"},
    {"jonathan", "jon", "johnny"},
    {"alexander", "alex"},
    {"nicholas", "nick"},
    {"benjamin", "ben", "benny"},
    {"samuel", "sam", "sammy"},
    {"thomas", "tom", "tommy"},
    {"anthony", "tony"},
    {"patricia", "pat", "patty", "tricia"},
    {"margaret", "meg", "maggie", "peggy"},
    {"stephanie", "steph"},
    {"jacob", "jake"},
    {"nathaniel", "nate"},
    {"timothy", "tim"},
    {"gregory", "greg"},
    {"edward", "ed", "eddie"},
    {"charles", "charlie", "chuck"},
    {"david", "dave"},
    {"james", "jim", "jimmy"},
]
NICKNAME_TO_CANONICAL: dict[str, str] = {}
for group in _NICKNAME_GROUPS:
    canonical = sorted(group, key=len)[-1]
    for name in group:
        NICKNAME_TO_CANONICAL[name] = canonical

_EMAIL_RE = re.compile(r"^[A-Za-z0-9_.+\-]+@[A-Za-z0-9\-]+\.[A-Za-z0-9\-.]+$")
_HANDLE_RE = re.compile(r"^@?[A-Za-z0-9_.\-]+$")


def normalize_name(raw: str) -> str:
    """Lowercase, strip honorifics/punctuation, collapse whitespace."""
    if not raw:
        return ""
    tokens = re.findall(r"[A-Za-z'\-]+", raw.lower())
    tokens = [t for t in tokens if t not in _HONORIFICS]
    return " ".join(tokens)


def looks_like_email(raw: str) -> bool:
    return bool(raw) and bool(_EMAIL_RE.match(raw.strip()))


def looks_like_handle(raw: str) -> bool:
    """A bare single-token identifier with no spaces — Slack/GitHub username shape,
    as opposed to a "First Last" display name."""
    if not raw:
        return False
    raw = raw.strip()
    if " " in raw or looks_like_email(raw):
        return False
    return bool(_HANDLE_RE.match(raw))


def email_local_part(email: str) -> str:
    return email.split("@", 1)[0].lower() if "@" in email else email.lower()


def split_name(raw: str) -> tuple[str, str]:
    """(given, surname) from a normalized "first ... last" string. Middle tokens are
    dropped from surname consideration — only the first and last token matter for
    blocking keys."""
    norm = normalize_name(raw)
    parts = norm.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def expand_nickname(given: str) -> str:
    return NICKNAME_TO_CANONICAL.get(given, given)


def soundex(s: str) -> str:
    """Standard soundex, applied to a surname token."""
    s = re.sub(r"[^A-Za-z]", "", s).upper()
    if not s:
        return "0000"
    codes = {
        **dict.fromkeys("BFPV", "1"),
        **dict.fromkeys("CGJKQSXZ", "2"),
        **dict.fromkeys("DT", "3"),
        "L": "4",
        **dict.fromkeys("MN", "5"),
        "R": "6",
    }
    first_letter = s[0]
    encoded = [codes.get(c, "") for c in s]
    result = [first_letter]
    prev = encoded[0]
    for code in encoded[1:]:
        if code and code != prev:
            result.append(code)
        if code != "":
            prev = code
        elif s[len(result) - 1 : len(result)] not in "HW":
            prev = ""
    out = "".join(result)[:4]
    return (out + "000")[:4]


def name_similarity(a: str, b: str) -> float:
    """Cheap token-overlap similarity in [0, 1] between two normalized display
    names — no external deps, good enough for a scoring feature (not the sole
    signal)."""
    ta, tb = set(normalize_name(a).split()), set(normalize_name(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0
