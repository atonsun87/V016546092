"""Append-only persistent EventStore for SELF-OS.

The :class:`EventStore` complements the in-process :class:`~core.pipeline.events.EventBus`
by **durably persisting** every event to a SQLite table with append-only semantics.

Architectural role
------------------
In the neurobiologically inspired design, the **online** phase (OBSERVE stage) focuses on
*signal capture*: the agent receives a stimulus (user message), sanitises it, classifies
intent, and immediately appends a raw :class:`~core.pipeline.events.Event` to the durable
log before any heavy processing begins.  The **offline** phase (consolidation, belief
revision, abstraction) then reads from this log at leisure — exactly like the hippocampus
replaying episodic signals during consolidation.

Guarantees
----------
* **Append-only**: rows are never updated or deleted by this class.  Soft-delete of
  user data is handled at the graph-node level; the event log is an immutable audit trail.
* **Idempotent writes**: each event carries a unique ``event_id``; duplicate inserts are
  silently ignored via ``INSERT OR IGNORE``.
* **Scoped reads**: :meth:`list_events` is always scoped by ``user_id`` so that users
  cannot observe each other's raw signal stream.

Usage::

    store = EventStore("data/self_os.db")

    # append an event (typically called from ObserveStage)
    event = Event(name="journal.appended", payload={"user_id": "u1", "text": "…"}, timestamp=…)
    await store.append(event)

    # offline consolidator reads recent raw signals for a user
    events = await store.list_events("u1", event_name="journal.appended", limit=200)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from core.pipeline.events import Event

logger = logging.getLogger(__name__)


class EventStore:
    """Durable, append-only log of :class:`~core.pipeline.events.Event` objects.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Parent directories are created
        automatically.  Defaults to ``"data/self_os.db"`` (the shared DB).
    """

    def __init__(self, db_path: str | Path = "data/self_os.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # ── initialisation ────────────────────────────────────────────────────

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with aiosqlite.connect(str(self.db_path)) as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS raw_events (
                        event_id   TEXT PRIMARY KEY,
                        event_name TEXT NOT NULL,
                        user_id    TEXT NOT NULL,
                        timestamp  TEXT NOT NULL,
                        payload    TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_raw_events_user_ts
                        ON raw_events(user_id, timestamp DESC)
                    """
                )
                await conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_raw_events_name_ts
                        ON raw_events(event_name, timestamp DESC)
                    """
                )
                await conn.commit()
            self._initialized = True

    # ── public API ────────────────────────────────────────────────────────

    async def append(self, event: Event, *, user_id: str = "") -> None:
        """Persist *event* to the append-only log.

        Parameters
        ----------
        event:
            The :class:`~core.pipeline.events.Event` to persist.
        user_id:
            The user whose raw signal stream this event belongs to.  When
            empty the value is inferred from ``event.payload["user_id"]``; if
            that is also absent the event is stored under the empty string
            (system-level events).
        """
        await self._ensure_initialized()

        effective_user = user_id or str(event.payload.get("user_id", ""))
        event_id = str(event.payload.get("event_id", uuid4()))

        try:
            payload_json = json.dumps(event.payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            logger.warning("EventStore.append: payload serialisation failed: %s", exc)
            payload_json = "{}"

        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO raw_events
                    (event_id, event_name, user_id, timestamp, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, event.name, effective_user, event.timestamp, payload_json),
            )
            await conn.commit()

        logger.debug(
            "EventStore: persisted event '%s' for user '%s' at %s",
            event.name, effective_user, event.timestamp,
        )

    async def list_events(
        self,
        user_id: str,
        *,
        event_name: str | None = None,
        since: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return recent events for *user_id*, newest first.

        Parameters
        ----------
        user_id:
            Only return events belonging to this user.
        event_name:
            Optional filter by event name (e.g. ``"journal.appended"``).
        since:
            Optional ISO-8601 lower bound for the event timestamp.  Only
            events at or after this timestamp are returned.
        limit:
            Maximum number of records to return.  Defaults to ``200``.

        Returns
        -------
        list[dict]
            Each dict has keys ``event_id``, ``event_name``, ``user_id``,
            ``timestamp``, and ``payload`` (as a decoded dict).
        """
        await self._ensure_initialized()

        conditions = ["user_id = ?"]
        params: list[Any] = [user_id]

        if event_name is not None:
            conditions.append("event_name = ?")
            params.append(event_name)

        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)

        where_clause = " AND ".join(conditions)
        params.append(limit)
        sql = (
            f"SELECT event_id, event_name, user_id, timestamp, payload "
            f"FROM raw_events WHERE {where_clause} "
            f"ORDER BY timestamp DESC LIMIT ?"
        )

        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "event_name": row["event_name"],
                    "user_id": row["user_id"],
                    "timestamp": row["timestamp"],
                    "payload": payload,
                }
            )

        return result

    async def count_events(
        self,
        user_id: str,
        *,
        event_name: str | None = None,
    ) -> int:
        """Return the total number of persisted events for *user_id*.

        Useful for offline consolidation loops that only start processing once
        enough raw signals have accumulated (analogous to the biological
        threshold for hippocampal replay).
        """
        await self._ensure_initialized()

        if event_name is not None:
            sql = "SELECT COUNT(*) FROM raw_events WHERE user_id = ? AND event_name = ?"
            params: tuple[Any, ...] = (user_id, event_name)
        else:
            sql = "SELECT COUNT(*) FROM raw_events WHERE user_id = ?"
            params = (user_id,)

        async with aiosqlite.connect(str(self.db_path)) as conn:
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()

        return int(row[0]) if row else 0

    async def get_oldest_unprocessed(
        self,
        user_id: str,
        *,
        event_name: str = "journal.appended",
        before: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return the oldest unprocessed events, oldest first.

        This is the primary method used by the **offline consolidation** phase
        to replay raw signals in chronological order — mirroring hippocampal
        memory consolidation during sleep.

        Parameters
        ----------
        user_id:
            Scope to this user's signal stream.
        event_name:
            The event type to replay.  Defaults to ``"journal.appended"``.
        before:
            Optional ISO-8601 upper bound.  Only events strictly before this
            timestamp are returned — allowing the consolidator to replay a
            bounded window (e.g. "everything captured more than 1 hour ago").
        limit:
            Maximum events to return.
        """
        await self._ensure_initialized()

        conditions = ["user_id = ?", "event_name = ?"]
        params: list[Any] = [user_id, event_name]

        if before is not None:
            conditions.append("timestamp < ?")
            params.append(before)

        where_clause = " AND ".join(conditions)
        params.append(limit)
        sql = (
            f"SELECT event_id, event_name, user_id, timestamp, payload "
            f"FROM raw_events WHERE {where_clause} "
            f"ORDER BY timestamp ASC LIMIT ?"
        )

        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "event_name": row["event_name"],
                    "user_id": row["user_id"],
                    "timestamp": row["timestamp"],
                    "payload": payload,
                }
            )
        return result
