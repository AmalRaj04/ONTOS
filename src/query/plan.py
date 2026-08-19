"""Classify a question into LOOKUP/MULTIHOP/CONFLICT/AGGREGATE/TEMPORAL — one
structured-output LLM call, BUILD-SPEC.md §11 step 2. M1 implements the classifier
and entity/predicate extraction fully; src/query/traverse.py (M1) only executes the
LOOKUP branch — the other four are M5 work, per BUILD-SPEC.md §13's milestone order.
"""

from dataclasses import dataclass

from src.llm.router import LLMRouter

PLAN_CLASSES = ("LOOKUP", "MULTIHOP", "CONFLICT", "AGGREGATE", "TEMPORAL")

_PROMPT = """Classify this question about a company's internal knowledge graph as JSON
under the key "items", containing exactly one object:
{{"plan_class": "LOOKUP"|"MULTIHOP"|"CONFLICT"|"AGGREGATE"|"TEMPORAL",
  "entities": [str, ...], "predicate_hint": str|null}}

- LOOKUP: a single direct fact about one entity (e.g. "who owns X", "what is X's status").
- MULTIHOP: requires traversing a relationship path between two or more entities.
- CONFLICT: explicitly asks about a contradiction or which of several claims is correct.
- AGGREGATE: asks for a count, list, or summary across many entities.
- TEMPORAL: asks about a specific point in time, sequence, or change over time.

"entities" is every person/team/project/ticket/customer name mentioned or implied.
"predicate_hint" is one of OWNS, WORKS_ON, MEMBER_OF, REPORTS_TO, LAUNCH_DATE, STATUS,
DEADLINE, BLOCKS, PRICE, HEADCOUNT, PRIORITY, TIER, ATTENDED, REVIEWED_BY, LINKED_TO,
or null if none clearly applies.

QUESTION: {question}"""


@dataclass
class Plan:
    plan_class: str
    entities: list[str]
    predicate_hint: str | None


def classify_question(router: LLMRouter, question: str) -> Plan:
    result = router.complete(_PROMPT.format(question=question), task="plan_classification")
    items = result.get("items", [])
    parsed = items[0] if items else {}
    plan_class = parsed.get("plan_class", "LOOKUP")
    if plan_class not in PLAN_CLASSES:
        plan_class = "LOOKUP"
    return Plan(
        plan_class=plan_class,
        entities=parsed.get("entities") or [],
        predicate_hint=parsed.get("predicate_hint"),
    )
