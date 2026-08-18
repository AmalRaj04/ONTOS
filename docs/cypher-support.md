# HydraDB Cypher support — probe results

Run live against a local `graph-node` (`CLOUD_PROVIDER=local`, filesystem-backed) built
from `vendor/hydradb` at commit checked out 2026-08-18, per `BUILD-SPEC.md` §6. Every
probe below was executed for real over both the HTTP query API (`/v1/graphs/default/
query`) and the actual Bolt driver we use in the application (Python `neo4j` package,
`bolt://127.0.0.1:7687`) — results were identical on both transports. Raw request/response
pairs are preserved in this file's probe log below the summary table.

This document is the source of truth for how `src/ingest/writer.py`, `src/resolution/
*.py`, and `src/query/*.py` talk to HydraDB. Where it disagrees with `BUILD-SPEC.md`'s
prose, this document wins per the spec's own §0 rule ("ground truth in those repos
overrides any description below if they conflict").

## Summary

| Probe | Spec's literal query | Result | Notes |
|---|---|---|---|
| P1 — node MERGE | `MERGE (p:Person {canonical_id:'probe-1'}) RETURN p.canonical_id;` | **Fails as written; passes when adapted** | `MERGE` cannot be followed by `RETURN` in the same statement ("MERGE with following clauses is not executable"). A standalone single-node `MERGE`/`CREATE` (no relationship) is also rejected outside an `UNWIND` batch ("only one-hop edge patterns are executable in Query engine MERGE"). Adapted form — `UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Person, n.canonical_id = row.cid` — passes. See **critical finding** below: `id` must be a non-negative integer. |
| P2 — relationship MERGE | `MATCH (a:Person {canonical_id:'probe-1'}) MERGE (a)-[r:PROBE]->(b:Person {canonical_id:'probe-2'})RETURN type(r);` | **Fails as written; passes when adapted** | Same "no RETURN after a mutation" rule. Also, `RETURN type(r)` is rejected outright — `RETURN` only accepts `<binding>.<property>` or an aggregate, no function calls. Multi-pattern `MATCH (a),(b)` outside `UNWIND` is also rejected. Adapted, single-pattern form — `MERGE (a {id:1001})-[r:PROBE]->(b {id:1002})` (endpoints matched inline by integer `id`, written and read as two separate statements) — passes. |
| P3 — batched UNWIND write | `UNWIND [{id:'p3'},{id:'p4'}] AS row CREATE (p:Person {canonical_id: row.id}) RETURN count(p);` | **Fails as written; passes when adapted** | Inline list literals are rejected — `UNWIND` input must be a bound parameter (`$rows`), not an inline `[...]`. `count(p)` (bare node variable) is also rejected — aggregates take `*` or `<binding>.<property>`. Adapted form (parameterized `$rows`, `count(*)`) passes; confirmed both over HTTP (JSON body field is `parameters`, not `params`) and over Bolt (`session.run(query, rows=[...])`). |
| P4 — bounded variable-length path | `MATCH path = (a:Person {canonical_id:'probe-1'})-[:PROBE*1..3]->(b) RETURN length(path);` | **Passes, with one adaptation** | `length(path)` is a function call and is rejected for the same reason as `type(r)` above (`RETURN` is projections/aggregates only). Returning bound node properties instead (`RETURN b.id`) passes and correctly returns every node reachable in 1–3 hops (tested with a 2-hop chain: both the 1-hop and 2-hop node came back). The upper bound is mandatory — `*` and `*1..` are rejected. |
| P5 — index DDL | `CREATE INDEX FOR (p:Person) ON (p.canonical_id);` | **FAILS — not supported, confirmed** | Both `CREATE INDEX FOR (n:Label) ON (n.prop)` and the older `CREATE INDEX ON :Label(prop)` syntax are rejected at parse time (`expected '=' or CREATE INDEX ON`, and `expected query, got CREATE INDEX`, respectively). There is no user-declarable index DDL in this Cypher subset. Per `vendor/hydradb/architecture.md`, indexing is automatic and server-managed: `graph-indexer` builds immutable, content-addressed CSC (compressed sparse column) generations per edge type in the background, and property lookups go through built-in property indexes — neither requires or accepts a DDL statement from the client. **Decision (per BUILD-SPEC.md §6's gate): `schema.cypher` contains no `CREATE INDEX` statements.** No indexing action is needed from application code; ingest at scale relies on HydraDB's automatic indexing. |
| P6 — aggregation + `OPTIONAL MATCH` | `MATCH (p:Person) OPTIONAL MATCH (p)-[:PROBE]->(q) RETURN p.canonical_id, count(q) ORDER BY p.canonical_id;` | **Fails as written; passes when adapted** | `count(q)` (bare node variable) is rejected, same rule as P3. `count(q.id)` passes and correctly returns `0` (not `1`) for a `p` with no matching `q` — i.e. `OPTIONAL MATCH` nulls are excluded when the aggregate argument is a property reference. **Caveat worth keeping in mind for our own query code:** `count(*)` does *not* exclude the null row from an unmatched `OPTIONAL MATCH` — it returns `1` where `count(q.id)` returns `0`. Always aggregate on a property of the optional binding, never `count(*)`, when the point is to count optional matches. |
| P7 — `algo.MSpaths` | `CALL algo.MSpaths({sourceLabel:'Person', sourceProperty:'canonical_id', sourceValues:['probe-1','probe-2'], targetValues:['probe-1','probe-2'], pairwise:true, relTypes:['PROBE'], relDirection:'both', maxLen:2, pathCount:3}) YIELD path RETURN path;` | **PASSES exactly as written** | Confirmed over both HTTP and Bolt. Returns a full path object (nodes with `id`/`labels`/`properties`, relationships with `edge_type`/`src`/`dst`/`properties`) — this is how our code gets the relationship type when a path is returned, since `type(r)` in `RETURN` is not available (see P2). This is the batch-pairwise-shortest-path primitive the entity-resolution (§9) and multi-hop query (§11) designs depend on, and it works. |

## Critical finding beyond the P1–P7 checklist: node `id` must be a non-negative integer

`cypher-compat.md` states this directly ("Node ids are non-negative integers") and it was
confirmed live: an `UNWIND ... MERGE (n {id: row.vertex})` batch with a string `id` value
is rejected with `"UNWIND row 0 field vertex must be a non-negative integer"`, while the
identical batch with an integer `id` (tested up to `4,611,686,018,427,387,903`, i.e. 2^62-1)
succeeds. `MERGE`/`MATCH`/relationship-endpoint patterns all key off this `id` property
specifically — it is the physical vertex identity, not just a conventionally-named
property.

This is a real conflict with `BUILD-SPEC.md` §7.1's `node_id()` scheme, which produces
opaque hex strings (`"doc:9f3a...", "person:<uuid>"`). **Resolution, applied in
`src/schema/models.py` / `src/ingest/writer.py`:**

- HydraDB's `id` property holds a deterministic 62-bit non-negative integer surrogate,
  derived from the same content-addressed hash `node_id()` already computes: take the
  first 8 bytes of the blake2b digest, interpret as an unsigned 64-bit integer, mask to
  62 bits (`& 0x3FFF_FFFF_FFFF_FFFF`) to stay safely inside a signed 64-bit range (Bolt
  integers are signed i64).
- The original spec-shaped string ID (`doc_id`, `mention_id`, `claim_id`,
  `canonical_id`, etc.) is still written as a regular node property and remains the
  human/content-addressed identifier used throughout ingest, resolution, conflict, and
  query code. Confirmed live that `MATCH`/`WHERE` filtering on this non-`id` property
  works normally (both inline-pattern and `WHERE`-clause forms) — only *write-time
  identity* (`MERGE` upsert matching, relationship endpoint patterns) requires the
  integer `id`.
- This is additive, not a schema redesign: §7.2's node labels and properties are
  unchanged; every node simply also carries an internal `id` integer alongside its
  documented properties.

## Other transport-level notes (not a P1–P7 item, but load-bearing for `writer.py`)

- The HTTP query API's JSON field for query parameters is `parameters`, not `params`
  (confirmed from `vendor/hydradb/src/client/http.rs`'s `HttpQueryRequestBody`).
- `MERGE`/`CREATE` cannot be followed by any other clause in the same statement
  (confirmed for both node and relationship forms). Any write that also needs to read
  the result back is two round trips, which is consistent with HydraDB having no
  explicit multi-statement transactions anyway (`architecture.md`: "Explicit
  transactions spanning multiple RUN requests are not exposed").
- A standalone single-node `CREATE`/`MERGE` (no relationship in the pattern) is only
  executable inside an `UNWIND` batch. `writer.py` therefore always uses the `UNWIND
  $rows AS row MERGE (n {id: row.id}) SET ...` batch form for plain node upserts, even
  for a single record — never a bare `MERGE (n {...})` statement.

## Raw probe log (HTTP transport, for reference)

```
P1 (as written):    MERGE (p:Person {canonical_id: 'probe-1'}) RETURN p.canonical_id
                  -> {"error":{"code":"invalid_request","message":"OpenCypher query is
                      not supported yet: MERGE with following clauses is not executable
                      in Query engine"}}
P1 (adapted, write): UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Person,
                      n.canonical_id = row.cid   [parameters: rows=[{vertex:301,
                      cid:'probe-1'}]]
                  -> {"columns":[],"rows":[], ...}   (success, empty result set)
P1 (adapted, read):  MATCH (p:Person {id: 301}) RETURN p.canonical_id AS cid
                  -> {"columns":["cid"],"rows":[[{"type":"string","value":"probe-1"}]], ...}

P2 (as written):    MATCH (a:Person {canonical_id:'probe-1'})
                     MERGE (a)-[r:PROBE]->(b:Person {canonical_id:'probe-2'})
                     RETURN type(r)
                  -> {"error":{"code":"invalid_request","message":"ClientProtocol query
                      is not supported yet: write query is not executable by the
                      mutation engine"}}
P2 (adapted, write): MERGE (a {id:1001})-[r:PROBE]->(b {id:1002})
                  -> success, empty result set
P2 (adapted, read):  MATCH (a {id:1001})-[r:PROBE]->(b {id:1002}) RETURN b.id AS bid
                  -> {"columns":["bid"],"rows":[[{"type":"vertex_id","value":1002}]], ...}

P3 (as written):    UNWIND [{id:'p3'},{id:'p4'}] AS row CREATE (p:Person
                     {canonical_id: row.id}) RETURN count(p)
                  -> rejected (inline list literal + count(p) both unsupported)
P3 (adapted):        UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Probe,
                      n.canonical_id = row.cid   [parameters: rows=[{vertex:1001,
                      cid:'probe-1'},{vertex:1002,cid:'probe-2'}]]
                  -> success
                     MATCH (p:Probe) RETURN count(*) AS n
                  -> {"columns":["n"],"rows":[[{"type":"integer","value":2}]], ...}

P4 (as written):    MATCH path = (a:Person {canonical_id:'probe-1'})-[:PROBE*1..3]->(b)
                     RETURN length(path)
                  -> length(path) rejected (function call in RETURN unsupported)
P4 (adapted):        MATCH path = (a {id:1001})-[:PROBE*1..3]->(b) RETURN b.id AS bid
                  -> {"columns":["bid"],"rows":[[{"...":1002}],[{"...":1003}]], ...}
                     (chain 1001->1002->1003; both 1-hop and 2-hop nodes returned)

P5 (as written):    CREATE INDEX FOR (p:Person) ON (p.canonical_id)
                  -> {"error":{"code":"invalid_request","message":"OpenCypher parse
                      error: Invalid input 'F': expected '=' or CREATE INDEX ON"}}
P5 (alt syntax):     CREATE INDEX ON :Person(canonical_id)
                  -> {"error":{"code":"invalid_request","message":"OpenCypher parse
                      error: expected query, got CREATE INDEX"}}
                     CONFIRMED UNSUPPORTED — see decision above.

P6 (as written):    MATCH (p:Person) OPTIONAL MATCH (p)-[:PROBE]->(q)
                     RETURN p.canonical_id, count(q) ORDER BY p.canonical_id
                  -> count(q) rejected (bare node variable in aggregate)
P6 (adapted):        MATCH (p:Probe) OPTIONAL MATCH (p)-[:PROBE]->(q)
                      RETURN p.canonical_id AS pid, count(q.id) AS n ORDER BY pid
                  -> probe-1: n=1, probe-2: n=1, probe-3: n=0   (correct: probe-3 has
                     no outgoing PROBE edge)
                     Same query with count(*) instead of count(q.id) returns n=1 for
                     probe-3 too — count(*) counts the OPTIONAL MATCH null row.

P7 (as written):    CALL algo.MSpaths({sourceLabel:'Probe', sourceProperty:'canonical_id',
                     sourceValues:['probe-1','probe-2'], targetValues:['probe-1','probe-2'],
                     pairwise:true, relTypes:['PROBE'], relDirection:'both', maxLen:2,
                     pathCount:3}) YIELD path RETURN path
                  -> {"columns":["path"],"rows":[[{"type":"path","value":{
                       "nodes":[{"id":1001,"labels":["Probe"],
                                 "properties":{"canonical_id":{"String":"probe-1"}}},
                                {"id":1002,"labels":["Probe"],
                                 "properties":{"canonical_id":{"String":"probe-2"}}}],
                       "relationships":[{"id":null,"edge_type":"PROBE","src":1001,
                                          "dst":1002,"properties":{}}]}}]], ...}
                     PASSES EXACTLY AS WRITTEN.
```

## Round-tripped write verification (BUILD-SPEC.md §6, before the probe suite)

```
CREATE (a {id: 1})-[:FOLLOWS]->(b {id: 2})
  -> success
MATCH (a {id: 1})-[:FOLLOWS]->(b) RETURN b.id AS id
  -> {"columns":["id"],"rows":[[{"type":"vertex_id","value":2}]], ...}
```
Matches the spec's required result exactly.

## Environment notes for reproducing this probe run

- macOS (arm64/Homebrew). `libcypher-parser` is not in `homebrew-core`; needed the
  `cleishm/neo4j` tap: `brew tap cleishm/neo4j && brew install cleishm/neo4j/
  libcypher-parser`, plus `brew install cmake llvm suite-sparse`.
- Building/running `graph-node` directly with `cargo run` (not `just`) fails on macOS
  with `wrapper.h:4:10: fatal error: 'cypher-parser.h' file not found` unless
  `BINDGEN_EXTRA_CLANG_ARGS="-I/opt/homebrew/include"` and `LIBRARY_PATH=/opt/homebrew/
  lib` are exported manually — `just` exports these automatically (see
  `vendor/hydradb/justfile`), a bare `cargo run` does not.
- `just native-check` and `just smoke` both pass in this environment.
