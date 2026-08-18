"""Materialize ontology/tbox.yaml as :Class/:Relation nodes in HydraDB — BUILD-SPEC.md
§7.6.2 step 5. Run after tbox.yaml is frozen: `python -m ontology.materialize`.

HydraDB property values are integers, floats, booleans and strings only — no lists, no
nested maps (see docs/cypher-support.md). `range` (a list of class names) and
`source_forms` (a nested per-source mapping) are therefore stored as encoded strings
(comma-joined and JSON respectively); application code that needs the structured form
reads ontology/tbox.yaml directly (this materialization exists to make the ontology
queryable from Cypher too, per BUILD-SPEC.md §7.6, not to replace the YAML as the
source of truth).

Every node also carries HydraDB's required integer `id` (src/schema/ids.py:hydra_id),
since node identity must be a non-negative integer — see docs/cypher-support.md's
"Critical finding" section. Writes go through the UNWIND batch form, the only way to
write standalone nodes outside a relationship pattern (same finding).
"""

import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from neo4j import GraphDatabase

from src.schema.ids import hydra_id, node_id

TBOX_PATH = Path(__file__).parent / "tbox.yaml"


def load_tbox() -> dict:
    with open(TBOX_PATH) as f:
        return yaml.safe_load(f)


def class_rows(classes: dict) -> list[dict]:
    rows = []
    for name, spec in classes.items():
        cid = node_id("class", name)
        rows.append(
            {
                "vertex": hydra_id(cid),
                "class_id": cid,
                "name": name,
                "parent": spec.get("parent") or "",
            }
        )
    return rows


def relation_rows(relations: dict) -> list[dict]:
    rows = []
    for name, spec in relations.items():
        rid = node_id("relation", name)

        def _scalarize(val):
            if isinstance(val, list):
                return ",".join(val)
            return str(val) if val is not None else ""

        rows.append(
            {
                "vertex": hydra_id(rid),
                "relation_id": rid,
                "name": name,
                "domain": _scalarize(spec.get("domain")),
                "range": _scalarize(spec.get("range")),
                "functional": bool(spec.get("functional", False)),
                "temporal": bool(spec.get("temporal", False)),
                "inverse": spec.get("inverse") or "",
                "source_forms_json": json.dumps(spec.get("source_forms") or {}),
            }
        )
    return rows


def materialize(driver, classes: dict, relations: dict) -> None:
    with driver.session(database="default") as session:
        session.run(
            "UNWIND $rows AS row MERGE (n {id: row.vertex}) "
            "SET n:Class, n.class_id = row.class_id, n.name = row.name, "
            "n.parent = row.parent",
            rows=class_rows(classes),
        ).consume()
        session.run(
            "UNWIND $rows AS row MERGE (n {id: row.vertex}) "
            "SET n:Relation, n.relation_id = row.relation_id, n.name = row.name, "
            "n.domain = row.domain, n.range = row.range, "
            "n.functional = row.functional, n.temporal = row.temporal, "
            "n.inverse = row.inverse, n.source_forms_json = row.source_forms_json",
            rows=relation_rows(relations),
        ).consume()


def main() -> None:
    load_dotenv()
    tbox = load_tbox()
    token = os.environ["HYDRADB_AUTH_TOKEN"]
    uri = os.environ.get("HYDRADB_BOLT_URI", "neo4j://127.0.0.1:7687")
    driver = GraphDatabase.driver(uri, auth=("neo4j", token))
    driver.verify_connectivity()
    materialize(driver, tbox["classes"], tbox["relations"])
    with driver.session(database="default") as session:
        n_classes = session.run("MATCH (c:Class) RETURN count(*) AS n").single()["n"]
        n_relations = session.run("MATCH (r:Relation) RETURN count(*) AS n").single()["n"]
    driver.close()
    print(f"materialized {n_classes} :Class nodes, {n_relations} :Relation nodes")


if __name__ == "__main__":
    main()
