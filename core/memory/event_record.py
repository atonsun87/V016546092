"""EventRecord — typed append-only event schema for the online→offline handoff.

In the neuro-inspired architecture SELF-OS separates two processing paths:

* **Online path** — receives user input, produces a fast reply, and writes an
  ``EventRecord`` to the journal.  It does minimal real-time processing: sanitise,
  classify intent, capture raw signals, generate a response.  It never directly
  mutates beliefs or the semantic memory graph.

* **Offline path** — consumes the queue of ``EventRecord`` objects and performs
  deeper work: episodic memory formation, belief consolidation, salience updates,
  relationship linking, and self-model revision.

``EventRecord`` is the contract between these two paths.  It is intentionally
richer than a raw journal entry: it carries the classified intent, lightweight
emotional/semantic signals captured inline (without LLM cost), and a
``processed_offline`` flag so the offline worker can find unprocessed events.

Schema stability
----------------
New optional fields may be added in a backward-compatible way.  Existing records
are never mutated — the append-only guarantee enables audit trails and
reconsolidation passes over the full history.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

# ---------------------------------------------------------------------------
# EventRecord dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EventRecord:
    """A single captured interaction event.

    Attributes
    ----------
    id:
        Auto-assigned integer primary key.  ``0`` before persistence.
    user_id:
        Identifies the user who produced the event.
    session_id:
        Groups events that belong to the same conversation session.
    timestamp:
        ISO-8601 UTC timestamp of the event.
    source:
        Input channel — e.g. ``"cli"``, ``"telegram"``, ``"api"``.
    text:
        Sanitised user text captured verbatim.
    intent:
        Lightweight intent label assigned by the router in the online path
        (e.g. ``"FEELING_REPORT"``, ``"EVENT_REPORT"``, ``"REFLECTION"``).
    raw_signals:
        Lightweight signals extracted inline during the online pass *without*
        an LLM call.  Typical keys: ``valence``, ``arousal``, ``keywords``,
        ``emotion_labels``.  Populated by cheap heuristics so the online path
        stays fast.
    processed_offline:
        Set to ``True`` by the offline worker after the record has been fully
        processed.  Allows the worker to query only pending events.
    offline_processed_at:
        ISO-8601 timestamp when offline processing completed.
    """

    id: int = 0
    user_id: str = ""
    session_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source: str = "cli"
    text: str = ""
    intent: str = "UNKNOWN"
    raw_signals: dict[str, Any] = field(default_factory=dict)
    processed_offline: bool = False
    offline_processed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "text": self.text,
            "intent": self.intent,
            "raw_signals": self.raw_signals,
            "processed_offline": self.processed_offline,
            "offline_processed_at": self.offline_processed_at,
        }


# ---------------------------------------------------------------------------
# EventRecordStore
# ---------------------------------------------------------------------------


class EventRecordStore:
    """Persistent store for :class:`EventRecord` objects.

    Uses the same SQLite database as the rest of SELF-OS to avoid extra
    infrastructure.  The table ``event_records`` is separate from
    ``journal_entries`` so the richer typed schema can evolve independently.

    The append-only nature is enforced by design: :meth:`append` inserts a
    new row; there is no update method for the content fields.  Only the
    ``processed_offline`` / ``offline_processed_at`` columns are mutable,
    reflecting processing state rather than event content.
    """

    def __init__(self, db_path: str | Path = "data/self_os.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False
        self._init_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Initialisation
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
                    CREATE TABLE IF NOT EXISTS event_records (
                        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id             TEXT    NOT NULL,
                        session_id          TEXT,
                        timestamp           TEXT    NOT NULL,
                        source              TEXT    NOT NULL DEFAULT 'cli',
                        text                TEXT    NOT NULL,
                        intent              TEXT    NOT NULL DEFAULT 'UNKNOWN',
                        raw_signals_json    TEXT    NOT NULL DEFAULT '{}',
                        processed_offline   INTEGER NOT NULL DEFAULT 0,
                        offline_processed_at TEXT
                    )
                    """
                )
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_er_user_processed "
                    "ON event_records (user_id, processed_offline)"
                )
                await conn.commit()
            self._initialized = True

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def append(self, record: EventRecord) -> EventRecord:
        """Persist *record* and return it with the assigned ``id``."""
        import json

        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            cursor = await conn.execute(
                """
                INSERT INTO event_records
                    (user_id, session_id, timestamp, source, text,
                     intent, raw_signals_json, processed_offline, offline_processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.user_id,
                    record.session_id,
                    record.timestamp,
                    record.source,
                    record.text,
                    record.intent,
                    json.dumps(record.raw_signals),
                    int(record.processed_offline),
                    record.offline_processed_at,
                ),
            )
            await conn.commit()
            record.id = int(cursor.lastrowid)
        return record

    async def mark_processed(self, record_id: int) -> None:
        """Mark an event as processed by the offline worker."""
        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            await conn.execute(
                """
                UPDATE event_records
                SET processed_offline = 1,
                    offline_processed_at = ?
                WHERE id = ?
                """,
                (datetime.now(UTC).isoformat(), record_id),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_pending(
        self, user_id: str, limit: int = 200
    ) -> list[EventRecord]:
        """Return unprocessed event records for *user_id*, oldest first."""
        import json

        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM event_records
                WHERE user_id = ? AND processed_offline = 0
                ORDER BY id ASC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return [_row_to_record(row, json) for row in rows]

    async def list_recent(
        self, user_id: str, limit: int = 50
    ) -> list[EventRecord]:
        """Return the most recent event records for *user_id* regardless of status."""
        import json

        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT * FROM event_records
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
            rows = await cursor.fetchall()
        return [_row_to_record(row, json) for row in rows]

    async def count_pending(self, user_id: str) -> int:
        """Return the number of unprocessed events for *user_id*."""
        await self._ensure_initialized()
        async with aiosqlite.connect(str(self.db_path)) as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM event_records WHERE user_id = ? AND processed_offline = 0",
                (user_id,),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _row_to_record(row: Any, json_module: Any) -> EventRecord:
    import contextlib
    keys = row.keys()
    raw_signals: dict = {}
    raw_json = row["raw_signals_json"] if "raw_signals_json" in keys else "{}"
    with contextlib.suppress(Exception):
        raw_signals = json_module.loads(raw_json) if raw_json else {}
    return EventRecord(
        id=int(row["id"]),
        user_id=row["user_id"],
        session_id=row["session_id"] if "session_id" in keys else None,
        timestamp=row["timestamp"],
        source=row["source"] if "source" in keys else "cli",
        text=row["text"],
        intent=row["intent"] if "intent" in keys else "UNKNOWN",
        raw_signals=raw_signals,
        processed_offline=bool(row["processed_offline"]),
        offline_processed_at=row["offline_processed_at"] if "offline_processed_at" in keys else None,
    )
