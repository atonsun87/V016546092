"""Memory trust operations — user control over their own memories.

This module provides the :class:`MemoryTrustService`, which gives users
explicit control over memories stored in the knowledge graph:

* **Edit** — update a node's text or name with a full audit trail.
* **Soft-delete** — mark a node as deleted (reversible via graph layer).
* **Hard-delete** — permanently remove a node, its edges, and its
  provenance record.
* **Export** — produce a portable JSON snapshot of the user's memory.
* **Inspect** — retrieve the full provenance and edit history for a node.

All mutating operations append an :class:`~core.memory.provenance.EditRecord`
so the complete history is preserved.

Usage example::

    service = MemoryTrustService(graph_api, provenance_store)
    await service.edit_node_text("user-123", "node-abc", new_text="updated thought")
    snapshot = await service.export_user_data("user-123")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from core.graph.api import GraphAPI
from core.graph.model import Node
from core.memory.provenance import ProvenanceRecord, ProvenanceStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemoryExport:
    """A portable, self-contained snapshot of all user memories.

    Attributes
    ----------
    user_id:
        The user whose data is exported.
    exported_at:
        ISO-8601 UTC timestamp of the export.
    node_count:
        Total number of nodes in the export.
    edge_count:
        Total number of edges in the export.
    nodes:
        List of node dicts (JSON-serialisable).
    edges:
        List of edge dicts (JSON-serialisable).
    provenance:
        List of provenance record dicts keyed by ``node_id``.
    """

    user_id: str
    exported_at: str
    node_count: int
    edge_count: int
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    provenance: list[dict[str, Any]]

    def to_json(self, indent: int = 2) -> str:
        """Serialise the export to a JSON string."""
        return json.dumps(
            {
                "user_id": self.user_id,
                "exported_at": self.exported_at,
                "node_count": self.node_count,
                "edge_count": self.edge_count,
                "nodes": self.nodes,
                "edges": self.edges,
                "provenance": self.provenance,
            },
            ensure_ascii=False,
            indent=indent,
        )


@dataclass(slots=True)
class TrustOperationResult:
    """Outcome of a single trust operation."""

    success: bool
    node_id: str
    operation: str
    message: str = ""


# ---------------------------------------------------------------------------
# MemoryTrustService
# ---------------------------------------------------------------------------


class MemoryTrustService:
    """High-level service for user-controlled memory operations.

    Parameters
    ----------
    graph_api:
        The :class:`~core.graph.api.GraphAPI` instance used to read and
        write graph nodes and edges.
    provenance_store:
        The :class:`~core.memory.provenance.ProvenanceStore` instance
        used to record audit events.
    """

    def __init__(self, graph_api: GraphAPI, provenance_store: ProvenanceStore) -> None:
        self._api = graph_api
        self._provenance = provenance_store

    # ------------------------------------------------------------------
    # Edit
    # ------------------------------------------------------------------

    async def edit_node_text(
        self,
        user_id: str,
        node_id: str,
        new_text: str,
        author: str | None = None,
    ) -> TrustOperationResult:
        """Replace the text content of a node and record the edit.

        Parameters
        ----------
        user_id:
            The owner of the node.
        node_id:
            ID of the node to edit.
        new_text:
            Replacement text value.
        author:
            Identifier of who is making the edit.  Defaults to *user_id*.
        """
        author = author or user_id
        try:
            node = await self._api.storage.get_node(node_id)
        except KeyError:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="edit_text",
                message=f"Node {node_id!r} not found.",
            )

        if node.user_id != user_id:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="edit_text",
                message="Permission denied: node belongs to a different user.",
            )

        previous_text = node.text
        updated_node = Node(
            id=node.id,
            user_id=node.user_id,
            type=node.type,
            name=node.name,
            text=new_text,
            subtype=node.subtype,
            key=node.key,
            metadata=dict(node.metadata),
            created_at=node.created_at,
        )
        await self._api.storage.upsert_node(updated_node)

        await self._provenance.append_edit(
            node_id=node_id,
            user_id=user_id,
            author=author,
            field="text",
            previous_value=previous_text,
        )
        logger.info("User %s edited text of node %s", user_id, node_id)
        return TrustOperationResult(
            success=True,
            node_id=node_id,
            operation="edit_text",
            message="Node text updated.",
        )

    async def edit_node_name(
        self,
        user_id: str,
        node_id: str,
        new_name: str,
        author: str | None = None,
    ) -> TrustOperationResult:
        """Replace the name of a node and record the edit."""
        author = author or user_id
        try:
            node = await self._api.storage.get_node(node_id)
        except KeyError:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="edit_name",
                message=f"Node {node_id!r} not found.",
            )

        if node.user_id != user_id:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="edit_name",
                message="Permission denied: node belongs to a different user.",
            )

        previous_name = node.name
        updated_node = Node(
            id=node.id,
            user_id=node.user_id,
            type=node.type,
            name=new_name,
            text=node.text,
            subtype=node.subtype,
            key=node.key,
            metadata=dict(node.metadata),
            created_at=node.created_at,
        )
        await self._api.storage.upsert_node(updated_node)

        await self._provenance.append_edit(
            node_id=node_id,
            user_id=user_id,
            author=author,
            field="name",
            previous_value=previous_name,
        )
        logger.info("User %s edited name of node %s", user_id, node_id)
        return TrustOperationResult(
            success=True,
            node_id=node_id,
            operation="edit_name",
            message="Node name updated.",
        )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def soft_delete_node(
        self,
        user_id: str,
        node_id: str,
        author: str | None = None,
    ) -> TrustOperationResult:
        """Mark a node as deleted (reversible soft-delete).

        The node remains in the database with ``is_deleted = 1`` and is
        excluded from normal queries.  The provenance record is retained.
        """
        author = author or user_id
        try:
            node = await self._api.storage.get_node(node_id)
        except KeyError:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="soft_delete",
                message=f"Node {node_id!r} not found.",
            )

        if node.user_id != user_id:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="soft_delete",
                message="Permission denied: node belongs to a different user.",
            )

        await self._api.storage.soft_delete_node(node_id)
        await self._provenance.append_edit(
            node_id=node_id,
            user_id=user_id,
            author=author,
            field="is_deleted",
            previous_value="0",
        )
        logger.info("User %s soft-deleted node %s", user_id, node_id)
        return TrustOperationResult(
            success=True,
            node_id=node_id,
            operation="soft_delete",
            message="Node marked as deleted.",
        )

    async def hard_delete_node(
        self,
        user_id: str,
        node_id: str,
        author: str | None = None,
    ) -> TrustOperationResult:
        """Permanently remove a node, its edges, and its provenance record.

        This is irreversible.  Use :meth:`soft_delete_node` for a
        recoverable option.
        """
        author = author or user_id
        conn = await self._api.storage._get_conn()
        await self._api.storage._ensure_initialized()

        cursor = await conn.execute(
            "SELECT id, user_id FROM nodes WHERE id = ?", (node_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="hard_delete",
                message=f"Node {node_id!r} not found.",
            )

        if row["user_id"] != user_id:
            return TrustOperationResult(
                success=False,
                node_id=node_id,
                operation="hard_delete",
                message="Permission denied: node belongs to a different user.",
            )

        # Remove edges first (FK constraint)
        await conn.execute(
            "DELETE FROM edges WHERE source_node_id = ? OR target_node_id = ?",
            (node_id, node_id),
        )
        await conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        await conn.commit()

        # Remove provenance — node is gone; record would be orphaned
        await self._provenance.delete(node_id)

        logger.warning("User %s permanently deleted node %s", user_id, node_id)
        return TrustOperationResult(
            success=True,
            node_id=node_id,
            operation="hard_delete",
            message="Node permanently deleted.",
        )

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    async def export_user_data(self, user_id: str) -> MemoryExport:
        """Build a portable JSON-serialisable snapshot of all user data.

        The export includes nodes, edges, and provenance records so that
        the user can inspect, archive, or migrate their memories.
        """
        nodes = await self._api.storage.find_nodes(user_id, limit=10_000)
        edges = await self._api.storage.list_edges(user_id)
        provenance_records = await self._provenance.list_for_user(user_id, limit=10_000)

        def _node_to_dict(n: Node) -> dict[str, Any]:
            return {
                "id": n.id,
                "type": n.type,
                "name": n.name,
                "text": n.text,
                "subtype": n.subtype,
                "key": n.key,
                "metadata": n.metadata,
                "created_at": n.created_at,
            }

        def _prov_to_dict(p: ProvenanceRecord) -> dict[str, Any]:
            return {
                "node_id": p.node_id,
                "source": p.source.value,
                "author": p.author,
                "created_at": p.created_at,
                "session_id": p.session_id,
                "pipeline_stage": p.pipeline_stage,
                "edit_history": [
                    {
                        "timestamp": e.timestamp,
                        "author": e.author,
                        "field": e.field,
                        "previous_value": e.previous_value,
                    }
                    for e in p.edit_history
                ],
            }

        exported_at = datetime.now(UTC).isoformat()
        return MemoryExport(
            user_id=user_id,
            exported_at=exported_at,
            node_count=len(nodes),
            edge_count=len(edges),
            nodes=[_node_to_dict(n) for n in nodes],
            edges=[
                {
                    "id": e.id,
                    "source_node_id": e.source_node_id,
                    "target_node_id": e.target_node_id,
                    "relation": e.relation,
                    "metadata": e.metadata,
                    "created_at": e.created_at,
                }
                for e in edges
            ],
            provenance=[_prov_to_dict(p) for p in provenance_records],
        )

    # ------------------------------------------------------------------
    # Inspect
    # ------------------------------------------------------------------

    async def get_memory_provenance(
        self,
        user_id: str,
        node_id: str,
    ) -> ProvenanceRecord | None:
        """Return the provenance record for a node owned by *user_id*.

        Returns ``None`` if either the node or its provenance record does
        not exist, or if the node belongs to a different user.
        """
        try:
            node = await self._api.storage.get_node(node_id)
        except KeyError:
            return None

        if node.user_id != user_id:
            return None

        return await self._provenance.get(node_id)
