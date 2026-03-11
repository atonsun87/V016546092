"""Tests for core.memory.event_record — EventRecord and EventRecordStore."""

import asyncio

from core.memory.event_record import EventRecord, EventRecordStore

# ---------------------------------------------------------------------------
# EventRecord dataclass
# ---------------------------------------------------------------------------


def test_event_record_defaults():
    rec = EventRecord(user_id="u1", text="hello")
    assert rec.id == 0
    assert rec.intent == "UNKNOWN"
    assert rec.source == "cli"
    assert rec.processed_offline is False
    assert isinstance(rec.raw_signals, dict)
    assert rec.timestamp  # not empty


def test_event_record_to_dict():
    rec = EventRecord(
        user_id="u1",
        text="test",
        intent="FEELING_REPORT",
        raw_signals={"valence": 0.5},
    )
    d = rec.to_dict()
    assert d["user_id"] == "u1"
    assert d["intent"] == "FEELING_REPORT"
    assert d["raw_signals"]["valence"] == 0.5
    assert d["processed_offline"] is False


# ---------------------------------------------------------------------------
# EventRecordStore persistence
# ---------------------------------------------------------------------------


def test_store_append_assigns_id(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        rec = EventRecord(user_id="u1", text="hi", intent="REFLECTION")
        saved = await store.append(rec)
        assert saved.id > 0
        return saved.id

    rid = asyncio.run(run())
    assert rid > 0


def test_store_list_pending_returns_unprocessed(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        for i in range(3):
            await store.append(EventRecord(user_id="u1", text=f"msg {i}"))
        pending = await store.list_pending("u1")
        assert len(pending) == 3
        for rec in pending:
            assert rec.processed_offline is False

    asyncio.run(run())


def test_store_mark_processed_removes_from_pending(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        rec = await store.append(EventRecord(user_id="u1", text="done"))
        await store.mark_processed(rec.id)
        pending = await store.list_pending("u1")
        assert all(r.id != rec.id for r in pending)

    asyncio.run(run())


def test_store_count_pending(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        for _ in range(5):
            await store.append(EventRecord(user_id="u1", text="x"))
        # mark 2 processed
        pending = await store.list_pending("u1", limit=2)
        for rec in pending:
            await store.mark_processed(rec.id)
        count = await store.count_pending("u1")
        assert count == 3

    asyncio.run(run())


def test_store_user_isolation(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        await store.append(EventRecord(user_id="alice", text="for alice"))
        await store.append(EventRecord(user_id="bob", text="for bob"))
        alice_pending = await store.list_pending("alice")
        bob_pending = await store.list_pending("bob")
        assert len(alice_pending) == 1
        assert len(bob_pending) == 1
        assert alice_pending[0].user_id == "alice"
        assert bob_pending[0].user_id == "bob"

    asyncio.run(run())


def test_store_raw_signals_roundtrip(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        signals = {"valence": 0.8, "arousal": 0.3, "keywords": ["love", "work"]}
        await store.append(
            EventRecord(user_id="u1", text="signals test", raw_signals=signals)
        )
        recent = await store.list_recent("u1", limit=1)
        assert recent[0].raw_signals["valence"] == 0.8
        assert recent[0].raw_signals["keywords"] == ["love", "work"]

    asyncio.run(run())


def test_store_list_recent_order(tmp_path):
    async def run():
        store = EventRecordStore(tmp_path / "test.db")
        for i in range(4):
            await store.append(EventRecord(user_id="u1", text=f"msg {i}"))
        recent = await store.list_recent("u1", limit=4)
        # list_recent returns newest first
        ids = [r.id for r in recent]
        assert ids == sorted(ids, reverse=True)

    asyncio.run(run())
