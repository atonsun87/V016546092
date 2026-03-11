"""Tests for core.retrieval.view — MemoryView, MemoryViewPolicy, MemoryViewBuilder."""

import pytest

from core.retrieval.models import RetrievalCandidate, RetrievalScoreBreakdown
from core.retrieval.view import (
    MemoryView,
    MemoryViewBuilder,
    MemoryViewItem,
    MemoryViewPolicy,
)

# ---------------------------------------------------------------------------
# MemoryViewPolicy
# ---------------------------------------------------------------------------


def test_policy_default_scope():
    policy = MemoryViewPolicy()
    assert policy.scope == "agent"


def test_policy_invalid_scope_raises():
    with pytest.raises(ValueError, match="Unknown scope"):
        MemoryViewPolicy(scope="hacker")


def test_policy_offline_worker_scope_overrides_limits():
    policy = MemoryViewPolicy(scope="offline_worker")
    assert policy.min_confidence == 0.0
    assert policy.max_results >= 10_000
    assert policy.include_revision_history is True


def test_policy_user_reflection_scope_accepted():
    policy = MemoryViewPolicy(scope="user_reflection")
    assert policy.scope == "user_reflection"


# ---------------------------------------------------------------------------
# MemoryViewBuilder.build
# ---------------------------------------------------------------------------

def _make_candidate(
    memory_id: str,
    content: str,
    confidence: float = 1.0,
    memory_type: str = "NOTE",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        memory_id=memory_id,
        memory_type=memory_type,
        content=content,
        confidence=confidence,
    )


def _make_scored(final_score: float = 0.5) -> RetrievalScoreBreakdown:
    return RetrievalScoreBreakdown(final_score=final_score)


def test_build_returns_memory_view():
    candidates = [_make_candidate("c1", "text 1")]
    scored = [_make_scored(0.7)]
    policy = MemoryViewPolicy(scope="agent")
    view = MemoryViewBuilder.build(candidates, scored, policy)
    assert isinstance(view, MemoryView)
    assert len(view) == 1
    assert view.items[0].memory_id == "c1"


def test_build_filters_low_confidence():
    candidates = [
        _make_candidate("high", "good", confidence=0.9),
        _make_candidate("low", "bad", confidence=0.1),
    ]
    scored = [_make_scored(0.8), _make_scored(0.6)]
    policy = MemoryViewPolicy(scope="agent", min_confidence=0.4)
    view = MemoryViewBuilder.build(candidates, scored, policy)
    assert len(view) == 1
    assert view.items[0].memory_id == "high"
    assert view.filtered_count == 1


def test_build_offline_worker_sees_all():
    candidates = [
        _make_candidate("c1", "text 1", confidence=0.0),
        _make_candidate("c2", "text 2", confidence=0.1),
    ]
    scored = [_make_scored(0.3), _make_scored(0.4)]
    policy = MemoryViewPolicy(scope="offline_worker")
    view = MemoryViewBuilder.build(candidates, scored, policy)
    assert len(view) == 2
    assert view.filtered_count == 0


def test_build_sorts_by_final_score_descending():
    candidates = [
        _make_candidate("low", "low", confidence=1.0),
        _make_candidate("high", "high", confidence=1.0),
        _make_candidate("mid", "mid", confidence=1.0),
    ]
    scored = [_make_scored(0.2), _make_scored(0.9), _make_scored(0.5)]
    policy = MemoryViewPolicy(scope="agent")
    view = MemoryViewBuilder.build(candidates, scored, policy)
    scores = [item.final_score for item in view.items]
    assert scores == sorted(scores, reverse=True)
    assert view.items[0].memory_id == "high"


def test_build_caps_to_max_results():
    candidates = [_make_candidate(f"c{i}", f"text {i}") for i in range(20)]
    scored = [_make_scored(0.5) for _ in range(20)]
    policy = MemoryViewPolicy(scope="agent", max_results=5)
    view = MemoryViewBuilder.build(candidates, scored, policy)
    assert len(view) == 5
    assert view.total_candidates == 20


