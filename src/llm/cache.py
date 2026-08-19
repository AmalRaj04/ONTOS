"""Content-addressed response cache — BUILD-SPEC.md §16: "caches by content hash of
(prompt, model) so a re-run or a retry never re-spends budget." Flat-file JSON store
under .cache/llm/ (gitignored — see .gitignore's checkpoints/.cache/ entries)."""

import hashlib
import json
from pathlib import Path


class ResponseCache:
    def __init__(self, cache_dir: str = ".cache/llm"):
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _key(self, prompt: str, model: str) -> str:
        raw = f"{model}\x1f{prompt}".encode()
        return hashlib.blake2b(raw, digest_size=16).hexdigest()

    def get(self, prompt: str, model: str) -> dict | None:
        path = self._dir / f"{self._key(prompt, model)}.json"
        if not path.exists():
            return None
        with open(path) as f:
            return json.load(f)["response"]

    def set(self, prompt: str, model: str, response: dict) -> None:
        path = self._dir / f"{self._key(prompt, model)}.json"
        with open(path, "w") as f:
            json.dump({"model": model, "response": response}, f)
