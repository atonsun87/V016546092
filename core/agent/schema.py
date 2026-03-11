"""AgentAction and AgentScope schema for SELF-OS.

An :class:`AgentAction` is a first-class record of a specific action taken
(or planned) by the agent on behalf of the user.  Actions are persisted so
that the system maintains an auditable history of what it did, why it did it,
and what the outcome was.

An :class:`AgentScope` describes the **boundaries within which an agent is
permitted to read user memory**.  It enforces the principle of least-privilege
for the memory marketplace: an agent may only access node types, domains, and
confidence bands that the user explicitly granted.

Actions may be triggered by user messages, scheduled background processes, or
the proactive motivation loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# AgentScope — declarative access policy
# ---------------------------------------------------------------------------

#: Node types that are always off-limits to external agents regardless of scope.
_PROTECTED_NODE_TYPES: frozenset[str] = frozenset({"PERSON", "VALUE", "BELIEF"})


@dataclass
class AgentScope:
    """Declarative access policy for an agent operating on user memory.

    An :class:`AgentScope` is attached to an agent identity and checked before
    any memory retrieval call.  Users grant scopes explicitly; the system
    enforces them on every read.

    Attributes
    ----------
    agent_id:
        Unique identifier for the agent (e.g. ``"planner-v1"`` or a UUID).
    allowed_node_types:
        Set of node type strings the agent may read (e.g. ``{"NOTE", "TASK"}``).
        An empty set means *no node types are allowed*.
        ``PERSON``, ``VALUE``, and ``BELIEF`` nodes are **always** excluded from
        external agents regardless of this field.
    allowed_domains:
        Broad domain labels the agent may access (e.g. ``{"work", "health"}``).
        An empty set means *all domains are allowed* (domain is not restricted).
    min_confidence:
        Minimum ``confidence`` a candidate must have to be surfaced to this
        agent.  Prevents low-confidence speculative nodes from leaking.
    allow_emotional_content:
        When ``False``, nodes tagged with emotion-related labels are excluded.
        Defaults to ``False`` so that emotional memories require explicit opt-in.
    description:
        Human-readable description of what this agent does and why it needs
        the granted access — shown to users during consent flows.
    """

    agent_id: str
    allowed_node_types: frozenset[str] = field(
        default_factory=lambda: frozenset({"NOTE", "TASK", "EVENT"})
    )
    allowed_domains: frozenset[str] = field(default_factory=frozenset)
    min_confidence: float = 0.5
    allow_emotional_content: bool = False
    description: str = ""

    # ------------------------------------------------------------------
    # Access check
    # ------------------------------------------------------------------

    def check_access(self, node_type: str, domain: str = "", confidence: float = 1.0) -> bool:
        """Return ``True`` if this scope permits access to the described node.

        Parameters
        ----------
        node_type:
            The type of the node being accessed (e.g. ``"NOTE"``, ``"BELIEF"``).
        domain:
            The broad domain label of the node (e.g. ``"work"``).  Pass an empty
            string when the node has no domain tag.
        confidence:
            The agent's confidence in the accuracy of this node.

        Returns
        -------
        bool
            ``True`` when all of the following hold:

            1. *node_type* is not in :data:`_PROTECTED_NODE_TYPES`.
            2. *node_type* is in :attr:`allowed_node_types`.
            3. If :attr:`allowed_domains` is non-empty, *domain* must be
               in :attr:`allowed_domains`.
            4. *confidence* ≥ :attr:`min_confidence`.
        """
        # 1. Hard-blocked types
        if node_type in _PROTECTED_NODE_TYPES:
            return False

        # 2. Type allowlist
        if node_type not in self.allowed_node_types:
            return False

        # 3. Domain restriction (empty set = unrestricted)
        if self.allowed_domains and domain not in self.allowed_domains:
            return False

        # 4. Confidence floor
        if confidence < self.min_confidence:
            return False

        return True

    def filter_candidates(
        self,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Filter a list of raw candidate dicts by this scope.

        Each dict must have at minimum a ``"memory_type"`` key; optionally
        ``"domain"`` and ``"confidence"`` keys.

        This is a convenience wrapper around :meth:`check_access` for bulk
        filtering of retrieval results before they are returned to an agent.
        """
        result: list[dict[str, Any]] = []
        for c in candidates:
            node_type = str(c.get("memory_type", c.get("type", "")))
            domain = str(c.get("domain", ""))
            confidence = float(c.get("confidence", 1.0))
            if not self.allow_emotional_content and node_type == "EMOTION":
                continue
            if self.check_access(node_type, domain, confidence):
                result.append(c)
        return result


@dataclass
class AgentAction:
    """A record of an action taken or planned by the agent.

    Attributes
    ----------
    id:
        Unique identifier for this action record.
    user_id:
        The user on whose behalf the action was taken.
    timestamp:
        ISO-8601 timestamp when the action was created.
    action_type:
        Category of the action.  Examples: ``"respond"``, ``"search_memory"``,
        ``"create_task"``, ``"send_notification"``, ``"reflect"``,
        ``"update_goal"``.
    title:
        Short human-readable summary of the action (1 line).
    description:
        Detailed description of what the action does or did.
    status:
        Lifecycle status of the action.  One of:
        ``"planned"`` → ``"in_progress"`` → ``"completed"`` / ``"failed"`` /
        ``"cancelled"``.
    triggered_by:
        What caused this action to be created.  Examples:
        ``"user_message"``, ``"scheduler"``, ``"proactive_loop"``,
        ``"reflection_pipeline"``.
    motivation_refs:
        References to :class:`~core.motivation.schema.MotivationState`
        snapshots (by timestamp or ID) that informed this action.
    memory_refs:
        Graph node IDs of memory nodes that were used as context for this
        action.
    tool_calls:
        List of tool invocation records.  Each entry is a dict with at least
        ``{"tool": str, "args": dict, "result": any}``.
    result:
        The final output or outcome of the action (may be text, a structured
        object, or ``None`` if the action is still in progress).
    explanation:
        Human-readable explanation of why the agent took this action.
    """

    user_id: str
    action_type: str
    title: str

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    description: str = ""
    status: str = "planned"  # planned | in_progress | completed | failed | cancelled
    triggered_by: str = "user_message"  # user_message | scheduler | proactive_loop | reflection_pipeline
    motivation_refs: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "triggered_by": self.triggered_by,
            "motivation_refs": self.motivation_refs,
            "memory_refs": self.memory_refs,
            "tool_calls": self.tool_calls,
            "result": self.result,
            "explanation": self.explanation,
        }

    def mark_in_progress(self) -> None:
        """Transition the action status to ``in_progress``."""
        self.status = "in_progress"

    def mark_completed(self, result: Any = None) -> None:
        """Transition the action status to ``completed`` and record the result."""
        self.status = "completed"
        if result is not None:
            self.result = result

    def mark_failed(self, reason: str = "") -> None:
        """Transition the action status to ``failed``."""
        self.status = "failed"
        if reason:
            self.explanation = reason

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentAction:
        """Deserialise from a plain dict (inverse of :meth:`to_dict`)."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            action_type=data["action_type"],
            title=data["title"],
            timestamp=data.get("timestamp", datetime.now(UTC).isoformat()),
            description=data.get("description", ""),
            status=data.get("status", "planned"),
            triggered_by=data.get("triggered_by", "user_message"),
            motivation_refs=list(data.get("motivation_refs", [])),
            memory_refs=list(data.get("memory_refs", [])),
            tool_calls=list(data.get("tool_calls", [])),
            result=data.get("result"),
            explanation=data.get("explanation", ""),
        )
