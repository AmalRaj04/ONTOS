"""BUILD-SPEC.md §7.5 — frozen. Every ingest adapter's contract is: input = raw source
records, output = list[Document]. Nothing else crosses that boundary."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SourceSystem = Literal[
    "slack", "gmail", "linear", "gdrive", "hubspot",
    "fireflies", "github", "jira", "confluence",
]


class Document(BaseModel):
    doc_id: str
    source_system: SourceSystem
    native_id: str
    title: str | None
    body: str
    created_at: datetime | None
    author_raw: str | None
    thread_key: str | None
    uri: str | None
    content_hash: str
    simhash: int
    declared_container: str | None = None


class Claim(BaseModel):
    claim_id: str
    predicate: str
    subject_id: str
    object_id: str | None
    object_literal: str | None
    polarity: Literal["affirm", "negate"] = "affirm"
    asserted_at: datetime | None
    extraction_confidence: float
    evidence_chunk_id: str
