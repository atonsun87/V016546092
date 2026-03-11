"""Memory provenance tracking for SELF-OS.

Every node in the knowledge graph can carry a provenance record that
describes *where* the memory came from, *who* authored it, and a log of
all subsequent edits.  Provenance is the foundation of the trust layer:
it lets users inspect why a memory exists and auditors verify the
history of any piece of stored information.

Provenance records are stored in a dedicated ``node_provenance`` SQLite
table and are keyed by ``node_id``.  The table is managed by
:class:`ProvenanceStore`, which shares the same ``aiosqlite`` connection
as :class:`~core.graph.storage.GraphStorage`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

import aiosqlite

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class MemorySource(StrEnum):
    """Origin of a memory node."""

    USER = "user"
    """Directly authored by the end-user (e.g. a typed message)."""

    LLM = "llm"
    """Extracted or inferred by the LLM during pipeline processing."""

    ONBOARDING = "onboarding"
    """Created during the structured onboarding flow."""

    AGENT = "agent"
    """Written by a proactive agent action (scheduler, tools, etc.)."""

    SYSTEM = "system"
    """Internal system operation (consolidation, migration, merge)."""

    IMPORT = "import"
    """Imported from an external source (Obsidian, export file, etc.)."""


@dataclass(slots=True)
class EditRecord:
    """A single entry in a node's edit history."""

    timestamp: str
    """ISO-8601 UTC timestamp of the edit."""

    author: str
    """User-ID or system identifier that performed the edit."""

    field: str
    """Which field was changed: ``"text"``, ``"name"``, ``"metadata"``, etc."""

    previous_value: str | None
    """Serialised previous value (may be truncated for large payloads)."""


