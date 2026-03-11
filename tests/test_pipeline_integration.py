"""Pipeline integration tests — EventStore and MetricsCollector wiring.

These tests verify that the ``MessageProcessor`` correctly connects the
``EventStore`` and ``MetricsCollector`` into the OODA pipeline, closing the
gap between *"foundation built"* and *"actually wired"* for the two services.

Tested invariants:

* Every message processed by ``MessageProcessor`` appends an event to the
  ``EventStore`` (online→offline bridge is live).
* ``MetricsCollector`` records ``messages_processed`` and ``pipeline_ms``
  after each successful ``process_message`` call.
"""

from __future__ import annotations

import asyncio

from core.context.session_memory import SessionMemory
from core.graph.api import GraphAPI
from core.graph.storage import GraphStorage
from core.journal.storage import JournalStorage
from core.observability.metrics import MetricsCollector
from core.pipeline.event_store import EventStore
from core.pipeline.processor import MessageProcessor


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_processor_llm_integration.py style)
# ---------------------------------------------------------------------------


class _NoopQdrant:
    def upsert_embeddings_batch(self, points):
        return

    def search_similar(self, *args, **kwargs):
        return []


class _NoopLLM:
    async def classify_intent(self, text: str) -> str:
        return "REFLECTION"

    async def extract_all(self, text: str, intent: str, graph_hints=None):
        import json
        return json.dumps({"intent": "REFLECTION", "nodes": [], "edges": []})

    async def extract_semantic(self, text: str, intent: str):
        return {"nodes": [], "edges": []}

    async def extract_parts(self, text: str, intent: str):
        return {"nodes": [], "edges": []}

    async def extract_emotion(self, text: str, intent: str):
        return {"nodes": [], "edges": []}

    async def generate_live_reply(self, **kwargs) -> str:
        return ""


def _build_processor(
    tmp_path,
    *,
    event_store: EventStore | None = None,
    metrics: MetricsCollector | None = None,
) -> MessageProcessor:
    db = str(tmp_path / "test.db")
    storage = GraphStorage(db_path=db)
    graph_api = GraphAPI(storage)
    journal = JournalStorage(db_path=db)
    session_memory = SessionMemory()
    return MessageProcessor(
        graph_api=graph_api,
        journal=journal,
        qdrant=_NoopQdrant(),
        session_memory=session_memory,
        llm_client=_NoopLLM(),
        background_mode=False,
        event_store=event_store,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# EventStore wiring
# ---------------------------------------------------------------------------


def test_event_store_receives_event_after_process_message(tmp_path):
    """Every ``process_message`` call must append a ``journal.appended`` event
    to the injected ``EventStore``."""

    async def scenario():
        store = EventStore(db_path=str(tmp_path / "events.db"))
        proc = _build_processor(tmp_path, event_store=store)

        await proc.process_message("u1", "Hello, world!", source="cli")

        events = await store.list_events("u1", event_name="journal.appended")
        assert len(events) >= 1, (
            "Expected at least one 'journal.appended' event in the EventStore "
            f"after process_message, got {len(events)}"
        )
        first = events[0]
        assert first["user_id"] == "u1"
        assert "Hello, world!" in first["payload"].get("text", "")

    asyncio.run(scenario())


def test_event_store_captures_multiple_messages(tmp_path):
    """Multiple messages from the same user produce multiple events."""

    async def scenario():
        store = EventStore(db_path=str(tmp_path / "events.db"))
        proc = _build_processor(tmp_path, event_store=store)

        await proc.process_message("u1", "First message")
        await proc.process_message("u1", "Second message")

        events = await store.list_events("u1", event_name="journal.appended")
        assert len(events) == 2, f"Expected 2 events, got {len(events)}"

    asyncio.run(scenario())


def test_event_store_scoped_by_user_id(tmp_path):
    """Events for one user must not appear when querying another user's events."""

    async def scenario():
        store = EventStore(db_path=str(tmp_path / "events.db"))
        proc = _build_processor(tmp_path, event_store=store)

        await proc.process_message("user-a", "Message from A")
        await proc.process_message("user-b", "Message from B")

        events_a = await store.list_events("user-a")
        events_b = await store.list_events("user-b")

        assert all(e["payload"].get("user_id") == "user-a" for e in events_a)
        assert all(e["payload"].get("user_id") == "user-b" for e in events_b)

    asyncio.run(scenario())


def test_process_message_succeeds_without_event_store(tmp_path):
    """``MessageProcessor`` must work correctly when no ``EventStore`` is injected
    (backward-compatibility guarantee)."""

    async def scenario():
        proc = _build_processor(tmp_path, event_store=None)
        result = await proc.process_message("u1", "No event store here")
        assert result.reply_text is not None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# MetricsCollector wiring
# ---------------------------------------------------------------------------


def test_metrics_increments_messages_processed(tmp_path):
    """``MetricsCollector.messages_processed`` must be incremented after each
    successful ``process_message`` call."""

    async def scenario():
        metrics = MetricsCollector()  # isolated instance for this test
        proc = _build_processor(tmp_path, metrics=metrics)

        before = metrics.snapshot().counters.get("messages_processed", 0)
        await proc.process_message("u1", "Test message")
        after = metrics.snapshot().counters.get("messages_processed", 0)

        assert after == before + 1, (
            f"Expected messages_processed to increment by 1 "
            f"(before={before}, after={after})"
        )

    asyncio.run(scenario())


def test_metrics_records_pipeline_latency(tmp_path):
    """``MetricsCollector`` must record at least one latency sample for
    ``pipeline_ms`` after ``process_message``."""

    async def scenario():
        metrics = MetricsCollector()
        proc = _build_processor(tmp_path, metrics=metrics)

        await proc.process_message("u1", "Latency test")
        snapshot = metrics.snapshot()

        assert "pipeline_ms" in snapshot.latencies, (
            "Expected 'pipeline_ms' latency entry in metrics snapshot after process_message"
        )
        latency = snapshot.latencies["pipeline_ms"]
        assert latency.count >= 1
        assert latency.mean_ms >= 0.0

    asyncio.run(scenario())
