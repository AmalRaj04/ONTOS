"""Slack adapter. Real field shapes confirmed from
vendor/EnterpriseRAG-Bench/generated_data/sources/slack/**/*.json — threads, not
individual messages, are the document unit; title_field_name is "channel" (there is
no subject line), content is the "thread" field (a list of messages)."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

from src.ingest.adapters.base import SourceAdapter
from src.ingest.adapters.common import assemble_body, get_title, iter_records, parse_unix_ts
from src.ingest.simhash import compute_simhash
from src.schema.ids import node_id
from src.schema.models import Document


class SlackAdapter(SourceAdapter):
    source_system = "slack"

    def build_document(self, rec: dict, file_path: Path) -> Document:
        native_id = rec.get("dataset_doc_uuid") or file_path.stem
        body = assemble_body(rec)
        participants = rec.get("participants") or []
        thread_ts = rec.get("thread_ts")
        return Document(
            doc_id=node_id("doc", self.source_system, native_id),
            source_system=self.source_system,
            native_id=native_id,
            title=get_title(rec),
            body=body,
            created_at=parse_unix_ts(rec.get("first_message_ts")),
            author_raw=participants[0] if participants else None,
            thread_key=str(thread_ts) if thread_ts is not None else None,
            uri=f"slack://{rec.get('channel')}/{thread_ts}",
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
            simhash=compute_simhash(body),
            declared_container=rec.get("channel"),
        )

    def iter_documents(self, path: str) -> Iterator[Document]:
        for rec, file_path in iter_records(path, self.source_system):
            yield self.build_document(rec, file_path)
