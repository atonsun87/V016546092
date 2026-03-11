"""Formal typed event registry for SELF-OS kernel.

Every event published through :class:`~core.pipeline.events.EventBus` must have
a registered name and a typed payload dataclass listed here.  This prevents
ad-hoc string events, makes the event surface auditable, and provides a
compile-time reference for all downstream consumers.

Usage::

    from core.kernel.event_types import KernelEventType, UserMessageReceived

    bus.publish(KernelEventType.USER_MESSAGE_RECEIVED, UserMessageReceived(
        event_id="...", user_id="...", raw_text="hello", ...
    ))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Event name registry
# ---------------------------------------------------------------------------


class KernelEventType(str, Enum):
    """Canonical event names for the SELF-OS event bus.

    Naming convention: ``<domain>.<verb>.<past_tense>``
    """

    # --- online capture path ---
    USER_MESSAGE_RECEIVED = "user.message.received"
    JOURNAL_ENTRY_CREATED = "journal.entry.created"
    INTENT_CLASSIFIED = "intent.classified"

    # --- offline consolidation path ---
    OFFLINE_JOB_SCHEDULED = "offline.job.scheduled"
    OFFLINE_JOB_STARTED = "offline.job.started"
    OFFLINE_JOB_COMPLETED = "offline.job.completed"
    OFFLINE_JOB_FAILED = "offline.job.failed"

    # --- memory lifecycle ---
    MEMORY_EXTRACTED = "memory.extracted"
    MEMORY_CONSOLIDATED = "memory.consolidated"
    MEMORY_ARCHIVED = "memory.archived"
    MEMORY_DELETED = "memory.deleted"
    MEMORY_CORRECTED_BY_USER = "memory.corrected_by_user"

    # --- beliefs ---
    BELIEF_CREATED = "belief.created"
    BELIEF_REVISED = "belief.revised"
    BELIEF_CONFIRMED_BY_USER = "belief.confirmed_by_user"
    BELIEF_CONTRADICTED = "belief.contradicted"

    # --- identity ---
    IDENTITY_PROFILE_UPDATED = "identity.profile.updated"

    # --- goals / motivation ---
    GOAL_STATE_CHANGED = "goal.state.changed"
    MOTIVATION_STATE_UPDATED = "motivation.state.updated"

    # --- agent actions ---
    AGENT_ACTION_TAKEN = "agent.action.taken"
    AGENT_ACTION_COMPLETED = "agent.action.completed"
    AGENT_ACTION_FAILED = "agent.action.failed"

    # --- policy ---
    CONSENT_REQUIRED = "policy.consent.required"
    CONSENT_GRANTED = "policy.consent.granted"
    CONSENT_DENIED = "policy.consent.denied"
    POLICY_VIOLATION_DETECTED = "policy.violation.detected"


# ---------------------------------------------------------------------------
# Payload dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class UserMessageReceived:
    user_id: str
    raw_text: str
    source: str = "telegram"  # telegram | cli | api
    event_id: str = field(default_factory=_new_id)
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class JournalEntryCreated:
    user_id: str
    event_id: str
    raw_text: str
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class IntentClassified:
    user_id: str
    event_id: str
    intent_class: str
    confidence: float = 1.0
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class OfflineJobScheduled:
    user_id: str
    event_id: str
    raw_text: str
    intent_class: str
    job_id: str = field(default_factory=_new_id)
    priority: int = 1
    scheduled_at: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class OfflineJobStarted:
    job_id: str
    user_id: str
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class OfflineJobCompleted:
    job_id: str
    user_id: str
    nodes_created: int = 0
    nodes_updated: int = 0
    beliefs_revised: int = 0
    duration_s: float = 0.0
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class OfflineJobFailed:
    job_id: str
    user_id: str
    reason: str = ""
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class MemoryExtracted:
    user_id: str
    job_id: str
    node_ids: list[str] = field(default_factory=list)
    source_event_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class MemoryConsolidated:
    user_id: str
    nodes_merged: int = 0
    new_nodes_created: int = 0
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class MemoryCorrectedByUser:
    user_id: str
    node_id: str
    old_text: str
    new_text: str
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class BeliefCreated:
    user_id: str
    belief_id: str
    content: str
    confidence: float
    source_event_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class BeliefRevised:
    user_id: str
    belief_id: str
    old_confidence: float
    new_confidence: float
    reason: str = ""
    contradictory_event_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class BeliefConfirmedByUser:
    user_id: str
    belief_id: str
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class AgentActionTaken:
    user_id: str
    agent_id: str
    action_type: str
    action_id: str = field(default_factory=_new_id)
    explanation: str = ""
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class ConsentRequired:
    user_id: str
    agent_id: str
    action_type: str
    description: str
    request_id: str = field(default_factory=_new_id)
    timestamp: str = field(default_factory=_utc_now)


@dataclass(slots=True)
class PolicyViolationDetected:
    agent_id: str
    attempted_scope: str
    node_type: str
    reason: str
    timestamp: str = field(default_factory=_utc_now)


# ---------------------------------------------------------------------------
# Registry: event name → expected payload type
# ---------------------------------------------------------------------------

KERNEL_EVENT_REGISTRY: dict[str, type] = {
    KernelEventType.USER_MESSAGE_RECEIVED: UserMessageReceived,
    KernelEventType.JOURNAL_ENTRY_CREATED: JournalEntryCreated,
    KernelEventType.INTENT_CLASSIFIED: IntentClassified,
    KernelEventType.OFFLINE_JOB_SCHEDULED: OfflineJobScheduled,
    KernelEventType.OFFLINE_JOB_STARTED: OfflineJobStarted,
    KernelEventType.OFFLINE_JOB_COMPLETED: OfflineJobCompleted,
    KernelEventType.OFFLINE_JOB_FAILED: OfflineJobFailed,
    KernelEventType.MEMORY_EXTRACTED: MemoryExtracted,
    KernelEventType.MEMORY_CONSOLIDATED: MemoryConsolidated,
    KernelEventType.MEMORY_CORRECTED_BY_USER: MemoryCorrectedByUser,
    KernelEventType.BELIEF_CREATED: BeliefCreated,
    KernelEventType.BELIEF_REVISED: BeliefRevised,
    KernelEventType.BELIEF_CONFIRMED_BY_USER: BeliefConfirmedByUser,
    KernelEventType.AGENT_ACTION_TAKEN: AgentActionTaken,
    KernelEventType.CONSENT_REQUIRED: ConsentRequired,
    KernelEventType.POLICY_VIOLATION_DETECTED: PolicyViolationDetected,
}


def registered_event_types() -> list[str]:
    """Return all registered event type names."""
    return [e.value for e in KernelEventType]


def get_payload_type(event_name: str) -> type | None:
    """Return the expected payload dataclass for *event_name*, or None if not registered."""
    key = KernelEventType(event_name) if event_name in KernelEventType._value2member_map_ else None
    if key is None:
        return None
    return KERNEL_EVENT_REGISTRY.get(key)
