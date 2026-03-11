"""MemoryScope — policy-aware, scoped view over the memory graph.

Agents and interfaces should never access ``GraphStorage`` or ``GraphAPI``
directly.  Instead they receive a ``MemoryScope`` — a narrowed,
permission-controlled view that exposes only the node types and operations
the caller is authorised to use.

Neurobiological analogy
-----------------------
The same underlying memory traces are not uniformly accessible.  The
prefrontal cortex gates hippocampal retrieval depending on the current task
context, emotional state, and role.  An "IFS Manager" part in session
retrieves different content than a "background analytics" process.

``MemoryScope`` models this gating: each agent is handed a scope configured
for its role.  Attempting to read outside the scope raises ``PermissionError``
with a clear message, making access violations explicit at development time.

Design decisions
----------------
* **Read-only** — writes always go through typed stores (e.g.
  :class:`~core.kernel.belief_store.BeliefStore`) so that provenance is
  always tracked.
* **Composable** — a scope can be further narrowed via
  :meth:`narrow`, returning a new ``MemoryScope`` with a subset of allowed
  types.  The original scope is unchanged.
* **Auditable** — every ``find`` call is logged at DEBUG level with the
  scope label, enabling future audit trails.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from core.graph.model import Node
from core.graph.storage import GraphStorage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class MemoryScope:
    """A read-only, policy-constrained view over the memory graph.

    Parameters
    ----------
    storage:
        Underlying graph storage.
    user_id:
        The user whose memory this scope covers.
    allowed_types:
        Frozenset of node type strings the scope may retrieve.  ``None``
        means *all* types are permitted (privileged / unrestricted scope).
        An empty frozenset means *no* types are permitted (deny-all scope,
        typically the result of narrowing two disjoint scopes).
    label:
        Descriptive name for this scope (e.g. ``"ifs_manager_agent"``,
        ``"background_analytics"``).  Used in log messages and future audit
        trails.
    """

    storage: GraphStorage
    user_id: str
    allowed_types: frozenset[str] | None = None
    label: str = "unnamed_scope"

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    async def find(
        self,
        node_type: str,
        limit: int = 50,
    ) -> list[Node]:
        """Return nodes of *node_type* visible in this scope.

        Parameters
        ----------
        node_type:
            The graph node type string (e.g. ``"BELIEF"``, ``"EMOTION"``).
        limit:
            Maximum number of nodes to return.

        Raises
        ------
        PermissionError
            If *node_type* is not in ``allowed_types`` and the scope is
            restricted (``allowed_types`` non-empty).
        """
        self._check_type(node_type)
        nodes = await self.storage.find_nodes(self.user_id, node_type=node_type, limit=limit)
        logger.debug(
            "MemoryScope[%s] find(type=%s) → %d nodes",
            self.label,
            node_type,
            len(nodes),
        )
        return nodes

    async def find_many(
        self,
        node_types: Sequence[str],
        limit_per_type: int = 50,
    ) -> list[Node]:
        """Return nodes for multiple types, applying the scope policy to each.

        Parameters
        ----------
        node_types:
            Sequence of node type strings to retrieve.
        limit_per_type:
            Maximum nodes per type.
        """
        results: list[Node] = []
        for nt in node_types:
            results.extend(await self.find(nt, limit=limit_per_type))
        return results

    def permits(self, node_type: str) -> bool:
        """Return ``True`` if this scope allows reading *node_type*.

        An unrestricted scope (``allowed_types`` is ``None``) permits
        everything.  A deny-all scope (``allowed_types`` is an empty
        frozenset, typically from narrowing two disjoint scopes) permits
        nothing.
        """
        if self.allowed_types is None:
            return True  # unrestricted / privileged
        return node_type in self.allowed_types

    def narrow(
        self,
        allowed_types: frozenset[str],
        label: str | None = None,
    ) -> MemoryScope:
        """Return a new, further-restricted scope with a subset of allowed types.

        The intersection of *allowed_types* and the current ``allowed_types``
        is used, so narrowing can never expand permissions.  If the current
        scope is unrestricted (``allowed_types`` is ``None``) the result is
        restricted to exactly *allowed_types*.

        Parameters
        ----------
        allowed_types:
            The desired allowed types for the new scope.
        label:
            Label for the narrowed scope.  Defaults to
            ``"<parent_label>:narrowed"``.
        """
        if self.allowed_types is None:
            effective: frozenset[str] = allowed_types
        else:
            effective = self.allowed_types & allowed_types
        return MemoryScope(
            storage=self.storage,
            user_id=self.user_id,
            allowed_types=effective,
            label=label or f"{self.label}:narrowed",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_type(self, node_type: str) -> None:
        if not self.permits(node_type):
            allowed_display = sorted(self.allowed_types) if self.allowed_types else "none"
            raise PermissionError(
                f"MemoryScope[{self.label!r}] does not permit access to node type "
                f"{node_type!r}.  Allowed: {allowed_display}"
            )
