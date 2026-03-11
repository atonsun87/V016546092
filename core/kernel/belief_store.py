"""BeliefStore — graph-backed persistence for DerivedBelief objects.

``BeliefStore`` wraps :class:`~core.graph.storage.GraphStorage` and exposes a
typed, revision-aware interface for persisting and querying
:class:`~core.kernel.signal.DerivedBelief` objects.  It acts as the boundary
between the raw graph layer and the higher-level belief-reasoning layer.

Neurobiological analogy
-----------------------
The hippocampus encodes episodic traces (raw signals) and gradually transfers
them to the cortex as semantic memories (beliefs).  ``BeliefStore`` models
that transfer point: it converts ``DerivedBelief`` objects into ``BELIEF``
graph nodes and keeps the full provenance chain (``source_signal_ids``,
``revision_history``) in node metadata — so the system can always trace a
belief back to the raw events that produced it.

Revision model
--------------
Each call to :meth:`revise` appends a snapshot of the previous belief state
to ``node.metadata["belief_provenance"]`` before overwriting the current
values.  This gives a complete audit trail without a separate table.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from core.graph.model import Node
from core.graph.storage import GraphStorage
from core.kernel.signal import DerivedBelief

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class BeliefStore:
    """Persist and revise :class:`DerivedBelief` objects in the knowledge graph.

    All beliefs are stored as ``BELIEF`` nodes.  Revision history is stored in
    ``node.metadata["belief_provenance"]`` so that the full chain is auditable
    without a separate table.

    Parameters
    ----------
    storage:
        The underlying :class:`~core.graph.storage.GraphStorage` instance.
    """

    def __init__(self, storage: GraphStorage) -> None:
        self._storage = storage

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def save(self, belief: DerivedBelief) -> DerivedBelief:
        """Persist *belief* as a ``BELIEF`` node and return the saved belief.

        If a node with the same ``belief.graph_node_id`` already exists it is
        **upserted** (metadata updated in-place), preserving the node id so
        that outgoing edges in the graph remain valid.
        """
        metadata: dict[str, Any] = {
            "belief_id": belief.id,
            "confidence": belief.confidence,
            "source_signal_ids": belief.source_signal_ids,
            "belief_provenance": belief.revision_history,
            "created_at": belief.created_at,
            "updated_at": belief.updated_at,
        }
        node = Node(
            id=belief.graph_node_id or belief.id,
            user_id=belief.user_id,
            type="BELIEF",
            text=belief.statement,
            key=f"belief:{belief.id}",
            metadata=metadata,
            created_at=belief.created_at,
        )
        await self._storage.upsert_node(node)
        belief.graph_node_id = node.id
        logger.debug(
            "BeliefStore saved belief %s (confidence=%.2f) for user %s",
            belief.id,
            belief.confidence,
            belief.user_id,
        )
        return belief

    async def revise(
        self,
        belief: DerivedBelief,
        new_statement: str,
        new_confidence: float,
        reason: str = "",
    ) -> DerivedBelief:
        """Revise *belief* in place and persist the update.

        Calls :meth:`DerivedBelief.revise` which appends the old state to
        ``revision_history``, then saves the updated node to the graph.

        Parameters
        ----------
        belief:
            The belief to revise.  Must have been previously saved (i.e.
            ``belief.graph_node_id`` must be set).
        new_statement:
            Replacement belief text.
        new_confidence:
            New confidence level ``[0, 1]``.
        reason:
            Human-readable reason for the revision (stored in provenance).
        """
        belief.revise(new_statement, new_confidence, reason)
        logger.info(
            "BeliefStore revising belief %s → %r (confidence=%.2f, reason=%r)",
            belief.id,
            new_statement[:60],
            new_confidence,
            reason,
        )
        return await self.save(belief)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def get(self, user_id: str, belief_id: str) -> DerivedBelief | None:
        """Retrieve a single belief by its ``belief_id``.

        Returns ``None`` if not found.
        """
        node = await self._storage.find_by_key(
            user_id, node_type="BELIEF", key=f"belief:{belief_id}"
        )
        if node is None:
            return None
        return _node_to_belief(node)

    async def list_beliefs(
        self,
        user_id: str,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[DerivedBelief]:
        """Return beliefs for *user_id* with confidence ≥ *min_confidence*.

        Ordered by confidence descending (strongest beliefs first).

        Parameters
        ----------
        user_id:
            The user whose beliefs to retrieve.
        min_confidence:
            Minimum confidence threshold.  Use ``0.0`` for all beliefs.
        limit:
            Maximum number of beliefs to return after confidence filtering.
            We fetch up to ``limit * 5`` raw nodes to account for beliefs that
            fall below ``min_confidence``; confidence filtering is done in
            Python because ``GraphStorage`` does not support metadata-level
            filtering at the SQL layer.
        """
        nodes = await self._storage.find_nodes(user_id, node_type="BELIEF", limit=limit * 5)
        beliefs = [_node_to_belief(n) for n in nodes]
        filtered = [b for b in beliefs if b.confidence >= min_confidence]
        filtered.sort(key=lambda b: b.confidence, reverse=True)
        return filtered[:limit]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _node_to_belief(node: Node) -> DerivedBelief:
    """Convert a graph ``BELIEF`` node to a :class:`DerivedBelief`."""
    m = node.metadata
    return DerivedBelief(
        id=m.get("belief_id", node.id),
        user_id=node.user_id,
        statement=node.text or node.name or "",
        confidence=float(m.get("confidence", 1.0)),
        source_signal_ids=list(m.get("source_signal_ids", [])),
        graph_node_id=node.id,
        revision_history=list(m.get("belief_provenance", [])),
        created_at=node.created_at,
        updated_at=m.get("updated_at", node.created_at),
    )
