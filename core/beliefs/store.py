"""BeliefStore — persistent storage for revisable :class:`BeliefRecord` objects.

Architecture contract
---------------------
* **Offline path only writes beliefs.**  The online path (ObserveStage,
  OrientStage, ActStage) reads beliefs via the retrieval layer but never
  creates or mutates them directly.  This ensures that belief mutations are
  controlled, audited, and happen only after offline consolidation.
* **Online path reads beliefs** as part of context construction, but only
  through approved :class:`~core.retrieval.view.MemoryView` scopes.

The store uses the same SQLite database as the rest of SELF-OS.  Beliefs are
stored as JSON blobs keyed by ``(user_id, key)``.  This avoids a rigid schema
migration burden while keeping retrieval efficient for the access patterns
we actually need.

Supported operations
--------------------
* :meth:`upsert` — insert or update a belief by key.
* :meth:`get` — retrieve by ``(user_id, key)``.
* :meth:`get_by_id` — retrieve by UUID.
* :meth:`list_for_user` — all beliefs for a user, optionally filtered by
  domain or minimum confidence.
* :meth:`apply_evidence` — update confidence in-place and record revision.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from core.beliefs.schema import BeliefRecord


class BeliefStore:
    """Persistent store for :class:`BeliefRecord` objects.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Defaults to ``data/self_os.db``.
    """

    def __init__(self, db_path: str | Path = "data/self_os.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Schema initialisation
    # ------------------------------------------------------------------

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with aiosqlite.connect(str(self.db_path)) as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS beliefs (
                        id          TEXT PRIMARY KEY,
                        user_id     TEXT NOT NULL,
                        key_name    TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        confidence  REAL NOT NULL DEFAULT 0.5,
                        updated_at  TEXT NOT NULL
                    )
                    """
                )
                await conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_beliefs_user_key "
                    "ON beliefs (user_id, key_name)"
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_beliefs_user "
                    "ON beliefs (user_id)"
                )
                await conn.commit()
            self._initialized = True

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert(self, belief: BeliefRecord) -> BeliefRecord:
        """Persist *belief*, inserting or replacing by ``(user_id, key)``.

        The record's ``updated_at`` is refreshed on every upsert.
        """
        belief.updated_at = datetime.now(UTC).isoformat()
        payload = json.dumps(belief.to_dict())

        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute(
                """
                INSERT INTO beliefs (id, user_id, key_name, payload_json, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, key_name) DO UPDATE SET
                    id          = excluded.id,
                    payload_json = excluded.payload_json,
                    confidence  = excluded.confidence,
                    updated_at  = excluded.updated_at
                """,
                (
                    belief.id,
                    belief.user_id,
                    belief.key,
                    payload,
                    belief.confidence,
                    belief.updated_at,
                ),
            )
            await conn.commit()
        return belief

    async def apply_evidence(
        self,
        user_id: str,
        key: str,
        *,
        confidence_delta: float,
        reason: str,
        evidence_ref: str | None = None,
    ) -> BeliefRecord | None:
        """Apply incremental evidence to an existing belief and persist.

        Returns the updated :class:`BeliefRecord`, or ``None`` if no belief
        with ``(user_id, key)`` exists.
        """
        belief = await self.get(user_id, key)
        if belief is None:
            return None
        belief.apply_evidence(
            confidence_delta=confidence_delta,
            reason=reason,
            evidence_ref=evidence_ref,
        )
        return await self.upsert(belief)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, user_id: str, key: str) -> BeliefRecord | None:
        """Return the belief with the given key, or ``None``."""
        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT payload_json FROM beliefs WHERE user_id = ? AND key_name = ?",
                (user_id, key),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return BeliefRecord.from_dict(json.loads(row["payload_json"]))

    async def get_by_id(self, belief_id: str) -> BeliefRecord | None:
        """Return the belief with the given UUID, or ``None``."""
        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT payload_json FROM beliefs WHERE id = ?",
                (belief_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return BeliefRecord.from_dict(json.loads(row["payload_json"]))

    async def list_for_user(
        self,
        user_id: str,
        *,
        domain: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 200,
    ) -> list[BeliefRecord]:
        """Return beliefs for *user_id*, optionally filtered.

        Results are ordered by ``confidence DESC`` so the most firmly held
        beliefs appear first.
        """
        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT payload_json FROM beliefs
                WHERE user_id = ? AND confidence >= ?
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (user_id, min_confidence, limit),
            )
            rows = await cursor.fetchall()

        beliefs: list[BeliefRecord] = []
        for row in rows:
            try:
                b = BeliefRecord.from_dict(json.loads(row["payload_json"]))
                if domain is None or b.domain == domain:
                    beliefs.append(b)
            except Exception:
                continue
        return beliefs

    async def delete(self, user_id: str, key: str) -> bool:
        """Delete a belief.  Returns ``True`` if a row was removed."""
        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            cursor = await conn.execute(
                "DELETE FROM beliefs WHERE user_id = ? AND key_name = ?",
                (user_id, key),
            )
            await conn.commit()
        return (cursor.rowcount or 0) > 0
