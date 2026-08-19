"""BUILD-SPEC.md §8.2. Every adapter's contract: input = raw source records, output =
list[Document]. Nothing else crosses that boundary — chunking, mention extraction, and
everything downstream is source-agnostic."""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from src.schema.models import Document


class SourceAdapter(ABC):
    source_system: str

    @abstractmethod
    def build_document(self, rec: dict, file_path: Path) -> Document:
        """Build one Document from a single already-loaded raw record. Exposed
        separately from iter_documents() so the M2 priority-tier loader
        (src/ingest/priority.py) can build a Document for one specific known file
        (looked up via the corpus's uuid_index.json) without scanning the whole
        source directory."""

    @abstractmethod
    def iter_documents(self, path: str) -> Iterator[Document]:
        """Yield normalized Document records from raw source files."""
