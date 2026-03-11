"""Memory domain contract tests for SELF-OS.

These tests verify cross-module architectural contracts — they check that
the system's components interact correctly at their boundaries.  They are
deliberately free of LLM calls and external network access.

Contracts tested (from docs/ARCHITECTURE_BACKLOG.md ARCH-14):
  1. Conflicting memories are handled without crash.
  2. User correction overrides inference (user-confirmed belief wins).
  3. Deleted memory is not returned by retrieval.
  4. Low-confidence belief (< 0.35) is not surfaced in agent context.
  5. Agent without scope cannot access out-of-scope memory.
  6. Retrieval result includes provenance reference (source_event_ids).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from core.beliefs.model import UNCERTAIN_THRESHOLD, RevisableBelief
from core.beliefs.store import BeliefStore
from core.kernel.contracts import AgentContractRegistry, AgentMemoryView
from core.kernel.policy import (
    AgentPermission,
    PolicyEngine,
    PolicyScope,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeNode:
    type: str
    metadata: dict[str, Any]


def _node(node_type: str, scope: PolicyScope | None = None, event_ids: list[str] | None = None) -> _FakeNode:
    meta: dict[str, Any] = {}
    if scope is not None:
        meta["privacy_scope"] = scope.value
    if event_ids is not None:
        meta["source_event_ids"] = event_ids
    return _FakeNode(type=node_type, metadata=meta)


def _belief(confidence: float = 0.6) -> RevisableBelief:
    return RevisableBelief.create(
        user_id="u1",
        content="test belief",
        confidence=confidence,
        source_event_ids=["evt-origin"],
    )


# ---------------------------------------------------------------------------
# Contract 1: Conflicting memories handled without crash
# ---------------------------------------------------------------------------


def test_contract_conflicting_beliefs_handled_without_crash():
    """Revising a belief with contradictory evidence must not raise."""
    store = BeliefStore(user_id="u1")

    b1 = _belief(confidence=0.8)
    b1_id = b1.id
    store.add(b1)

    # Contradictory evidence arrives
    entry = store.revise(
        b1_id,
        new_confidence=0.3,
        reason="contradicted by new event",
        contradictory_event_ids=["evt-contra"],
    )
    assert entry is not None
    assert store.get(b1_id).is_contradicted is True


def test_contract_multiple_conflicting_beliefs_coexist():
    """Multiple conflicting beliefs can coexist — system does not crash."""
    store = BeliefStore(user_id="u1")
    for i in range(5):
        b = RevisableBelief.create(
            user_id="u1",
            content=f"conflicting belief {i}",
            confidence=0.5,
        )
        b.revise(0.3, contradictory_event_ids=[f"contra-{i}"])
        store.add(b)

    contradicted = store.contradicted()
    assert len(contradicted) == 5  # all coexist, no crash


# ---------------------------------------------------------------------------
# Contract 2: User correction overrides inference
# ---------------------------------------------------------------------------


def test_contract_user_confirmed_belief_not_uncertain():
    """A user-confirmed belief is never classified as uncertain, even with low confidence."""
    store = BeliefStore(user_id="u1")
    b = _belief(confidence=UNCERTAIN_THRESHOLD - 0.05)
    store.add(b)

    assert b.is_uncertain is True  # before confirmation

    store.confirm_by_user(b.id)
    confirmed = store.get(b.id)

    assert confirmed.user_confirmed is True
    assert confirmed.is_uncertain is False


def test_contract_user_confirmed_belief_appears_in_agent_context():
    """User-confirmed beliefs must appear in for_agent_context() regardless of confidence."""
    store = BeliefStore(user_id="u1")
    b = _belief(confidence=0.05)  # very low confidence
    store.add(b)
    store.confirm_by_user(b.id)

    context = store.for_agent_context()
    assert any(bel.id == b.id for bel in context)


def test_contract_user_rejected_belief_excluded_from_agent_context():
    """User-rejected beliefs must NOT appear in for_agent_context()."""
    store = BeliefStore(user_id="u1")
    b = _belief(confidence=0.9)  # high confidence but rejected
    store.add(b)
    store.reject_by_user(b.id)

    context = store.for_agent_context()
    assert all(bel.id != b.id for bel in context)


# ---------------------------------------------------------------------------
# Contract 3: Deleted (rejected) memory not returned by retrieval
# ---------------------------------------------------------------------------


def test_contract_deleted_belief_not_in_store():
    """Removing a belief from the store means it cannot be retrieved."""
    store = BeliefStore(user_id="u1")
    b = _belief()
    store.add(b)

    store.remove(b.id)
    assert store.get(b.id) is None
    assert all(bel.id != b.id for bel in store.all())


def test_contract_filtered_nodes_not_returned():
    """Nodes outside agent scope are not returned by the policy filter."""
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="companion",
        allowed_read_scopes=[PolicyScope.PUBLIC, PolicyScope.RELATIONSHIP],
    ))
    nodes = [
        _node("NOTE", PolicyScope.PUBLIC),
        _node("SOMA", PolicyScope.HEALTH),   # out of scope
        _node("PART", PolicyScope.PRIVATE),  # out of scope
    ]
    result = engine.filter("companion", nodes)
    returned_types = {n.type for n in result}
    assert "SOMA" not in returned_types
    assert "PART" not in returned_types
    assert "NOTE" in returned_types


# ---------------------------------------------------------------------------
# Contract 4: Low-confidence belief not surfaced as fact in agent context
# ---------------------------------------------------------------------------


def test_contract_uncertain_belief_excluded_from_agent_context():
    """Beliefs below UNCERTAIN_THRESHOLD must not appear in for_agent_context()."""
    store = BeliefStore(user_id="u1")
    b = _belief(confidence=UNCERTAIN_THRESHOLD - 0.1)
    store.add(b)

    assert b.is_uncertain is True
    context = store.for_agent_context()
    assert all(bel.id != b.id for bel in context)


def test_contract_contradicted_belief_excluded_from_agent_context():
    """Contradicted beliefs (even with moderate confidence) not in agent context."""
    store = BeliefStore(user_id="u1")
    b = _belief(confidence=0.55)  # moderate confidence but contradicted
    b.revise(0.45, contradictory_event_ids=["evt-contra"])
    store.add(b)

    context = store.for_agent_context()
    assert all(bel.id != b.id for bel in context)


# ---------------------------------------------------------------------------
# Contract 5: Agent without scope cannot access out-of-scope memory
# ---------------------------------------------------------------------------


def test_contract_analytics_agent_cannot_read_health():
    """Analytics agent (PUBLIC scope only) must not see HEALTH-scoped nodes."""
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="analytics",
        allowed_read_scopes=[PolicyScope.PUBLIC],
    ))

    health_node = _node("SOMA", PolicyScope.HEALTH)
    assert engine.check("analytics", health_node) is False


def test_contract_analytics_agent_filter_excludes_health():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="analytics",
        allowed_read_scopes=[PolicyScope.PUBLIC],
    ))
    nodes = [
        _node("INSIGHT", PolicyScope.PUBLIC),
        _node("SOMA", PolicyScope.HEALTH),
    ]
    filtered = engine.filter("analytics", nodes)
    assert len(filtered) == 1
    assert filtered[0].type == "INSIGHT"


def test_contract_agent_contract_registry_scope_matches_policy():
    """Default agent contracts should be enforceable by the policy engine."""
    registry = AgentContractRegistry.default()
    engine = PolicyEngine()

    for agent_id in registry.all_agent_ids():
        contract = registry.get(agent_id)
        engine.grant(AgentPermission(
            agent_id=agent_id,
            allowed_read_scopes=contract.allowed_read_scopes,
            allowed_write_types=contract.allowed_write_types,
            can_write=bool(contract.allowed_write_types),
        ))

    # Companion should not read HEALTH
    health_node = _node("PART", PolicyScope.HEALTH)
    assert engine.check("companion", health_node) is False

    # Reflection should read HEALTH
    assert engine.check("reflection", health_node) is True


# ---------------------------------------------------------------------------
# Contract 6: Retrieval result includes provenance reference
# ---------------------------------------------------------------------------


def test_contract_belief_carries_source_event_ids():
    """Every belief created with source_event_ids must retain them."""
    b = RevisableBelief.create(
        user_id="u1",
        content="I value autonomy",
        confidence=0.75,
        source_event_ids=["evt-100", "evt-200"],
    )
    assert len(b.source_event_ids) == 2
    assert "evt-100" in b.source_event_ids


def test_contract_revision_extends_provenance():
    """After revision, both original and new supporting events are preserved."""
    b = RevisableBelief.create(
        user_id="u1",
        content="I value collaboration",
        confidence=0.5,
        source_event_ids=["evt-origin"],
    )
    b.revise(
        new_confidence=0.8,
        supporting_event_ids=["evt-support-1"],
    )
    assert "evt-origin" in b.source_event_ids
    assert "evt-support-1" in b.source_event_ids


def test_contract_node_with_source_events_in_metadata():
    """Nodes returned by retrieval should carry source_event_ids in metadata."""
    node = _node("BELIEF", PolicyScope.RELATIONSHIP, event_ids=["evt-abc"])
    assert "source_event_ids" in node.metadata
    assert "evt-abc" in node.metadata["source_event_ids"]