@dataclass(slots=True)
class ProvenanceRecord:
    """Full provenance for a single node.

    Parameters
    ----------
    node_id:
        The ID of the graph node this record belongs to.
    user_id:
        The user who owns the node.
    source:
        Enum describing the origin of the memory.
    author:
        The agent, user-id, or system component that created the node.
    created_at:
        ISO-8601 UTC timestamp of node creation (mirrors ``node.created_at``).
    session_id:
        Optional session identifier that was active at creation time.
    pipeline_stage:
        Optional OODA stage that produced this node
        (``"observe"``, ``"orient"``, ``"decide"``, ``"act"``).
    edit_history:
        Ordered list of :class:`EditRecord` objects (oldest first).
    """

    node_id: str
    user_id: str
    source: MemorySource
    author: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    session_id: str | None = None
    pipeline_stage: str | None = None
    edit_history: list[EditRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ProvenanceStore
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS node_provenance (
    node_id       TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    source        TEXT NOT NULL,
    author        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    session_id    TEXT,
    pipeline_stage TEXT,
    edit_history_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_provenance_user_id
    ON node_provenance(user_id);

CREATE INDEX IF NOT EXISTS idx_provenance_source
    ON node_provenance(user_id, source);
"""


class ProvenanceStore:
    """Persist and retrieve :class:`ProvenanceRecord` objects.

    The store is designed to share an ``aiosqlite`` connection that is
    already managed by :class:`~core.graph.storage.GraphStorage`.  If you
    use it standalone (e.g. in tests) pass ``db_path`` and call
    :meth:`ensure_initialized` before any operation.

    Parameters
    ----------
    conn:
        An open *aiosqlite* connection.  Caller is responsible for its
        lifecycle.  Mutually exclusive with *db_path*.
    db_path:
        Path to the SQLite database file.  A new connection is opened on
        the first operation.  Mutually exclusive with *conn*.
    """

    def __init__(
        self,
        conn: aiosqlite.Connection | None = None,
        db_path: str | None = None,
    ) -> None:
        if conn is None and db_path is None:
            raise ValueError("Provide either 'conn' or 'db_path'.")
        self._conn = conn
        self._db_path = db_path
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is not None:
            return self._conn
        if self._db_path is not None and not hasattr(self, "_own_conn"):
            self._own_conn: aiosqlite.Connection = await aiosqlite.connect(self._db_path)
            self._own_conn.row_factory = aiosqlite.Row
            self._conn = self._own_conn
        return self._conn  # type: ignore[return-value]

    async def ensure_initialized(self) -> None:
        """Create tables if they do not already exist."""
        if self._initialized:
            return
        conn = await self._get_conn()
        await conn.executescript(_DDL)
        await conn.commit()
        self._initialized = True

    async def close(self) -> None:
        """Close the connection only if we opened it ourselves."""
        if hasattr(self, "_own_conn") and self._own_conn is not None:
            await self._own_conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def save(self, record: ProvenanceRecord) -> None:
        """Insert or replace a provenance record."""
        await self.ensure_initialized()
        conn = await self._get_conn()
        edit_json = json.dumps(
            [
                {
                    "timestamp": e.timestamp,
                    "author": e.author,
                    "field": e.field,
                    "previous_value": e.previous_value,
                }
                for e in record.edit_history
            ],
            ensure_ascii=False,
        )
        await conn.execute(
            """
            INSERT OR REPLACE INTO node_provenance
                (node_id, user_id, source, author, created_at,
                 session_id, pipeline_stage, edit_history_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.node_id,
                record.user_id,
                record.source.value,
                record.author,
                record.created_at,
                record.session_id,
                record.pipeline_stage,
                edit_json,
            ),
        )
        await conn.commit()
        logger.debug("Saved provenance for node %s (source=%s)", record.node_id, record.source)

    async def append_edit(
        self,
        node_id: str,
        user_id: str,
        author: str,
        field: str,
        previous_value: str | None,
    ) -> None:
        """Append an :class:`EditRecord` to an existing provenance row.

        If no provenance row exists for *node_id* a minimal record is
        created with ``source=MemorySource.SYSTEM``.
        """
        await self.ensure_initialized()
        record = await self.get(node_id)
        if record is None:
            record = ProvenanceRecord(
                node_id=node_id,
                user_id=user_id,
                source=MemorySource.SYSTEM,
                author=author,
            )
        edit = EditRecord(
            timestamp=datetime.now(UTC).isoformat(),
            author=author,
            field=field,
            previous_value=previous_value,
        )
        record.edit_history.append(edit)
        await self.save(record)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, node_id: str) -> ProvenanceRecord | None:
        """Return the provenance record for *node_id*, or ``None``."""
        await self.ensure_initialized()
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT * FROM node_provenance WHERE node_id = ?", (node_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    async def list_for_user(
        self,
        user_id: str,
        source: MemorySource | None = None,
        limit: int = 100,
    ) -> list[ProvenanceRecord]:
        """List provenance records for *user_id*, optionally filtered by source."""
        await self.ensure_initialized()
        conn = await self._get_conn()
        if source is not None:
            cursor = await conn.execute(
                "SELECT * FROM node_provenance WHERE user_id = ? AND source = ? LIMIT ?",
                (user_id, source.value, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM node_provenance WHERE user_id = ? LIMIT ?",
                (user_id, limit),
            )
        rows = await cursor.fetchall()
        return [_row_to_record(r) for r in rows]

    async def count_by_source(self, user_id: str) -> dict[str, int]:
        """Return a ``{source: count}`` breakdown for *user_id*."""
        await self.ensure_initialized()
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT source, COUNT(*) AS n FROM node_provenance WHERE user_id = ? GROUP BY source",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return {row["source"]: int(row["n"]) for row in rows}

    async def delete(self, node_id: str) -> None:
        """Hard-delete the provenance record for *node_id*."""
        await self.ensure_initialized()
        conn = await self._get_conn()
        await conn.execute("DELETE FROM node_provenance WHERE node_id = ?", (node_id,))
        await conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_record(row: aiosqlite.Row) -> ProvenanceRecord:
    raw_edits: list[dict] = json.loads(row["edit_history_json"] or "[]")
    edits = [
        EditRecord(
            timestamp=e["timestamp"],
            author=e["author"],
            field=e["field"],
            previous_value=e.get("previous_value"),
        )
        for e in raw_edits
    ]
    return ProvenanceRecord(
        node_id=row["node_id"],
        user_id=row["user_id"],
        source=MemorySource(row["source"]),
        author=row["author"],
        created_at=row["created_at"],
        session_id=row["session_id"],
        pipeline_stage=row["pipeline_stage"],
        edit_history=edits,
    )


def make_id() -> str:
    """Return a new UUID string."""
    return str(uuid4())
