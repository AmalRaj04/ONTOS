"""Confluence adapter. Real field shapes confirmed by reading
vendor/EnterpriseRAG-Bench/generated_data/sources/confluence/**/*.json directly (per
BUILD-SPEC.md §8.2's "inspect real record shapes before writing each adapter"), not
assumed from the field-name list in the corpus's own README.
"""

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from src.ingest.adapters.base import SourceAdapter
from src.ingest.adapters.common import assemble_body, get_title
from src.ingest.simhash import compute_simhash
from src.schema.ids import node_id
from src.schema.models import Document


def _parse_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class ConfluenceAdapter(SourceAdapter):
    source_system = "confluence"

    def iter_documents(self, path: str) -> Iterator[Document]:
        root = Path(path) / "confluence"
        for file_path in sorted(root.rglob("*.json")):
            with open(file_path) as f:
                rec = json.load(f)
            native_id = rec.get("dataset_doc_uuid") or file_path.stem
            body = assemble_body(rec)
            yield Document(
                doc_id=node_id("doc", self.source_system, native_id),
                source_system=self.source_system,
                native_id=native_id,
                title=get_title(rec),
                body=body,
                created_at=_parse_date(rec.get("created_at")),
                author_raw=rec.get("author"),
                thread_key=None,  # Confluence pages have no thread concept
                uri=rec.get("original_location"),
                content_hash=hashlib.sha256(body.encode()).hexdigest(),
                simhash=compute_simhash(body),
                # `space` is the page's self-declared container; its own file-system
                # location under generated_data/sources/confluence/<dir>/ can differ
                # (the corpus's noise injection deliberately misfiles some pages) —
                # exactly the case query/traverse.py's inferred-container handling
                # (docs/planning/02 §6.1) needs to demonstrate.
                declared_container=rec.get("space"),
            )
