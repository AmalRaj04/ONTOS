"""Google Drive adapter. Real field shapes confirmed from
vendor/EnterpriseRAG-Bench/generated_data/sources/google_drive/**/*.json.
source_system is "gdrive" per BUILD-SPEC.md §7.5's SourceSystem literal, but the
corpus directory is named "google_drive" — the two differ, see PROJECT.md."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

from src.ingest.adapters.base import SourceAdapter
from src.ingest.adapters.common import assemble_body, get_title, iter_records, parse_iso_date
from src.ingest.simhash import compute_simhash
from src.schema.ids import node_id
from src.schema.models import Document


class GDriveAdapter(SourceAdapter):
    source_system = "gdrive"
    _corpus_dir_name = "google_drive"

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
            author_raw=rec.get("owner"),
            thread_key=None,
            uri=rec.get("original_location") or f"gdrive://{native_id}",
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
            simhash=compute_simhash(body),
            # drive_area is the self-declared area; `path` is the actual file
            # location — same misfiled-document signal as confluence's `space`
            # vs directory (PROJECT.md decision — misfiled-doc demo material).
            declared_container=rec.get("drive_area"),
        )

    def iter_documents(self, path: str) -> Iterator[Document]:
        for rec, file_path in iter_records(path, self._corpus_dir_name):
            yield self.build_document(rec, file_path)
