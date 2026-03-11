"""Tests for core.kernel.policy — PolicyEngine."""

from __future__ import annotations

import pytest
from dataclasses import dataclass
from typing import Any

from core.kernel.policy import (
    DEFAULT_NODE_SCOPES,
    AgentPermission,
    PolicyEngine,
    PolicyScope,
    PolicyViolationError,
)


# ---------------------------------------------------------------------------
# Minimal NodeLike implementation for testing
# ---------------------------------------------------------------------------


@dataclass
class _FakeNode:
    type: str
    metadata: dict[str, Any]


def _node(node_type: str, scope: PolicyScope | None = None) -> _FakeNode:
    meta: dict[str, Any] = {}
    if scope is not None:
        meta["privacy_scope"] = scope.value
    return _FakeNode(type=node_type, metadata=meta)


# ---------------------------------------------------------------------------
# Default scope assignment
# ---------------------------------------------------------------------------


def test_default_scope_note_is_public():
    assert DEFAULT_NODE_SCOPES["NOTE"] == PolicyScope.PUBLIC


def test_default_scope_part_is_health():
    assert DEFAULT_NODE_SCOPES["PART"] == PolicyScope.HEALTH


def test_default_scope_person_is_private():
    assert DEFAULT_NODE_SCOPES["PERSON"] == PolicyScope.PRIVATE


# ---------------------------------------------------------------------------
# System agent bypass
# ---------------------------------------------------------------------------


def test_system_agent_can_read_any_node():
    engine = PolicyEngine()
    node = _node("SOMA", PolicyScope.PRIVATE)
    assert engine.check(PolicyEngine.SYSTEM_AGENT_ID, node) is True


def test_system_agent_can_write_any_type():
    engine = PolicyEngine()
    assert engine.can_write(PolicyEngine.SYSTEM_AGENT_ID, "BELIEF") is True


# ---------------------------------------------------------------------------
# Unregistered agent is denied
# ---------------------------------------------------------------------------


def test_unregistered_agent_cannot_read():
    engine = PolicyEngine()
    node = _node("NOTE", PolicyScope.PUBLIC)
    assert engine.check("ghost_agent", node) is False


def test_unregistered_agent_filter_returns_empty():
    engine = PolicyEngine()
    nodes = [_node("NOTE", PolicyScope.PUBLIC)]
    assert engine.filter("ghost_agent", nodes) == []


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------


def test_agent_with_public_scope_cannot_read_health():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="planner",
        allowed_read_scopes=[PolicyScope.PUBLIC],
    ))
    node = _node("PART", PolicyScope.HEALTH)
    assert engine.check("planner", node) is False


def test_agent_with_health_scope_can_read_health():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="reflection",
        allowed_read_scopes=[PolicyScope.PUBLIC, PolicyScope.RELATIONSHIP, PolicyScope.HEALTH],
    ))
    node = _node("PART", PolicyScope.HEALTH)
    assert engine.check("reflection", node) is True


def test_agent_with_public_scope_can_read_public():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="companion",
        allowed_read_scopes=[PolicyScope.PUBLIC, PolicyScope.RELATIONSHIP],
    ))
    node = _node("NOTE")  # default: PUBLIC
    assert engine.check("companion", node) is True


# ---------------------------------------------------------------------------
# filter()
# ---------------------------------------------------------------------------


def test_filter_removes_out_of_scope_nodes():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="analytics",
        allowed_read_scopes=[PolicyScope.PUBLIC],
    ))
    nodes = [
        _node("NOTE", PolicyScope.PUBLIC),     # allowed
        _node("SOMA", PolicyScope.HEALTH),     # denied
        _node("INSIGHT", PolicyScope.PUBLIC),  # allowed
    ]
    result = engine.filter("analytics", nodes)
    assert len(result) == 2
    assert all(n.type in ("NOTE", "INSIGHT") for n in result)


def test_filter_respects_max_nodes_per_request():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="small_agent",
        allowed_read_scopes=[PolicyScope.PUBLIC],
        max_nodes_per_request=2,
    ))
    nodes = [_node("NOTE", PolicyScope.PUBLIC) for _ in range(10)]
    result = engine.filter("small_agent", nodes)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Write policy
# ---------------------------------------------------------------------------


def test_agent_without_can_write_cannot_write():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="readonly_agent",
        allowed_read_scopes=[PolicyScope.PUBLIC],
        allowed_write_types=["NOTE"],
        can_write=False,
    ))
    assert engine.can_write("readonly_agent", "NOTE") is False


def test_agent_with_write_can_write_allowed_type():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="writer",
        allowed_read_scopes=[PolicyScope.PUBLIC],
        allowed_write_types=["NOTE", "TASK"],
        can_write=True,
    ))
    assert engine.can_write("writer", "NOTE") is True
    assert engine.can_write("writer", "BELIEF") is False


def test_assert_can_write_raises_on_denial():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="companion",
        allowed_read_scopes=[PolicyScope.PUBLIC],
        allowed_write_types=["NOTE"],
        can_write=True,
    ))
    with pytest.raises(PolicyViolationError):
        engine.assert_can_write("companion", "BELIEF")


# ---------------------------------------------------------------------------
# Grant / revoke
# ---------------------------------------------------------------------------


def test_revoke_removes_agent_permission():
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="temp_agent",
        allowed_read_scopes=[PolicyScope.PUBLIC],
    ))
    engine.revoke("temp_agent")
    node = _node("NOTE", PolicyScope.PUBLIC)
    assert engine.check("temp_agent", node) is False


def test_cannot_revoke_system_agent():
    engine = PolicyEngine()
    with pytest.raises(ValueError):
        engine.revoke(PolicyEngine.SYSTEM_AGENT_ID)


def test_override_scope_in_metadata():
    """A node with explicit privacy_scope in metadata overrides the default."""
    engine = PolicyEngine()
    engine.grant(AgentPermission(
        agent_id="companion",
        allowed_read_scopes=[PolicyScope.PUBLIC, PolicyScope.RELATIONSHIP],
    ))
    # NOTE is normally PUBLIC, but user has marked it PRIVATE
    node = _node("NOTE", PolicyScope.PRIVATE)
    assert engine.check("companion", node) is False
