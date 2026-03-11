"""Tests for core.beliefs — BeliefRecord schema and BeliefStore persistence."""

import asyncio

from core.beliefs.schema import BeliefRecord
from core.beliefs.store import BeliefStore

# ---------------------------------------------------------------------------
# BeliefRecord schema
# ---------------------------------------------------------------------------


def test_belief_record_defaults():
    b = BeliefRecord(user_id="u1", text="I am capable")
    assert 0.0 <= b.confidence <= 1.0
    assert b.source == "inferred"
    assert b.revision_history == []
    assert b.evidence_refs == []


def test_belief_record_apply_evidence_increases_confidence():
    b = BeliefRecord(user_id="u1", text="I am capable", confidence=0.5)
    b.apply_evidence(confidence_delta=0.2, reason="positive event", evidence_ref="ev:1")
    assert abs(b.confidence - 0.7) < 1e-9
    assert len(b.revision_history) == 1
    assert b.revision_history[0].previous_confidence == 0.5
    assert b.revision_history[0].new_confidence == 0.7
    assert "ev:1" in b.evidence_refs


def test_belief_record_apply_evidence_clamps_to_zero():
    b = BeliefRecord(user_id="u1", text="something", confidence=0.1)
    b.apply_evidence(confidence_delta=-0.5, reason="strong contra-evidence")
    assert b.confidence == 0.0


def test_belief_record_apply_evidence_clamps_to_one():
    b = BeliefRecord(user_id="u1", text="something", confidence=0.9)
    b.apply_evidence(confidence_delta=0.5, reason="strong support")
    assert b.confidence == 1.0


def test_belief_record_multiple_revisions():
    b = BeliefRecord(user_id="u1", text="text", confidence=0.5)
    for i in range(3):
        b.apply_evidence(confidence_delta=0.1, reason=f"event {i}")
    assert len(b.revision_history) == 3
    assert abs(b.confidence - 0.8) < 1e-9


def test_belief_record_to_dict_roundtrip():
    b = BeliefRecord(
        user_id="u1",
        text="I am resilient",
        key="self:resilience",
        confidence=0.7,
        source="stated",
        domain="self",
        tags=["resilience", "self"],
        evidence_refs=["ev:1"],
    )
    b.apply_evidence(confidence_delta=0.05, reason="test")
    d = b.to_dict()
    b2 = BeliefRecord.from_dict(d)
    assert b2.id == b.id
    assert b2.key == b.key
    assert b2.confidence == b.confidence
    assert b2.source == b.source
    assert len(b2.revision_history) == 1


def test_belief_record_evidence_ref_no_duplicate():
    b = BeliefRecord(user_id="u1", text="t")
    b.apply_evidence(confidence_delta=0.1, reason="r", evidence_ref="ev:1")
    b.apply_evidence(confidence_delta=0.1, reason="r", evidence_ref="ev:1")
    assert b.evidence_refs.count("ev:1") == 1


# ---------------------------------------------------------------------------
# BeliefStore persistence
# ---------------------------------------------------------------------------


def test_belief_store_upsert_and_get(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        belief = BeliefRecord(
            user_id="u1",
            text="I am capable",
            key="self:efficacy",
            confidence=0.6,
        )
        await store.upsert(belief)
        retrieved = await store.get("u1", "self:efficacy")
        assert retrieved is not None
        assert retrieved.text == "I am capable"
        assert abs(retrieved.confidence - 0.6) < 1e-6

    asyncio.run(run())


def test_belief_store_upsert_updates_existing(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        belief = BeliefRecord(user_id="u1", text="original", key="self:test", confidence=0.5)
        await store.upsert(belief)
        belief.confidence = 0.8
        belief.text = "updated"
        await store.upsert(belief)
        retrieved = await store.get("u1", "self:test")
        assert retrieved is not None
        assert abs(retrieved.confidence - 0.8) < 1e-6
        assert retrieved.text == "updated"

    asyncio.run(run())


def test_belief_store_get_missing_returns_none(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        result = await store.get("u1", "nonexistent:key")
        assert result is None

    asyncio.run(run())


def test_belief_store_apply_evidence(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        belief = BeliefRecord(user_id="u1", text="t", key="self:x", confidence=0.5)
        await store.upsert(belief)
        updated = await store.apply_evidence(
            "u1", "self:x",
            confidence_delta=0.2,
            reason="test evidence",
            evidence_ref="ev:99",
        )
        assert updated is not None
        assert abs(updated.confidence - 0.7) < 1e-6
        assert len(updated.revision_history) == 1

    asyncio.run(run())


def test_belief_store_apply_evidence_on_missing_returns_none(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        result = await store.apply_evidence(
            "u1", "no:such:key",
            confidence_delta=0.1,
            reason="should return None",
        )
        assert result is None

    asyncio.run(run())


def test_belief_store_list_for_user(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        for i in range(5):
            await store.upsert(BeliefRecord(
                user_id="u1",
                text=f"belief {i}",
                key=f"self:belief{i}",
                confidence=0.3 + i * 0.1,
                domain="self" if i < 3 else "career",
            ))
        all_beliefs = await store.list_for_user("u1")
        assert len(all_beliefs) == 5
        # Ordered by confidence descending
        scores = [b.confidence for b in all_beliefs]
        assert scores == sorted(scores, reverse=True)

        career = await store.list_for_user("u1", domain="career")
        assert len(career) == 2

        high_conf = await store.list_for_user("u1", min_confidence=0.6)
        assert all(b.confidence >= 0.6 for b in high_conf)

    asyncio.run(run())


def test_belief_store_user_isolation(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        await store.upsert(BeliefRecord(user_id="alice", text="t", key="self:x"))
        await store.upsert(BeliefRecord(user_id="bob", text="t", key="self:x"))
        alice_beliefs = await store.list_for_user("alice")
        bob_beliefs = await store.list_for_user("bob")
        assert len(alice_beliefs) == 1
        assert len(bob_beliefs) == 1
        assert alice_beliefs[0].user_id == "alice"

    asyncio.run(run())


def test_belief_store_delete(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        belief = BeliefRecord(user_id="u1", text="to delete", key="self:delete")
        await store.upsert(belief)
        deleted = await store.delete("u1", "self:delete")
        assert deleted is True
        assert await store.get("u1", "self:delete") is None
        # Deleting again returns False
        assert await store.delete("u1", "self:delete") is False

    asyncio.run(run())


def test_belief_store_get_by_id(tmp_path):
    async def run():
        store = BeliefStore(tmp_path / "test.db")
        belief = BeliefRecord(user_id="u1", text="by id", key="self:byid")
        await store.upsert(belief)
        retrieved = await store.get_by_id(belief.id)
        assert retrieved is not None
        assert retrieved.id == belief.id

    asyncio.run(run())
