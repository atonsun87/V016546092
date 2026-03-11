"""Tests for core.offline.pipeline — OfflineConsolidationPipeline."""

import asyncio

from core.beliefs.schema import BeliefRecord
from core.beliefs.store import BeliefStore
from core.graph.api import GraphAPI
from core.graph.storage import GraphStorage
from core.memory.event_record import EventRecord, EventRecordStore
from core.offline.pipeline import OfflineConsolidationPipeline, OfflineProcessingReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_pipeline(tmp_path) -> tuple[OfflineConsolidationPipeline, EventRecordStore, BeliefStore]:
    storage = GraphStorage(tmp_path / "test.db")
    await storage._ensure_initialized()
    graph_api = GraphAPI(storage)
    event_store = EventRecordStore(tmp_path / "test.db")
    belief_store = BeliefStore(tmp_path / "test.db")
    pipeline = OfflineConsolidationPipeline(
        graph_api=graph_api,
        event_store=event_store,
        belief_store=belief_store,
    )
    return pipeline, event_store, belief_store


# ---------------------------------------------------------------------------
# Report structure
# ---------------------------------------------------------------------------


def test_report_finalise():
    report = OfflineProcessingReport(user_id="u1")
    assert report.finished_at == ""
    report.finalise()
    assert report.finished_at != ""


def test_report_to_dict():
    report = OfflineProcessingReport(user_id="u1")
    report.events_processed = 5
    report.finalise()
    d = report.to_dict()
    assert d["user_id"] == "u1"
    assert d["events_processed"] == 5
    assert d["finished_at"]


# ---------------------------------------------------------------------------
# Empty run (no pending events)
# ---------------------------------------------------------------------------


def test_pipeline_no_pending_events(tmp_path):
    async def run():
        pipeline, _, _ = await _make_pipeline(tmp_path)
        report = await pipeline.process_pending_events("u1")
        assert report.events_processed == 0
        assert report.finished_at != ""
        assert report.errors == []

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Feeling events trigger belief creation/update
# ---------------------------------------------------------------------------


def test_pipeline_positive_feeling_creates_efficacy_belief(tmp_path):
    async def run():
        pipeline, event_store, belief_store = await _make_pipeline(tmp_path)
        await event_store.append(EventRecord(
            user_id="u1",
            text="I aced the presentation!",
            intent="FEELING_REPORT",
            raw_signals={"valence": 0.8, "arousal": 0.5},
        ))
        report = await pipeline.process_pending_events("u1")
        assert report.events_processed == 1
        # Belief should be created
        assert report.beliefs_created >= 1
        belief = await belief_store.get("u1", "self:efficacy")
        assert belief is not None
        assert belief.confidence > 0.5

    asyncio.run(run())


def test_pipeline_negative_feeling_weakens_efficacy_belief(tmp_path):
    async def run():
        pipeline, event_store, belief_store = await _make_pipeline(tmp_path)
        # Pre-create belief
        initial = BeliefRecord(user_id="u1", key="self:efficacy", text="I can do it", confidence=0.7)
        await belief_store.upsert(initial)
        # Negative event
        await event_store.append(EventRecord(
            user_id="u1",
            text="failed again",
            intent="FEELING_REPORT",
            raw_signals={"valence": -0.6},
        ))
        report = await pipeline.process_pending_events("u1")
        assert report.events_processed == 1
        updated = await belief_store.get("u1", "self:efficacy")
        assert updated is not None
        assert updated.confidence < 0.7  # weakened

    asyncio.run(run())


def test_pipeline_positive_feeling_strengthens_existing_belief(tmp_path):
    async def run():
        pipeline, event_store, belief_store = await _make_pipeline(tmp_path)
        initial = BeliefRecord(user_id="u1", key="self:efficacy", text="I can do it", confidence=0.5)
        await belief_store.upsert(initial)
        await event_store.append(EventRecord(
            user_id="u1",
            text="great day",
            intent="FEELING_REPORT",
            raw_signals={"valence": 0.7},
        ))
        report = await pipeline.process_pending_events("u1")
        updated = await belief_store.get("u1", "self:efficacy")
        assert updated.confidence > 0.5
        assert report.beliefs_updated >= 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Events are marked processed after run
# ---------------------------------------------------------------------------


def test_pipeline_marks_events_processed(tmp_path):
    async def run():
        pipeline, event_store, _ = await _make_pipeline(tmp_path)
        for _ in range(3):
            await event_store.append(EventRecord(
                user_id="u1",
                text="hello",
                intent="REFLECTION",
            ))
        await pipeline.process_pending_events("u1")
        remaining = await event_store.count_pending("u1")
        assert remaining == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Second run on same user sees no pending events
# ---------------------------------------------------------------------------


def test_pipeline_idempotent_second_run(tmp_path):
    async def run():
        pipeline, event_store, _ = await _make_pipeline(tmp_path)
        await event_store.append(EventRecord(user_id="u1", text="once", intent="REFLECTION"))
        r1 = await pipeline.process_pending_events("u1")
        r2 = await pipeline.process_pending_events("u1")
        assert r1.events_processed == 1
        assert r2.events_processed == 0

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Neutral intent events (no belief changes expected)
# ---------------------------------------------------------------------------


def test_pipeline_neutral_intent_no_belief_change(tmp_path):
    async def run():
        pipeline, event_store, belief_store = await _make_pipeline(tmp_path)
        initial = BeliefRecord(user_id="u1", key="self:x", text="t", confidence=0.5)
        await belief_store.upsert(initial)
        await event_store.append(EventRecord(
            user_id="u1",
            text="what time is it?",
            intent="META",
            raw_signals={},
        ))
        await pipeline.process_pending_events("u1")
        unchanged = await belief_store.get("u1", "self:x")
        assert unchanged.confidence == 0.5

    asyncio.run(run())


# ---------------------------------------------------------------------------
# User isolation
# ---------------------------------------------------------------------------


def test_pipeline_user_isolation(tmp_path):
    async def run():
        pipeline, event_store, belief_store = await _make_pipeline(tmp_path)
        await event_store.append(EventRecord(
            user_id="alice",
            text="great",
            intent="FEELING_REPORT",
            raw_signals={"valence": 0.9},
        ))
        await event_store.append(EventRecord(
            user_id="bob",
            text="terrible",
            intent="FEELING_REPORT",
            raw_signals={"valence": -0.9},
        ))
        await pipeline.process_pending_events("alice")
        await pipeline.process_pending_events("bob")

        alice_belief = await belief_store.get("alice", "self:efficacy")
        bob_belief = await belief_store.get("bob", "self:efficacy")
        # Alice should have a belief; Bob should also have one but lower confidence
        assert alice_belief is not None
        # Bob's negative feeling should not have created a belief with high confidence
        if bob_belief is not None:
            assert bob_belief.confidence <= alice_belief.confidence

    asyncio.run(run())
