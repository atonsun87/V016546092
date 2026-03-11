"""Tests for core.kernel.event_types — typed event registry."""

from __future__ import annotations

from core.kernel.event_types import (
    KernelEventType,
    KERNEL_EVENT_REGISTRY,
    UserMessageReceived,
    OfflineJobScheduled,
    BeliefRevised,
    PolicyViolationDetected,
    get_payload_type,
    registered_event_types,
)


def test_all_kernel_event_types_have_registered_payloads():
    """Every KernelEventType that appears in KERNEL_EVENT_REGISTRY maps to a class."""
    for event_type, payload_class in KERNEL_EVENT_REGISTRY.items():
        assert payload_class is not None
        assert isinstance(payload_class, type), f"{event_type} has non-type payload class"


def test_registered_event_types_returns_all_values():
    names = registered_event_types()
    assert len(names) > 0
    assert KernelEventType.USER_MESSAGE_RECEIVED.value in names
    assert KernelEventType.BELIEF_REVISED.value in names


def test_get_payload_type_known_event():
    cls = get_payload_type(KernelEventType.USER_MESSAGE_RECEIVED.value)
    assert cls is UserMessageReceived


def test_get_payload_type_unknown_event():
    cls = get_payload_type("unknown.event.type")
    assert cls is None


def test_user_message_received_defaults():
    msg = UserMessageReceived(user_id="u1", raw_text="hello")
    assert msg.user_id == "u1"
    assert msg.raw_text == "hello"
    assert msg.source == "telegram"
    assert msg.event_id != ""
    assert msg.timestamp != ""


def test_offline_job_scheduled_defaults():
    job = OfflineJobScheduled(
        user_id="u1",
        event_id="e1",
        raw_text="some text",
        intent_class="REFLECTION",
    )
    assert job.job_id != ""
    assert job.priority == 1


def test_belief_revised_carries_events():
    entry = BeliefRevised(
        user_id="u1",
        belief_id="b1",
        old_confidence=0.7,
        new_confidence=0.3,
        reason="Contradicted by user",
        contradictory_event_ids=["e2", "e3"],
    )
    assert entry.old_confidence == 0.7
    assert entry.new_confidence == 0.3
    assert "e2" in entry.contradictory_event_ids


def test_policy_violation_detected_fields():
    v = PolicyViolationDetected(
        agent_id="analytics",
        attempted_scope="HEALTH",
        node_type="SOMA",
        reason="scope_not_allowed",
    )
    assert v.agent_id == "analytics"
    assert v.attempted_scope == "HEALTH"
