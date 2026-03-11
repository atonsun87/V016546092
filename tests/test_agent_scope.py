"""Tests for AgentScope — scoped agent memory access (core/agent/schema.py)."""

from __future__ import annotations

from core.agent.schema import AgentScope, _PROTECTED_NODE_TYPES


# ---------------------------------------------------------------------------
# check_access — basic allowlist behaviour
# ---------------------------------------------------------------------------


def test_scope_allows_permitted_type():
    scope = AgentScope(
        agent_id="planner-v1",
        allowed_node_types=frozenset({"NOTE", "TASK"}),
    )
    assert scope.check_access("NOTE") is True
    assert scope.check_access("TASK") is True


def test_scope_blocks_non_permitted_type():
    scope = AgentScope(
        agent_id="planner-v1",
        allowed_node_types=frozenset({"NOTE"}),
    )
    assert scope.check_access("TASK") is False
    assert scope.check_access("EMOTION") is False


def test_scope_always_blocks_protected_types():
    """PERSON, VALUE, BELIEF must be blocked regardless of allowed_node_types."""
    scope = AgentScope(
        agent_id="greedy-agent",
        # Attempt to grant access to protected types
        allowed_node_types=frozenset(_PROTECTED_NODE_TYPES | {"NOTE"}),
    )
    for protected in _PROTECTED_NODE_TYPES:
        assert scope.check_access(protected) is False, (
            f"Protected node type '{protected}' should always be blocked"
        )


def test_scope_domain_restriction():
    """When allowed_domains is non-empty, domain must match."""
    scope = AgentScope(
        agent_id="work-agent",
        allowed_node_types=frozenset({"NOTE", "TASK"}),
        allowed_domains=frozenset({"work"}),
    )
    assert scope.check_access("NOTE", domain="work") is True
    assert scope.check_access("NOTE", domain="health") is False
    assert scope.check_access("NOTE", domain="") is False


def test_scope_no_domain_restriction_allows_any_domain():
    """When allowed_domains is empty, any domain is permitted."""
    scope = AgentScope(
        agent_id="broad-agent",
        allowed_node_types=frozenset({"NOTE"}),
        allowed_domains=frozenset(),  # unrestricted
    )
    assert scope.check_access("NOTE", domain="work") is True
    assert scope.check_access("NOTE", domain="health") is True
    assert scope.check_access("NOTE", domain="") is True


def test_scope_confidence_floor():
    """Candidates below min_confidence are blocked."""
    scope = AgentScope(
        agent_id="strict-agent",
        allowed_node_types=frozenset({"NOTE"}),
        min_confidence=0.7,
    )
    assert scope.check_access("NOTE", confidence=0.9) is True
    assert scope.check_access("NOTE", confidence=0.7) is True  # equal → allowed
    assert scope.check_access("NOTE", confidence=0.5) is False


# ---------------------------------------------------------------------------
# filter_candidates
# ---------------------------------------------------------------------------


def _make_candidate(**kwargs) -> dict:
    defaults = {
        "memory_type": "NOTE",
        "domain": "work",
        "confidence": 1.0,
        "content": "some content",
    }
    defaults.update(kwargs)
    return defaults


def test_filter_candidates_basic():
    """filter_candidates removes disallowed node types."""
    scope = AgentScope(
        agent_id="task-agent",
        allowed_node_types=frozenset({"TASK"}),
    )
    candidates = [
        _make_candidate(memory_type="TASK"),
        _make_candidate(memory_type="NOTE"),
        _make_candidate(memory_type="BELIEF"),
    ]
    filtered = scope.filter_candidates(candidates)
    assert len(filtered) == 1
    assert filtered[0]["memory_type"] == "TASK"


def test_filter_candidates_removes_low_confidence():
    scope = AgentScope(
        agent_id="a",
        allowed_node_types=frozenset({"NOTE"}),
        min_confidence=0.6,
    )
    candidates = [
        _make_candidate(confidence=0.9),
        _make_candidate(confidence=0.4),
        _make_candidate(confidence=0.6),
    ]
    filtered = scope.filter_candidates(candidates)
    assert len(filtered) == 2
    assert all(c["confidence"] >= 0.6 for c in filtered)


def test_filter_candidates_excludes_emotion_by_default():
    """Emotion nodes are excluded unless allow_emotional_content=True."""
    scope_default = AgentScope(
        agent_id="a",
        allowed_node_types=frozenset({"NOTE", "EMOTION"}),
        allow_emotional_content=False,
    )
    scope_with_emotion = AgentScope(
        agent_id="b",
        allowed_node_types=frozenset({"NOTE", "EMOTION"}),
        allow_emotional_content=True,
    )
    candidates = [
        _make_candidate(memory_type="NOTE"),
        _make_candidate(memory_type="EMOTION"),
    ]

    filtered_default = scope_default.filter_candidates(candidates)
    filtered_emotion = scope_with_emotion.filter_candidates(candidates)

    assert len(filtered_default) == 1
    assert filtered_default[0]["memory_type"] == "NOTE"

    assert len(filtered_emotion) == 2


def test_filter_candidates_domain_scope():
    """filter_candidates enforces domain restriction."""
    scope = AgentScope(
        agent_id="health-agent",
        allowed_node_types=frozenset({"NOTE"}),
        allowed_domains=frozenset({"health"}),
    )
    candidates = [
        _make_candidate(domain="health"),
        _make_candidate(domain="work"),
    ]
    filtered = scope.filter_candidates(candidates)
    assert len(filtered) == 1
    assert filtered[0]["domain"] == "health"


def test_filter_candidates_empty_list():
    """filter_candidates handles an empty candidate list."""
    scope = AgentScope(agent_id="a", allowed_node_types=frozenset({"NOTE"}))
    assert scope.filter_candidates([]) == []


def test_filter_candidates_all_blocked():
    """filter_candidates returns empty list when all candidates are blocked."""
    scope = AgentScope(
        agent_id="a",
        allowed_node_types=frozenset({"NOTE"}),
    )
    candidates = [
        _make_candidate(memory_type="BELIEF"),
        _make_candidate(memory_type="VALUE"),
        _make_candidate(memory_type="PERSON"),
    ]
    assert scope.filter_candidates(candidates) == []
