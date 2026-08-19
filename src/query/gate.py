"""The abstention gate — BUILD-SPEC.md §11 step 4: "this file matters more than any
other in the query layer." Treats an empty bounded traversal as evidence of absence
rather than confabulating (BUILD-SPEC.md §1's core thesis).
"""

from dataclasses import dataclass


@dataclass
class AbstentionSignals:
    unresolved_anchors: bool
    claim_count: int
    path_count: int
    max_trust: float
    evidence_sources: int


@dataclass
class Decision:
    abstain: bool
    reason: str


def should_abstain(signals: AbstentionSignals) -> Decision:
    if signals.unresolved_anchors and not signals.claim_count:
        return Decision(True, "entity not found under any known alias")
    if signals.path_count == 0 and signals.claim_count == 0:
        return Decision(True, "entities exist but no relationship found within bound")
    if signals.max_trust < 0.35 and signals.evidence_sources < 2:
        return Decision(True, "only weak, uncorroborated evidence")
    return Decision(False, "")
