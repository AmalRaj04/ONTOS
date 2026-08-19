"""Jira adapter. Real field shapes confirmed from
vendor/EnterpriseRAG-Bench/generated_data/sources/jira/**/*.json."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

from src.ingest.adapters.base import SourceAdapter
from src.ingest.adapters.common import assemble_body, get_title, iter_records, parse_iso_date
from src.ingest.simhash import compute_simhash
from src.schema.ids import node_id
from src.schema.models import Document


class JiraAdapter(SourceAdapter):
    source_system = "jira"

    def build_document(self, rec: dict, file_path: Path) -> Document:
        native_id = rec.get("dataset_doc_uuid") or file_path.stem
        body = assemble_body(rec)
        return Document(
            doc_id=node_id("doc", self.source_system, native_id),
            source_system=self.source_system,
            native_id=native_id,
            title=get_title(rec),
            body=body,
            created_at=parse_iso_date(rec.get("created_at")),
            author_raw=rec.get("reporter"),
            thread_key=None,
            uri=f"jira://{rec.get('project')}/{rec.get('key')}",
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
            simhash=compute_simhash(body),
            declared_container=rec.get("project"),
        )

    def iter_documents(self, path: str) -> Iterator[Document]:
        for rec, file_path in iter_records(path, self.source_system):
            yield self.build_document(rec, file_path)
