"""Tests for core.beliefs.model — RevisableBelief."""

from __future__ import annotations

import pytest

from core.beliefs.model import (
    STRONG_THRESHOLD,
    UNCERTAIN_THRESHOLD,
    RevisableBelief,
    RevisionEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_belief(confidence: float = 0.6, **kwargs) -> RevisableBelief:
    defaults = dict(
        user_id="u1",
        content="I prefer working alone on deep focus tasks",
        source_event_ids=["evt-001"],
        topic="work/style",
    )
    defaults.update(kwargs)
    return RevisableBelief.create(confidence=confidence, **defaults)


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_create_returns_belief_with_id():
    belief = _make_belief()
    assert belief.id != ""
    assert belief.user_id == "u1"
    assert belief.confidence == 0.6


def test_create_clamps_confidence():
    b_high = _make_belief(confidence=1.5)
    assert b_high.confidence == 1.0

    b_low = _make_belief(confidence=-0.3)
    assert b_low.confidence == 0.0


def test_create_without_source_events():
    belief = RevisableBelief.create(user_id="u1", content="test", confidence=0.5)
    assert belief.source_event_ids == []


# ---------------------------------------------------------------------------
# Status properties
# ---------------------------------------------------------------------------


def test_is_uncertain_below_threshold():
    belief = _make_belief(confidence=UNCERTAIN_THRESHOLD - 0.01)
    assert belief.is_uncertain is True


def test_is_uncertain_above_threshold():
    belief = _make_belief(confidence=UNCERTAIN_THRESHOLD + 0.01)
    assert belief.is_uncertain is False


def test_is_strong_above_threshold():
    belief = _make_belief(confidence=STRONG_THRESHOLD + 0.01)
    assert belief.is_strong is True


def test_is_strong_below_threshold():
    belief = _make_belief(confidence=STRONG_THRESHOLD - 0.01)
    assert belief.is_strong is False


def test_user_confirmed_overrides_uncertainty():
    belief = _make_belief(confidence=0.1)
    assert belief.is_uncertain is True
    belief.confirm_by_user()
    assert belief.is_uncertain is False
    assert belief.is_strong is True


def test_is_contradicted_when_has_contradictory_events():
    belief = _make_belief()
    assert belief.is_contradicted is False
    belief.revise(
        new_confidence=0.4,
        contradictory_event_ids=["evt-contra-1"],
    )
    assert belief.is_contradicted is True


# ---------------------------------------------------------------------------
# Status string
# ---------------------------------------------------------------------------


def test_status_user_confirmed():
    belief = _make_belief(confidence=0.2)
    belief.confirm_by_user()
    assert belief.status == "user_confirmed"


def test_status_user_rejected():
    belief = _make_belief()
    belief.reject_by_user()
    assert belief.status == "user_rejected"


def test_status_strong():
    belief = _make_belief(confidence=0.85)
    assert belief.status == "strong"


def test_status_uncertain():
    belief = _make_belief(confidence=0.15)
    assert belief.status == "uncertain"


def test_status_contradicted():
    belief = _make_belief(confidence=0.5)
    belief.revise(0.4, contradictory_event_ids=["e-c"])
    assert belief.status == "contradicted"


# ---------------------------------------------------------------------------
# revise()
# ---------------------------------------------------------------------------


def test_revise_updates_confidence():
    belief = _make_belief(confidence=0.7)
    entry = belief.revise(new_confidence=0.4, reason="Contradicted")
    assert belief.confidence == 0.4
    assert isinstance(entry, RevisionEntry)
    assert entry.old_confidence == 0.7
    assert entry.new_confidence == 0.4


def test_revise_logs_history():
    belief = _make_belief(confidence=0.7)
    belief.revise(0.5, reason="first revision")
    belief.revise(0.3, reason="second revision")
    assert belief.revision_count == 2
    assert len(belief.revision_history) == 2
    assert belief.revision_history[0].reason == "first revision"


def test_revise_appends_contradictory_evidence():
    belief = _make_belief()
    belief.revise(0.4, contradictory_event_ids=["c1", "c2"])
    belief.revise(0.3, contradictory_event_ids=["c3"])
    assert "c1" in belief.contradictory_event_ids
    assert "c3" in belief.contradictory_event_ids


def test_revise_appends_supporting_evidence():
    belief = _make_belief(source_event_ids=["e1"])
    belief.revise(0.8, supporting_event_ids=["e2"])
    assert "e1" in belief.source_event_ids
    assert "e2" in belief.source_event_ids


def test_revise_clamps_confidence():
    belief = _make_belief(confidence=0.5)
    entry = belief.revise(new_confidence=2.0)
    assert belief.confidence == 1.0
    assert entry.new_confidence == 1.0


def test_revise_revised_by_tracks_actor():
    belief = _make_belief()
    entry = belief.revise(0.3, revised_by="reflection_agent")
    assert entry.revised_by == "reflection_agent"


# ---------------------------------------------------------------------------
# confirm / reject
# ---------------------------------------------------------------------------


def test_confirm_by_user_sets_flag():
    belief = _make_belief(confidence=0.1)
    belief.confirm_by_user()
    assert belief.user_confirmed is True


def test_reject_by_user_sets_confidence_to_zero():
    belief = _make_belief(confidence=0.8)
    belief.reject_by_user()
    assert belief.user_confirmed is False
    assert belief.confidence == 0.0


def test_reject_by_user_logs_correct_old_confidence():
    """old_confidence in the revision entry must reflect the pre-rejection value."""
    belief = _make_belief(confidence=0.75)
    belief.reject_by_user()
    entry = belief.revision_history[-1]
    assert entry.old_confidence == 0.75
    assert entry.new_confidence == 0.0


def test_reject_by_user_logs_history():
    belief = _make_belief()
    belief.reject_by_user()
    assert belief.revision_count == 1
    assert "Rejected" in belief.revision_history[-1].reason


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_roundtrip():
    belief = _make_belief(confidence=0.65)
    belief.revise(0.4, reason="test", contradictory_event_ids=["cx"])
    belief.confirm_by_user()

    data = belief.to_dict()
    restored = RevisableBelief.from_dict(data)

    assert restored.id == belief.id
    assert restored.user_id == belief.user_id
    assert restored.confidence == belief.confidence
    assert restored.user_confirmed == belief.user_confirmed
    assert restored.revision_count == belief.revision_count
    assert len(restored.revision_history) == len(belief.revision_history)
    assert "cx" in restored.contradictory_event_ids


def test_snapshot_is_independent_copy():
    belief = _make_belief(confidence=0.6)
    snap = belief.snapshot()
    belief.revise(0.2)
    assert snap.confidence == 0.6  # snapshot not affected


def test_status_in_to_dict():
    belief = _make_belief(confidence=0.9)
    data = belief.to_dict()
    assert data["status"] == "strong"
