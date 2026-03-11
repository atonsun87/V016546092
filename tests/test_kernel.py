"""Tests for core.kernel — RawSignal, DerivedBelief, BeliefStore, MemoryScope."""

from __future__ import annotations

import pytest

from core.graph.storage import GraphStorage
from core.kernel import BeliefStore, DerivedBelief, MemoryScope, RawSignal

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    db_file = tmp_path / "test_kernel.db"
    return str(db_file)


@pytest.fixture()
def storage(tmp_db):
    return GraphStorage(tmp_db)


# ---------------------------------------------------------------------------
# RawSignal
# ---------------------------------------------------------------------------


class TestRawSignal:
    def test_basic_creation(self):
        sig = RawSignal(user_id="u1", source="telegram_message", content="I feel tired today.")
        assert sig.user_id == "u1"
        assert sig.source == "telegram_message"
        assert sig.content == "I feel tired today."
        assert sig.id  # UUID auto-generated
        assert sig.recorded_at  # ISO timestamp auto-set

    def test_immutable(self):
        sig = RawSignal(user_id="u1", source="journal", content="day was hard")
        with pytest.raises((AttributeError, TypeError)):
            sig.content = "changed"  # type: ignore[misc]

    def test_explicit_id(self):
        sig = RawSignal(user_id="u1", source="s", content="c", id="fixed-id")
        assert sig.id == "fixed-id"

    def test_metadata_defaults_empty(self):
        sig = RawSignal(user_id="u1", source="s", content="c")
        assert sig.metadata == {}

    def test_metadata_carried(self):
        sig = RawSignal(user_id="u1", source="s", content="c", metadata={"channel": "tg"})
        assert sig.metadata["channel"] == "tg"

    def test_different_signals_have_different_ids(self):
        s1 = RawSignal(user_id="u1", source="s", content="a")
        s2 = RawSignal(user_id="u1", source="s", content="b")
        assert s1.id != s2.id


# ---------------------------------------------------------------------------
# DerivedBelief
# ---------------------------------------------------------------------------


class TestDerivedBelief:
    def test_basic_creation(self):
        b = DerivedBelief(user_id="u1", statement="I am an introvert.")
        assert b.user_id == "u1"
        assert b.statement == "I am an introvert."
        assert b.confidence == 1.0
        assert b.id
        assert b.revision_history == []
        assert b.source_signal_ids == []

    def test_confidence_clamped_on_revise(self):
        b = DerivedBelief(user_id="u1", statement="old statement")
        b.revise("new statement", new_confidence=1.5)  # over 1.0 → clamped
        assert b.confidence == 1.0

    def test_confidence_clamped_negative_on_revise(self):
        b = DerivedBelief(user_id="u1", statement="old statement")
        b.revise("new statement", new_confidence=-0.5)  # under 0.0 → clamped
        assert b.confidence == 0.0

    def test_revise_records_history(self):
        b = DerivedBelief(user_id="u1", statement="I enjoy solitude.", confidence=0.9)
        b.revise("I sometimes enjoy company too.", new_confidence=0.6, reason="contradicted by signal-42")
        assert len(b.revision_history) == 1
        record = b.revision_history[0]
        assert record["statement"] == "I enjoy solitude."
        assert record["confidence"] == 0.9
        assert record["reason"] == "contradicted by signal-42"

    def test_revise_multiple_times_appends_history(self):
        b = DerivedBelief(user_id="u1", statement="v0", confidence=1.0)
        b.revise("v1", 0.8)
        b.revise("v2", 0.6)
        assert len(b.revision_history) == 2
        assert b.revision_history[0]["statement"] == "v0"
        assert b.revision_history[1]["statement"] == "v1"
        assert b.statement == "v2"

    def test_revise_updates_updated_at(self):
        b = DerivedBelief(user_id="u1", statement="initial")
        original_ts = b.updated_at
        b.revise("revised", 0.7)
        # updated_at must be a valid ISO-8601 string that is ≥ original
        assert b.updated_at >= original_ts

    def test_source_signal_ids(self):
        b = DerivedBelief(user_id="u1", statement="x", source_signal_ids=["sig-1", "sig-2"])
        assert b.source_signal_ids == ["sig-1", "sig-2"]


# ---------------------------------------------------------------------------
# BeliefStore
# ---------------------------------------------------------------------------


