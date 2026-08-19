"""Question-priority tier — BUILD-SPEC.md §8.3 (amended 2026-08-19): resolve the
documents needed to answer all 500 official + 100 extra eval questions and guarantee
they are ingested first, regardless of what happens with the rest of the corpus.
Missing one of these costs real eval score through false abstention, not just
thoroughness.

Every gold question record carries `expected_doc_ids` (real field name — see
PROJECT.md decision #12), and EnterpriseRAG-Bench's own
`generated_data/uuid_index.json` maps every `dsid_...` id directly to its file path
relative to `generated_data/sources/` (the same base CORPUS_DIR points at) — so the
priority set is looked up directly, not derived by resolving question entities
against ingested data as BUILD-SPEC.md's original §11-style approach would require.
"""

import json
from pathlib import Path

from src.ingest.adapters import ALL_ADAPTERS
from src.schema.models import Document

QUESTIONS_PATHS = [
    "vendor/EnterpriseRAG-Bench/questions.jsonl",
    "vendor/EnterpriseRAG-Bench/extra_questions.jsonl",
]
UUID_INDEX_PATH = "vendor/EnterpriseRAG-Bench/generated_data/uuid_index.json"

# uuid_index.json paths are "<corpus-subdir>/...json" — corpus-subdir names differ
# from our SourceSystem literals in exactly one case (google_drive -> gdrive).
_DIR_TO_SOURCE_SYSTEM = {
    "confluence": "confluence",
    "fireflies": "fireflies",
    "google_drive": "gdrive",
    "github": "github",
    "gmail": "gmail",
    "hubspot": "hubspot",
    "jira": "jira",
    "linear": "linear",
    "slack": "slack",
}


def load_priority_doc_ids() -> set[str]:
    doc_ids: set[str] = set()
    for path in QUESTIONS_PATHS:
        with open(path) as f:
            for line in f:
                q = json.loads(line)
                doc_ids.update(q.get("expected_doc_ids") or [])
    return doc_ids


def resolve_priority_files(corpus_dir: str) -> list[tuple[str, Path]]:
    """Returns (source_system, absolute_file_path) pairs for every priority doc_id
    that resolves to a real file."""
    with open(UUID_INDEX_PATH) as f:
        uuid_index = json.load(f)

    results = []
    missing = 0
    for doc_id in load_priority_doc_ids():
        rel_path = uuid_index.get(doc_id)
        if not rel_path:
            missing += 1
            continue
        dir_name = rel_path.split("/", 1)[0]
        source_system = _DIR_TO_SOURCE_SYSTEM.get(dir_name)
        if not source_system:
            missing += 1
            continue
        results.append((source_system, Path(corpus_dir) / rel_path))

    if missing:
        print(f"[priority] {missing} referenced doc_ids did not resolve to a file")
    return results


def load_priority_documents(corpus_dir: str) -> list[Document]:
    adapters = {name: cls() for name, cls in ALL_ADAPTERS.items()}
    docs = []
    for source_system, abs_path in resolve_priority_files(corpus_dir):
        with open(abs_path) as f:
            rec = json.load(f)
        docs.append(adapters[source_system].build_document(rec, abs_path))
    return docs