def test_build_handles_score_length_mismatch():
    candidates = [_make_candidate("c1", "t1"), _make_candidate("c2", "t2")]
    scored: list[RetrievalScoreBreakdown] = []  # empty — should not crash
    policy = MemoryViewPolicy(scope="agent")
    view = MemoryViewBuilder.build(candidates, scored, policy)
    assert len(view) == 2


def test_build_total_candidates_count():
    candidates = [_make_candidate(f"c{i}", f"text {i}", confidence=0.5 + i * 0.1) for i in range(5)]
    scored = [_make_scored(0.5) for _ in range(5)]
    policy = MemoryViewPolicy(scope="agent", min_confidence=0.7)
    view = MemoryViewBuilder.build(candidates, scored, policy)
    assert view.total_candidates == 5
    assert view.filtered_count + len(view) == 5


def test_build_includes_revision_history_for_offline_worker():
    cand = _make_candidate("c1", "text")
    cand.raw_payload = {"revision_history": [{"reason": "old event"}]}
    scored = [_make_scored(0.5)]
    policy = MemoryViewPolicy(scope="offline_worker")
    view = MemoryViewBuilder.build([cand], scored, policy)
    assert view.items[0].raw_payload.get("revision_history") is not None


def test_build_agent_scope_no_revision_history():
    cand = _make_candidate("c1", "text")
    cand.raw_payload = {"revision_history": [{"reason": "old event"}]}
    scored = [_make_scored(0.5)]
    policy = MemoryViewPolicy(scope="agent")
    view = MemoryViewBuilder.build([cand], scored, policy)
    assert view.items[0].raw_payload == {}


# ---------------------------------------------------------------------------
# MemoryViewBuilder.build_from_nodes
# ---------------------------------------------------------------------------


def test_build_from_nodes_basic():
    nodes = [
        {"id": "n1", "type": "BELIEF", "text": "I am strong", "metadata": {"salience_score": 0.9}},
        {"id": "n2", "type": "NOTE", "text": "Meeting at 3pm", "metadata": {"salience_score": 0.2}},
    ]
    policy = MemoryViewPolicy(scope="agent", min_confidence=0.5)
    view = MemoryViewBuilder.build_from_nodes(nodes, policy)
    assert len(view) == 1
    assert view.items[0].memory_id == "n1"


def test_build_from_nodes_empty_list():
    policy = MemoryViewPolicy(scope="agent")
    view = MemoryViewBuilder.build_from_nodes([], policy)
    assert len(view) == 0
    assert view.total_candidates == 0


# ---------------------------------------------------------------------------
# MemoryView helpers
# ---------------------------------------------------------------------------


def test_memory_view_top():
    candidates = [_make_candidate(f"c{i}", f"text {i}") for i in range(10)]
    scored = [_make_scored(float(i) / 10) for i in range(10)]
    policy = MemoryViewPolicy(scope="agent", max_results=10)
    view = MemoryViewBuilder.build(candidates, scored, policy)
    top3 = view.top(3)
    assert len(top3) == 3


def test_memory_view_iter():
    candidates = [_make_candidate("c1", "text")]
    scored = [_make_scored(0.5)]
    policy = MemoryViewPolicy(scope="agent")
    view = MemoryViewBuilder.build(candidates, scored, policy)
    items = list(view)
    assert len(items) == 1
    assert isinstance(items[0], MemoryViewItem)


def test_memory_view_to_dict():
    candidates = [_make_candidate("c1", "text")]
    scored = [_make_scored(0.7)]
    policy = MemoryViewPolicy(scope="agent", reason="test retrieval")
    view = MemoryViewBuilder.build(candidates, scored, policy)
    d = view.to_dict()
    assert d["scope"] == "agent"
    assert d["reason"] == "test retrieval"
    assert len(d["items"]) == 1
    assert d["items"][0]["content"] == "text"
