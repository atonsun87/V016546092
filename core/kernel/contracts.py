"""Agent contracts and memory view definitions for SELF-OS.

This module defines the formal interface between the kernel and agents.

Key types:

- :class:`AgentContract` — what an agent is allowed to read/write/decide.
- :class:`AgentMemoryView` — pre-filtered snapshot of memory for one agent.
- :class:`BeliefSummary` — confidence-level summary (not raw belief text).
- :class:`OnlineCaptureResult` — result of the fast online capture path.
- :class:`OfflineConsolidationJob` — work item for the background worker.
- :class:`AgentContractRegistry` — singleton registry of built-in contracts.
- :exc:`AgentContractViolation` — raised when a contract is breached.

Architecture rule: this module imports ONLY from ``core.kernel.policy``
(for ``PolicyScope``) and the standard library.  No domain imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from core.kernel.policy import PolicyScope

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AgentContractViolation(Exception):
    """Raised when an agent violates its declared contract."""

    def __init__(self, agent_id: str, reason: str) -> None:
        super().__init__(f"Agent '{agent_id}' violated its contract: {reason}")
        self.agent_id = agent_id
        self.reason = reason


# ---------------------------------------------------------------------------
# AgentContract
# ---------------------------------------------------------------------------


@dataclass
class AgentContract:
    """Formal declaration of what an agent is allowed to do.

    Attributes
    ----------
    agent_id:
        Stable unique identifier for this agent role.
    agent_role:
        Human-readable role name (e.g. "Companion / Coach").
    allowed_read_scopes:
        Memory scopes this agent may read.
    allowed_write_types:
        Node types this agent may create or update.
    max_nodes_per_request:
        Hard cap on nodes returned per retrieval call.
    can_proactively_act:
        Whether the agent may initiate actions without a user message.
    can_revise_beliefs:
        Whether the agent may propose belief revisions.
    requires_user_consent_for:
        Action type strings that require explicit user confirmation.
    """

    agent_id: str
    agent_role: str
    allowed_read_scopes: list[PolicyScope] = field(default_factory=list)
    allowed_write_types: list[str] = field(default_factory=list)
    max_nodes_per_request: int = 20
    can_proactively_act: bool = False
    can_revise_beliefs: bool = False
    requires_user_consent_for: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# BeliefSummary (for agent views — confidence + status only, not raw text)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeliefSummary:
    """Condensed belief representation safe for agent consumption.

    Agents see confidence level and confirmation status, but NOT the raw
    belief text unless they are specifically scoped for it.
    """

    belief_id: str
    confidence: float
    user_confirmed: bool | None
    status: str  # "strong" | "uncertain" | "contradicted" | "user_confirmed"
    topic_hint: str = ""  # e.g. "values/autonomy" — non-identifying topic label


# ---------------------------------------------------------------------------
# AgentMemoryView
# ---------------------------------------------------------------------------


@dataclass
class AgentMemoryView:
    """Pre-filtered, scoped memory snapshot delivered to an agent.

    Agents receive this object instead of direct storage access.

    Attributes
    ----------
    agent_id:
        The agent this view was built for.
    scope:
        The effective policy scope used to build this view.
    nodes:
        Memory nodes the agent is allowed to see (filtered + ranked).
    goals:
        Active goal title strings (public summary only).
    emotional_tone:
        Single-label emotional tone summary (e.g. "calm", "anxious").
    retrieval_context:
        Human-readable explanation of why these memories were selected.
    belief_summaries:
        Confidence-level summaries for beliefs in scope.
    built_at:
        ISO timestamp when the view was assembled.
    node_count_filtered:
        How many nodes were excluded by policy (for audit/transparency).
    """

    agent_id: str
    scope: PolicyScope
    nodes: list[Any] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    emotional_tone: str = "neutral"
    retrieval_context: str = ""
    belief_summaries: list[BeliefSummary] = field(default_factory=list)
    built_at: str = field(default_factory=_utc_now)
    node_count_filtered: int = 0


# ---------------------------------------------------------------------------
# Online capture / offline consolidation job contracts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OnlineCaptureResult:
    """Output of the fast online capture path (< 200 ms, no LLM extraction).

    Attributes
    ----------
    event_id:
        Immutable event log ID for this capture.
    intent_class:
        Classified intent label.
    reply:
        Live reply text sent to the user.
    job_id:
        Offline consolidation job scheduled for this capture.
    latency_ms:
        Measured end-to-end latency of the online path.
    """

    event_id: str
    intent_class: str
    reply: str
    job_id: str
    latency_ms: float = 0.0


@dataclass
class OfflineConsolidationJob:
    """Work item consumed by the background consolidation worker.

    Attributes
    ----------
    job_id:
        Unique job identifier (matches ``OnlineCaptureResult.job_id``).
    user_id:
        User whose memory is being consolidated.
    event_id:
        Source event in the immutable event log.
    raw_text:
        Original message text (used for LLM extraction).
    intent_class:
        Already-classified intent (avoids re-classification offline).
    priority:
        0 = immediate, 1 = normal, 2 = background-only.
    scheduled_at:
        ISO timestamp when the job should be processed.
    status:
        Job lifecycle: ``pending | running | completed | failed``.
    attempts:
        Number of processing attempts (for retry logic).
    """

    user_id: str
    event_id: str
    raw_text: str
    intent_class: str
    job_id: str = field(default_factory=_new_id)
    priority: int = 1
    scheduled_at: str = field(default_factory=_utc_now)
    status: str = "pending"
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "event_id": self.event_id,
            "raw_text": self.raw_text,
            "intent_class": self.intent_class,
            "priority": self.priority,
            "scheduled_at": self.scheduled_at,
            "status": self.status,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OfflineConsolidationJob:
        return cls(
            job_id=data["job_id"],
            user_id=data["user_id"],
            event_id=data["event_id"],
            raw_text=data["raw_text"],
            intent_class=data["intent_class"],
            priority=data.get("priority", 1),
            scheduled_at=data.get("scheduled_at", _utc_now()),
            status=data.get("status", "pending"),
            attempts=data.get("attempts", 0),
        )


# ---------------------------------------------------------------------------
# AgentContractRegistry
# ---------------------------------------------------------------------------


class AgentContractRegistry:
    """Registry of agent contracts.

    Built-in agent contracts are pre-registered.  External agents register
    via :meth:`register`.

    Usage::

        registry = AgentContractRegistry.default()
        contract = registry.get("companion")
    """

    def __init__(self) -> None:
        self._contracts: dict[str, AgentContract] = {}

    def register(self, contract: AgentContract) -> None:
        """Register or replace a contract."""
        self._contracts[contract.agent_id] = contract

    def get(self, agent_id: str) -> AgentContract | None:
        """Return the contract for *agent_id*, or None if not registered."""
        return self._contracts.get(agent_id)

    def assert_registered(self, agent_id: str) -> AgentContract:
        """Return the contract or raise :exc:`AgentContractViolation`."""
        contract = self.get(agent_id)
        if contract is None:
            raise AgentContractViolation(agent_id, "agent not registered in contract registry")
        return contract

    def all_agent_ids(self) -> list[str]:
        return list(self._contracts.keys())

    @classmethod
    def default(cls) -> AgentContractRegistry:
        """Return a registry pre-populated with built-in agent contracts."""
        registry = cls()

        registry.register(AgentContract(
            agent_id="companion",
            agent_role="Companion / Coach",
            allowed_read_scopes=[PolicyScope.PUBLIC, PolicyScope.RELATIONSHIP],
            allowed_write_types=["NOTE", "EMOTION", "THOUGHT"],
            max_nodes_per_request=20,
            can_proactively_act=True,
            can_revise_beliefs=False,
            requires_user_consent_for=["send_notification", "update_goal"],
        ))

        registry.register(AgentContract(
            agent_id="planner",
            agent_role="Planner / Productivity",
            allowed_read_scopes=[PolicyScope.PUBLIC, PolicyScope.PROFESSIONAL],
            allowed_write_types=["TASK", "PROJECT", "NOTE"],
            max_nodes_per_request=30,
            can_proactively_act=True,
            can_revise_beliefs=False,
            requires_user_consent_for=["create_project", "delete_task"],
        ))

        registry.register(AgentContract(
            agent_id="reflection",
            agent_role="Reflection / Inner Work",
            allowed_read_scopes=[
                PolicyScope.PUBLIC,
                PolicyScope.RELATIONSHIP,
                PolicyScope.HEALTH,
            ],
            allowed_write_types=["BELIEF", "THOUGHT", "INSIGHT", "PART", "NEED"],
            max_nodes_per_request=15,
            can_proactively_act=False,
            can_revise_beliefs=True,
            requires_user_consent_for=["revise_belief", "archive_part"],
        ))

        registry.register(AgentContract(
            agent_id="analytics",
            agent_role="Analytics / Insights",
            allowed_read_scopes=[PolicyScope.PUBLIC],
            allowed_write_types=["INSIGHT"],
            max_nodes_per_request=100,
            can_proactively_act=False,
            can_revise_beliefs=False,
            requires_user_consent_for=["share_report", "export_data"],
        ))

        # System agent: unrestricted (internal use only, never marketplace-facing)
        registry.register(AgentContract(
            agent_id="system",
            agent_role="Internal System",
            allowed_read_scopes=list(PolicyScope),
            allowed_write_types=[
                "NOTE", "TASK", "PROJECT", "BELIEF", "VALUE", "NEED",
                "EMOTION", "PART", "SOMA", "THOUGHT", "INSIGHT", "EVENT", "PERSON",
            ],
            max_nodes_per_request=10_000,
            can_proactively_act=True,
            can_revise_beliefs=True,
            requires_user_consent_for=[],
        ))

        return registry
