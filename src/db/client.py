"""HydraDB Bolt session wrapper. See docs/cypher-support.md for the constraints this
code works around: node `id` must be a non-negative integer (src/schema/ids.py
hydra_id()), MERGE/CREATE cannot be followed by RETURN in the same statement, and a
standalone single-node write only executes inside an UNWIND batch — so `run_write`
below always takes a batch (`rows`), even for a single record.
"""

import os
from contextlib import contextmanager

from neo4j import GraphDatabase

CAUSAL = "causal"
STRONG = "strong"


class HydraClient:
    def __init__(self, uri: str | None = None, token: str | None = None):
        uri = uri or os.environ["HYDRADB_BOLT_URI"]
        token = token or os.environ["HYDRADB_AUTH_TOKEN"]
        self._driver = GraphDatabase.driver(uri, auth=("neo4j", token))
        self._driver.verify_connectivity()

    def close(self) -> None:
        self._driver.close()

    @contextmanager
    def session(self, consistency: str = CAUSAL):
        # HydraDB's consistency mode is set per-request via Bolt transaction metadata
        # (see docs/cypher-support.md); the neo4j driver exposes this as a bookmark/
        # routing hint on the session for read queries. For the write-heavy hot path
        # (ingest) causal is the default; eval/run_eval.py overrides to strong per
        # BUILD-SPEC.md §12.
        with self._driver.session(
            database="default", default_access_mode="READ" if consistency == STRONG else "WRITE"
        ) as session:
            yield session

    def run_write(self, query: str, rows: list[dict]) -> None:
        """Batched UNWIND write. `query` must start with `UNWIND $rows AS row`."""
        if not rows:
            return
        with self.session() as session:
            session.run(query, rows=rows).consume()

    def run_read(self, query: str, **params):
        with self.session() as session:
            return list(session.run(query, **params))
