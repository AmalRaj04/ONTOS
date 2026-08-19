"""Resumable checkpointing — BUILD-SPEC.md §2: "Every write path must be resumable/
checkpointed... A crash at document 300,000 must resume near there, not at zero."
One plain integer (documents-processed-so-far) per source, since adapters iterate
their corpus directory in a fixed sorted order — the same offset always names the
same document."""

from pathlib import Path


class Checkpoint:
    def __init__(self, name: str, checkpoint_dir: str = "data/checkpoints"):
        self._path = Path(checkpoint_dir) / f"{name}.offset"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> int:
        if not self._path.exists():
            return 0
        return int(self._path.read_text().strip() or 0)

    def save(self, offset: int) -> None:
        self._path.write_text(str(offset))
