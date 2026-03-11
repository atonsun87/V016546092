"""Tests for core.beliefs.store — BeliefStore."""

from __future__ import annotations

from core.beliefs.model import RevisableBelief
from core.beliefs.store import BeliefStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(user_id: str = "u1") -> BeliefStore:
    return BeliefStore(user_id=user_id)


def _belief(user_id: str = "u1", confidence: float = 0.6, **kwargs) -> RevisableBelief:
    defaults = dict(content="I prefer deep focus work", topic="work/style")
    defaults.update(kwargs)
    return RevisableBelief.create(user_id=user_id, confidence=confidence, **defaults)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_add_and_get():
    store = _make_store()
    b = _belief()
    store.add(b)
    assert store.get(b.id) is b


def test_get_missing_returns_none():
    store = _make_store()
    assert store.get("nonexistent") is None


def test_remove_existing():
    store = _make_store()
    b = _belief()
    store.add(b)
    assert store.remove(b.id) is True
    assert store.get(b.id) is None


def test_remove_missing_returns_false():
    store = _make_store()
    assert store.remove("ghost-id") is False


def test_count():
    store = _make_store()
    store.add(_belief())
    store.add(_belief())
    assert store.count() == 2


def test_add_wrong_user_raises():
    store = _make_store(user_id="u1")
    b = _belief(user_id="u2")
    try:
        store.add(b)
        assert False, "Expected ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_uncertain_returns_low_confidence_beliefs():
    store = _make_store()
    store.add(_belief(confidence=0.1))  # uncertain
    store.add(_belief(confidence=0.9))  # strong
    uncertain = store.uncertain()
    assert len(uncertain) == 1
    assert uncertain[0].confidence == 0.1


def test_strong_returns_high_confidence_beliefs():
    store = _make_store()
    store.add(_belief(confidence=0.2))  # uncertain
    store.add(_belief(confidence=0.9))  # strong
    strong = store.strong()
    assert len(strong) == 1


def test_contradicted_returns_beliefs_with_contradictory_evidence():
    store = _make_store()
    b = _belief()
    b.revise(0.4, contradictory_event_ids=["cx"])
    store.add(b)
    store.add(_belief(confidence=0.8))  # not contradicted
    assert len(store.contradicted()) == 1


def test_by_topic_filters_correctly():
    store = _make_store()
    store.add(_belief(topic="work/style"))
    store.add(_belief(topic="values/autonomy"))
    result = store.by_topic("work")
    assert len(result) == 1
    assert result[0].topic == "work/style"


def test_user_confirmed_beliefs():
    store = _make_store()
    b = _belief(confidence=0.1)
    store.add(b)
    store.confirm_by_user(b.id)
    confirmed = store.user_confirmed_beliefs()
    assert len(confirmed) == 1


def test_user_rejected_beliefs():
    store = _make_store()
    b = _belief()
    store.add(b)
    store.reject_by_user(b.id)
    rejected = store.user_rejected_beliefs()
    assert len(rejected) == 1


def test_for_agent_context_excludes_rejected_and_uncertain():
    store = _make_store()

    strong = _belief(confidence=0.85)
    uncertain = _belief(confidence=0.1)
    rejected = _belief(confidence=0.7)
    contradicted = _belief(confidence=0.5)
    contradicted.revise(0.45, contradictory_event_ids=["cx"])

    store.add(strong)
    store.add(uncertain)
    store.add(rejected)
    store.add(contradicted)

    store.reject_by_user(rejected.id)

    context_beliefs = store.for_agent_context()
    ids = {b.id for b in context_beliefs}

    assert strong.id in ids
    assert uncertain.id not in ids
    assert rejected.id not in ids
    assert contradicted.id not in ids


def test_user_confirmed_belief_appears_in_agent_context():
    store = _make_store()
    b = _belief(confidence=0.1)  # normally uncertain
    store.add(b)
    store.confirm_by_user(b.id)
    assert b.id in {x.id for x in store.for_agent_context()}


# ---------------------------------------------------------------------------
# Revision helpers
# ---------------------------------------------------------------------------


def test_revise_updates_store_belief():
    store = _make_store()
    b = _belief(confidence=0.7)
    store.add(b)
    entry = store.revise(b.id, new_confidence=0.3, reason="contradicted")
    assert entry is not None
    assert store.get(b.id).confidence == 0.3


def test_revise_missing_belief_returns_none():
    store = _make_store()
    result = store.revise("nonexistent", 0.5)
    assert result is None


def test_on_revision_callback_is_called():
    calls = []

    def callback(belief, entry):
        calls.append((belief.id, entry.new_confidence))

    store = BeliefStore(user_id="u1", on_revision=callback)
    b = _belief()
    store.add(b)
    store.revise(b.id, 0.2, reason="test")
    assert len(calls) == 1
    assert calls[0][1] == 0.2


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_to_list_from_list_roundtrip():
    store = _make_store()
    b1 = _belief(confidence=0.7)
    b2 = _belief(confidence=0.3)
    store.add(b1)
    store.add(b2)
    store.revise(b1.id, 0.5, reason="revised")

    data = store.to_list()
    restored = BeliefStore.from_list(user_id="u1", data=data)

    assert restored.count() == 2
    restored_b1 = restored.get(b1.id)
    assert restored_b1 is not None
    assert restored_b1.revision_count == 1


def test_all_sorted_by_confidence_desc():
    store = _make_store()
    store.add(_belief(confidence=0.3))
    store.add(_belief(confidence=0.9))
    store.add(_belief(confidence=0.5))
    all_beliefs = store.all()
    confidences = [b.confidence for b in all_beliefs]
    assert confidences == sorted(confidences, reverse=True)
