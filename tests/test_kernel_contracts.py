"""Tests for core.kernel.contracts — AgentContract, AgentContractRegistry, etc."""

from __future__ import annotations

import pytest

from core.kernel.contracts import (
    AgentContract,
    AgentContractRegistry,
    AgentContractViolation,
    AgentMemoryView,
    BeliefSummary,
    OfflineConsolidationJob,
    OnlineCaptureResult,
)
from core.kernel.policy import PolicyScope


# ---------------------------------------------------------------------------
# AgentContractRegistry
# ---------------------------------------------------------------------------


def test_default_registry_has_built_in_agents():
    registry = AgentContractRegistry.default()
    for agent_id in ("companion", "planner", "reflection", "analytics", "system"):
        assert registry.get(agent_id) is not None, f"Missing built-in agent: {agent_id}"


def test_companion_contract_cannot_read_health():
    registry = AgentContractRegistry.default()
    contract = registry.get("companion")
    assert PolicyScope.HEALTH not in contract.allowed_read_scopes


def test_reflection_contract_can_read_health():
    registry = AgentContractRegistry.default()
    contract = registry.get("reflection")
    assert PolicyScope.HEALTH in contract.allowed_read_scopes


def test_analytics_cannot_write_beliefs():
    registry = AgentContractRegistry.default()
    contract = registry.get("analytics")
    assert "BELIEF" not in contract.allowed_write_types


def test_reflection_can_revise_beliefs():
    registry = AgentContractRegistry.default()
    contract = registry.get("reflection")
    assert contract.can_revise_beliefs is True


def test_companion_cannot_revise_beliefs():
    registry = AgentContractRegistry.default()
    contract = registry.get("companion")
    assert contract.can_revise_beliefs is False


def test_assert_registered_raises_for_unknown_agent():
    registry = AgentContractRegistry.default()
    with pytest.raises(AgentContractViolation):
        registry.assert_registered("unknown_agent_xyz")


def test_register_and_get_custom_contract():
    registry = AgentContractRegistry.default()
    custom = AgentContract(
        agent_id="custom_agent",
        agent_role="Custom",
        allowed_read_scopes=[PolicyScope.PUBLIC],
    )
    registry.register(custom)
    assert registry.get("custom_agent") is custom


def test_all_agent_ids_includes_built_ins():
    registry = AgentContractRegistry.default()
    ids = registry.all_agent_ids()
    assert "companion" in ids
    assert "system" in ids


# ---------------------------------------------------------------------------
# AgentMemoryView
# ---------------------------------------------------------------------------


def test_agent_memory_view_defaults():
    view = AgentMemoryView(agent_id="companion", scope=PolicyScope.PUBLIC)
    assert view.nodes == []
    assert view.goals == []
    assert view.emotional_tone == "neutral"
    assert view.node_count_filtered == 0
    assert view.built_at != ""


def test_agent_memory_view_with_belief_summaries():
    summary = BeliefSummary(
        belief_id="b1",
        confidence=0.8,
        user_confirmed=True,
        status="user_confirmed",
        topic_hint="values/autonomy",
    )
    view = AgentMemoryView(
        agent_id="reflection",
        scope=PolicyScope.HEALTH,
        belief_summaries=[summary],
    )
    assert len(view.belief_summaries) == 1
    assert view.belief_summaries[0].confidence == 0.8


# ---------------------------------------------------------------------------
# OnlineCaptureResult
# ---------------------------------------------------------------------------


def test_online_capture_result_fields():
    result = OnlineCaptureResult(
        event_id="evt-1",
        intent_class="REFLECTION",
        reply="I hear you.",
        job_id="job-1",
        latency_ms=145.5,
    )
    assert result.event_id == "evt-1"
    assert result.intent_class == "REFLECTION"
    assert result.latency_ms == 145.5


# ---------------------------------------------------------------------------
# OfflineConsolidationJob
# ---------------------------------------------------------------------------


def test_offline_job_roundtrip():
    job = OfflineConsolidationJob(
        user_id="u1",
        event_id="e1",
        raw_text="I felt anxious today",
        intent_class="FEELING_REPORT",
        priority=0,
    )
    data = job.to_dict()
    restored = OfflineConsolidationJob.from_dict(data)

    assert restored.job_id == job.job_id
    assert restored.user_id == "u1"
    assert restored.raw_text == "I felt anxious today"
    assert restored.priority == 0
    assert restored.status == "pending"


def test_offline_job_default_status():
    job = OfflineConsolidationJob(
        user_id="u1",
        event_id="e1",
        raw_text="hello",
        intent_class="IDEA",
    )
    assert job.status == "pending"
    assert job.attempts == 0


# ---------------------------------------------------------------------------
# AgentContractViolation
# ---------------------------------------------------------------------------


def test_agent_contract_violation_message():
    exc = AgentContractViolation("analytics", "tried to write BELIEF")
    assert "analytics" in str(exc)
    assert "BELIEF" in str(exc)