class TestBeliefStore:
    @pytest.mark.asyncio
    async def test_save_and_get(self, storage):
        store = BeliefStore(storage)
        belief = DerivedBelief(user_id="u1", statement="I value autonomy.", confidence=0.85)
        saved = await store.save(belief)
        assert saved.graph_node_id is not None

        fetched = await store.get("u1", belief.id)
        assert fetched is not None
        assert fetched.statement == "I value autonomy."
        assert abs(fetched.confidence - 0.85) < 1e-6

    @pytest.mark.asyncio
    async def test_get_returns_none_if_missing(self, storage):
        store = BeliefStore(storage)
        result = await store.get("u1", "nonexistent-belief-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_revise_persists_history(self, storage):
        store = BeliefStore(storage)
        belief = DerivedBelief(user_id="u1", statement="I am shy.", confidence=0.9)
        await store.save(belief)

        revised = await store.revise(belief, "I can be outgoing in some contexts.", 0.6, reason="new evidence")
        assert revised.statement == "I can be outgoing in some contexts."
        assert abs(revised.confidence - 0.6) < 1e-6

        fetched = await store.get("u1", belief.id)
        assert fetched is not None
        assert fetched.statement == "I can be outgoing in some contexts."
        assert len(fetched.revision_history) == 1
        assert fetched.revision_history[0]["statement"] == "I am shy."
        assert fetched.revision_history[0]["reason"] == "new evidence"

    @pytest.mark.asyncio
    async def test_list_beliefs_filtered_by_confidence(self, storage):
        store = BeliefStore(storage)
        for stmt, conf in [
            ("high conf", 0.9),
            ("mid conf", 0.5),
            ("low conf", 0.1),
        ]:
            await store.save(DerivedBelief(user_id="u1", statement=stmt, confidence=conf))

        high = await store.list_beliefs("u1", min_confidence=0.8)
        assert len(high) == 1
        assert high[0].statement == "high conf"

        mid_and_above = await store.list_beliefs("u1", min_confidence=0.4)
        assert len(mid_and_above) == 2

    @pytest.mark.asyncio
    async def test_list_beliefs_sorted_by_confidence(self, storage):
        store = BeliefStore(storage)
        for stmt, conf in [("b", 0.5), ("a", 0.9), ("c", 0.3)]:
            await store.save(DerivedBelief(user_id="u1", statement=stmt, confidence=conf))

        beliefs = await store.list_beliefs("u1")
        confidences = [b.confidence for b in beliefs]
        assert confidences == sorted(confidences, reverse=True)

    @pytest.mark.asyncio
    async def test_save_preserves_source_signal_ids(self, storage):
        store = BeliefStore(storage)
        belief = DerivedBelief(
            user_id="u1",
            statement="I prefer mornings.",
            source_signal_ids=["sig-abc", "sig-xyz"],
        )
        await store.save(belief)
        fetched = await store.get("u1", belief.id)
        assert fetched is not None
        assert set(fetched.source_signal_ids) == {"sig-abc", "sig-xyz"}

    @pytest.mark.asyncio
    async def test_save_idempotent(self, storage):
        store = BeliefStore(storage)
        belief = DerivedBelief(user_id="u1", statement="I like coffee.")
        await store.save(belief)
        await store.save(belief)  # second save should not duplicate
        beliefs = await store.list_beliefs("u1")
        coffee_beliefs = [b for b in beliefs if "coffee" in b.statement]
        assert len(coffee_beliefs) == 1


# ---------------------------------------------------------------------------
# MemoryScope
# ---------------------------------------------------------------------------


class TestMemoryScope:
    @pytest.mark.asyncio
    async def test_unrestricted_scope_allows_all(self, storage):
        scope = MemoryScope(storage=storage, user_id="u1", label="privileged")
        # allowed_types=None means unrestricted
        assert scope.permits("BELIEF")
        assert scope.permits("EMOTION")
        assert scope.permits("NOTE")

    @pytest.mark.asyncio
    async def test_restricted_scope_blocks_disallowed_types(self, storage):
        scope = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF", "VALUE"}),
            label="belief_only",
        )
        assert scope.permits("BELIEF")
        assert scope.permits("VALUE")
        assert not scope.permits("EMOTION")
        assert not scope.permits("NOTE")

    @pytest.mark.asyncio
    async def test_find_raises_on_disallowed_type(self, storage):
        scope = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF"}),
            label="belief_only",
        )
        with pytest.raises(PermissionError, match="does not permit"):
            await scope.find("EMOTION")

    @pytest.mark.asyncio
    async def test_find_returns_matching_nodes(self, storage):
        store = BeliefStore(storage)
        b = DerivedBelief(user_id="u1", statement="I am creative.")
        await store.save(b)

        scope = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF"}),
            label="test_scope",
        )
        nodes = await scope.find("BELIEF")
        assert any(n.text == "I am creative." for n in nodes)

    @pytest.mark.asyncio
    async def test_find_many_applies_policy(self, storage):
        scope = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF", "VALUE"}),
            label="limited",
        )
        results = await scope.find_many(["BELIEF", "VALUE"])
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_find_many_raises_on_disallowed(self, storage):
        scope = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF"}),
            label="limited",
        )
        with pytest.raises(PermissionError):
            await scope.find_many(["BELIEF", "EMOTION"])

    def test_narrow_restricts_allowed_types(self, storage):
        parent = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF", "VALUE", "EMOTION"}),
            label="wide",
        )
        narrowed = parent.narrow(frozenset({"BELIEF", "VALUE"}), label="narrow")
        assert narrowed.permits("BELIEF")
        assert narrowed.permits("VALUE")
        assert not narrowed.permits("EMOTION")

    def test_narrow_cannot_expand_unrestricted_parent(self, storage):
        parent = MemoryScope(storage=storage, user_id="u1", label="unrestricted")
        narrowed = parent.narrow(frozenset({"BELIEF"}))
        assert narrowed.permits("BELIEF")
        assert not narrowed.permits("EMOTION")

    def test_narrow_intersection_of_disjoint_is_empty(self, storage):
        parent = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF"}),
            label="a",
        )
        narrowed = parent.narrow(frozenset({"EMOTION"}))
        assert not narrowed.permits("BELIEF")
        assert not narrowed.permits("EMOTION")

    def test_narrow_preserves_parent_scope(self, storage):
        parent = MemoryScope(
            storage=storage,
            user_id="u1",
            allowed_types=frozenset({"BELIEF", "VALUE"}),
            label="parent",
        )
        parent.narrow(frozenset({"BELIEF"}))
        # parent scope should be unchanged
        assert parent.permits("VALUE")
