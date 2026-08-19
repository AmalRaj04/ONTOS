"""BUILD-SPEC.md §8.2. Every adapter's contract: input = raw source records, output =
list[Document]. Nothing else crosses that boundary — chunking, mention extraction, and
everything downstream is source-agnostic."""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from src.schema.models import Document


class SourceAdapter(ABC):
    source_system: str

    @abstractmethod
    def iter_documents(self, path: str) -> Iterator[Document]:
        """Yield normalized Document records from raw source files."""
