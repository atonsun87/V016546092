"""Tests for the append-only EventStore (core/pipeline/event_store.py)."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from core.pipeline.event_store import EventStore
from core.pipeline.events import Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(name: str = "journal.appended", user_id: str = "u1", text: str = "hello") -> Event:
    from datetime import datetime, timezone

    return Event(
        name=name,
        payload={"user_id": user_id, "text": text},
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_event_store_append_and_list():
    """Events appended to the store can be listed back."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        ev = _make_event(user_id="u1", text="first message")
        _run(store.append(ev, user_id="u1"))

        events = _run(store.list_events("u1"))
        assert len(events) == 1
        assert events[0]["event_name"] == "journal.appended"
        assert events[0]["payload"]["text"] == "first message"


def test_event_store_scoped_by_user():
    """list_events returns only events for the requested user."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        _run(store.append(_make_event(user_id="alice"), user_id="alice"))
        _run(store.append(_make_event(user_id="bob"), user_id="bob"))

        alice_events = _run(store.list_events("alice"))
        bob_events = _run(store.list_events("bob"))

        assert len(alice_events) == 1
        assert len(bob_events) == 1
        assert alice_events[0]["user_id"] == "alice"
        assert bob_events[0]["user_id"] == "bob"


def test_event_store_filter_by_event_name():
    """list_events filters correctly by event_name."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        _run(store.append(_make_event(name="journal.appended", user_id="u1"), user_id="u1"))
        _run(store.append(_make_event(name="pipeline.processed", user_id="u1"), user_id="u1"))

        journal = _run(store.list_events("u1", event_name="journal.appended"))
        processed = _run(store.list_events("u1", event_name="pipeline.processed"))

        assert len(journal) == 1
        assert len(processed) == 1
        assert journal[0]["event_name"] == "journal.appended"
        assert processed[0]["event_name"] == "pipeline.processed"


def test_event_store_count_events():
    """count_events returns the correct total."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        for i in range(5):
            _run(store.append(_make_event(user_id="u1", text=f"msg {i}"), user_id="u1"))

        assert _run(store.count_events("u1")) == 5
        assert _run(store.count_events("u2")) == 0


def test_event_store_count_events_by_name():
    """count_events respects the event_name filter."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        _run(store.append(_make_event(name="journal.appended", user_id="u1"), user_id="u1"))
        _run(store.append(_make_event(name="journal.appended", user_id="u1"), user_id="u1"))
        _run(store.append(_make_event(name="other.event", user_id="u1"), user_id="u1"))

        assert _run(store.count_events("u1", event_name="journal.appended")) == 2
        assert _run(store.count_events("u1", event_name="other.event")) == 1


def test_event_store_append_is_idempotent():
    """Appending the same event_id twice is silently ignored (no duplicate rows)."""
    from core.pipeline.events import Event
    from datetime import datetime, timezone

    fixed_id = "dup-event-id-001"
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))

        ev = Event(
            name="journal.appended",
            payload={"user_id": "u1", "text": "hello", "event_id": fixed_id},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        _run(store.append(ev, user_id="u1"))
        _run(store.append(ev, user_id="u1"))  # duplicate

        events = _run(store.list_events("u1"))
        assert len(events) == 1


def test_event_store_get_oldest_unprocessed():
    """get_oldest_unprocessed returns events in chronological order."""
    from datetime import datetime, timezone, timedelta

    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        # Append three events with different timestamps
        for i in range(3):
            ts = (base + timedelta(hours=i)).isoformat()
            ev = Event(
                name="journal.appended",
                payload={"user_id": "u1", "text": f"msg {i}"},
                timestamp=ts,
            )
            _run(store.append(ev, user_id="u1"))

        # Should return all three in ascending timestamp order
        events = _run(store.get_oldest_unprocessed("u1"))
        assert len(events) == 3
        texts = [e["payload"]["text"] for e in events]
        assert texts == ["msg 0", "msg 1", "msg 2"]


def test_event_store_get_oldest_unprocessed_before_filter():
    """get_oldest_unprocessed respects the 'before' timestamp bound."""
    from datetime import datetime, timezone, timedelta

    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        for i in range(5):
            ts = (base + timedelta(hours=i)).isoformat()
            ev = Event(
                name="journal.appended",
                payload={"user_id": "u1", "text": f"msg {i}"},
                timestamp=ts,
            )
            _run(store.append(ev, user_id="u1"))

        cutoff = (base + timedelta(hours=3)).isoformat()  # exclude hours 3 and 4
        events = _run(store.get_oldest_unprocessed("u1", before=cutoff))
        assert len(events) == 3
        texts = [e["payload"]["text"] for e in events]
        assert texts == ["msg 0", "msg 1", "msg 2"]


def test_event_store_limit():
    """list_events respects the limit parameter."""
    with tempfile.TemporaryDirectory() as tmp:
        store = EventStore(os.path.join(tmp, "events.db"))
        for i in range(10):
            _run(store.append(_make_event(user_id="u1", text=f"msg {i}"), user_id="u1"))

        events = _run(store.list_events("u1", limit=3))
        assert len(events) == 3
