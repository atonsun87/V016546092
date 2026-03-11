"""Policy engine for SELF-OS memory access control.

The :class:`PolicyEngine` is the single enforcement point for all memory reads
and writes.  Every agent interacts with memory only through a filtered
:class:`AgentMemoryView` (see ``core.kernel.contracts``); the policy engine
determines what goes into that view.

Architecture rule: this module has NO imports from ``core.graph``,
``core.identity``, ``core.motivation``, or any other domain package.  It
operates on plain Python objects and must stay thin.

Scopes are ordered by sensitivity (lowest index = most public)::

    PUBLIC < RELATIONSHIP < HEALTH < PROFESSIONAL < PRIVATE

An agent with ``allowed_read_scopes = [PUBLIC, RELATIONSHIP]`` can read nodes
whose ``privacy_scope`` is exactly ``PUBLIC`` or ``RELATIONSHIP``, and cannot
read HEALTH, PROFESSIONAL, or PRIVATE nodes.  Scope matching is an exact
membership check against ``allowed_read_scopes``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PolicyScope
# ---------------------------------------------------------------------------


class PolicyScope(str, Enum):
    """Privacy scope levels, ordered from least to most sensitive."""

    PUBLIC = "PUBLIC"
    RELATIONSHIP = "RELATIONSHIP"
    HEALTH = "HEALTH"
    PROFESSIONAL = "PROFESSIONAL"
    PRIVATE = "PRIVATE"


# Sensitivity order: lower index = less sensitive (more public).
_SCOPE_ORDER: list[PolicyScope] = [
    PolicyScope.PUBLIC,
    PolicyScope.RELATIONSHIP,
    PolicyScope.HEALTH,
    PolicyScope.PROFESSIONAL,
    PolicyScope.PRIVATE,
]


def _scope_sensitivity(scope: PolicyScope) -> int:
    """Return a numeric sensitivity level (0 = least sensitive)."""
    try:
        return _SCOPE_ORDER.index(scope)
    except ValueError:
        return len(_SCOPE_ORDER)


# ---------------------------------------------------------------------------
# Default scope by node type (used when a node has no explicit privacy_scope)
# ---------------------------------------------------------------------------

DEFAULT_NODE_SCOPES: dict[str, PolicyScope] = {
    "NOTE": PolicyScope.PUBLIC,
    "TASK": PolicyScope.PUBLIC,
    "PROJECT": PolicyScope.PROFESSIONAL,
    "BELIEF": PolicyScope.RELATIONSHIP,
    "VALUE": PolicyScope.PUBLIC,
    "NEED": PolicyScope.RELATIONSHIP,
    "EMOTION": PolicyScope.RELATIONSHIP,
    "PART": PolicyScope.HEALTH,
    "SOMA": PolicyScope.HEALTH,
    "THOUGHT": PolicyScope.RELATIONSHIP,
    "INSIGHT": PolicyScope.PUBLIC,
    "EVENT": PolicyScope.RELATIONSHIP,
    "PERSON": PolicyScope.PRIVATE,
}


# ---------------------------------------------------------------------------
# AgentPermission
# ---------------------------------------------------------------------------


@dataclass
class AgentPermission:
    """Describes what a specific agent is allowed to do.

    Attributes
    ----------
    agent_id:
        Unique agent identifier.
    allowed_read_scopes:
        Scopes the agent may read.  The agent may read any node whose
        ``privacy_scope`` is in this list.
    allowed_write_types:
        Node types the agent may create or update.  Empty list means read-only.
    can_write:
        Convenience flag; if ``False`` the agent cannot write regardless of
        ``allowed_write_types``.
    max_nodes_per_request:
        Hard cap on the number of nodes returned in a single retrieval call.
    granted_at:
        ISO-8601 timestamp when this permission was granted.
    """

    agent_id: str
    allowed_read_scopes: list[PolicyScope] = field(
        default_factory=lambda: [PolicyScope.PUBLIC]
    )
    allowed_write_types: list[str] = field(default_factory=list)
    can_write: bool = False
    max_nodes_per_request: int = 20
    granted_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Node protocol (thin interface to avoid importing core.graph)
# ---------------------------------------------------------------------------


class NodeLike(Protocol):
    """Minimal interface expected from a memory node by the policy engine."""

    @property
    def type(self) -> str: ...

    @property
    def metadata(self) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PolicyViolationError(Exception):
    """Raised when an agent attempts an action not allowed by its permission."""

    def __init__(self, agent_id: str, reason: str) -> None:
        super().__init__(f"Policy violation by agent '{agent_id}': {reason}")
        self.agent_id = agent_id
        self.reason = reason


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------


class PolicyEngine:
    """Central policy enforcement for memory access and writes.

    Parameters
    ----------
    audit_log:
        Optional callable that receives audit log entries as dicts.
        Defaults to a debug-level logger call.
    """

    # System agent that bypasses all policy checks (used in tests and internal ops).
    SYSTEM_AGENT_ID = "system"

    def __init__(
        self, audit_log: Any | None = None
    ) -> None:
        self._permissions: dict[str, AgentPermission] = {}
        self._audit_log = audit_log or self._default_audit

        # Grant the system agent full access by default.
        self.grant(
            AgentPermission(
                agent_id=self.SYSTEM_AGENT_ID,
                allowed_read_scopes=list(PolicyScope),
                allowed_write_types=list(DEFAULT_NODE_SCOPES.keys()),
                can_write=True,
                max_nodes_per_request=10_000,
            )
        )

    # ------------------------------------------------------------------
    # Permission management
    # ------------------------------------------------------------------

    def grant(self, permission: AgentPermission) -> None:
        """Register or replace the permission for ``permission.agent_id``."""
        self._permissions[permission.agent_id] = permission
        logger.debug("Policy: granted permission to agent '%s'", permission.agent_id)

    def revoke(self, agent_id: str) -> None:
        """Remove all permissions for *agent_id* (except system)."""
        if agent_id == self.SYSTEM_AGENT_ID:
            raise ValueError("Cannot revoke system agent permission.")
        self._permissions.pop(agent_id, None)
        logger.info("Policy: revoked permission for agent '%s'", agent_id)

    def get_permission(self, agent_id: str) -> AgentPermission | None:
        """Return the current permission for *agent_id*, or None if not registered."""
        return self._permissions.get(agent_id)

    # ------------------------------------------------------------------
    # Read policy
    # ------------------------------------------------------------------

    def _node_scope(self, node: NodeLike) -> PolicyScope:
        """Determine the effective scope of *node*."""
        raw = node.metadata.get("privacy_scope")
        if raw:
            try:
                return PolicyScope(raw)
            except ValueError:
                pass
        return DEFAULT_NODE_SCOPES.get(node.type, PolicyScope.PRIVATE)

    def check(self, agent_id: str, node: NodeLike) -> bool:
        """Return ``True`` if *agent_id* is allowed to read *node*.

        The system agent always returns ``True``.
        Unregistered agents are denied.
        """
        if agent_id == self.SYSTEM_AGENT_ID:
            return True

        permission = self._permissions.get(agent_id)
        if permission is None:
            self._audit(
                agent_id=agent_id,
                action="read",
                result="denied",
                reason="agent_not_registered",
                node_type=node.type,
            )
            return False

        node_scope = self._node_scope(node)
        allowed = node_scope in permission.allowed_read_scopes
        if not allowed:
            self._audit(
                agent_id=agent_id,
                action="read",
                result="denied",
                reason="scope_not_allowed",
                node_type=node.type,
                attempted_scope=node_scope.value,
            )
        return allowed

    def filter(self, agent_id: str, nodes: list[NodeLike]) -> list[NodeLike]:
        """Return the subset of *nodes* that *agent_id* is allowed to read.

        Also applies the ``max_nodes_per_request`` cap from the permission.
        """
        if agent_id == self.SYSTEM_AGENT_ID:
            return nodes

        permission = self._permissions.get(agent_id)
        if permission is None:
            self._audit(
                agent_id=agent_id,
                action="filter",
                result="denied",
                reason="agent_not_registered",
                requested_count=len(nodes),
                returned_count=0,
            )
            return []

        allowed = [n for n in nodes if self.check(agent_id, n)]
        capped = allowed[: permission.max_nodes_per_request]

        self._audit(
            agent_id=agent_id,
            action="filter",
            result="ok",
            requested_count=len(nodes),
            returned_count=len(capped),
            filtered_out=len(nodes) - len(capped),
        )
        return capped

    # ------------------------------------------------------------------
    # Write policy
    # ------------------------------------------------------------------

    def can_write(self, agent_id: str, node_type: str) -> bool:
        """Return ``True`` if *agent_id* may create/update nodes of *node_type*."""
        if agent_id == self.SYSTEM_AGENT_ID:
            return True

        permission = self._permissions.get(agent_id)
        if permission is None:
            return False

        if not permission.can_write:
            self._audit(
                agent_id=agent_id,
                action="write",
                result="denied",
                reason="writes_disabled",
                node_type=node_type,
            )
            return False

        allowed = node_type in permission.allowed_write_types
        if not allowed:
            self._audit(
                agent_id=agent_id,
                action="write",
                result="denied",
                reason="type_not_in_allowed_write_types",
                node_type=node_type,
            )
        return allowed

    def assert_can_write(self, agent_id: str, node_type: str) -> None:
        """Like :meth:`can_write` but raises :exc:`PolicyViolationError` on denial."""
        if not self.can_write(agent_id, node_type):
            raise PolicyViolationError(
                agent_id,
                f"not allowed to write node type '{node_type}'",
            )

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _audit(self, **kwargs: Any) -> None:
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **kwargs}
        self._audit_log(entry)

    @staticmethod
    def _default_audit(entry: dict[str, Any]) -> None:
        logger.debug("Policy audit: %s", entry)
