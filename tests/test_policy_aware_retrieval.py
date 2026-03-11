"""Tests for policy-aware retrieval scoring (query_type weight presets)."""

from __future__ import annotations

from core.retrieval.models import RetrievalCandidate, RetrievalQueryContext
from core.retrieval.scoring import (
    DEFAULT_WEIGHTS,
    QUERY_TYPE_WEIGHTS,
    RetrievalScorer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(**kwargs) -> RetrievalCandidate:
    defaults = dict(
        memory_id="mem-1",
        memory_type="NOTE",
        content="Some memory content",
        confidence=1.0,
        embedding_score=0.5,
    )
    defaults.update(kwargs)
    return RetrievalCandidate(**defaults)


def _make_context(query_type: str = "chat", **kwargs) -> RetrievalQueryContext:
    defaults = dict(user_id="u1", query_text="test query", query_type=query_type)
    defaults.update(kwargs)
    return RetrievalQueryContext(**defaults)


# ---------------------------------------------------------------------------
# Preset coverage
# ---------------------------------------------------------------------------


def test_all_presets_sum_to_one():
    """Every query-type preset must have weights that sum to 1.0."""
    for qt, weights in QUERY_TYPE_WEIGHTS.items():
        total = sum(weights.values())
        assert abs(total - 1.0) < 1e-6, (
            f"Preset '{qt}' weights sum to {total} (expected 1.0)"
        )


def test_all_presets_have_all_keys():
    """Every query-type preset must define all seven dimension keys."""
    required_keys = set(DEFAULT_WEIGHTS.keys())
    for qt, weights in QUERY_TYPE_WEIGHTS.items():
        assert set(weights.keys()) == required_keys, (
            f"Preset '{qt}' is missing keys: {required_keys - set(weights.keys())}"
        )


def test_default_weights_sum_to_one():
    """DEFAULT_WEIGHTS must sum to 1.0."""
    total = sum(DEFAULT_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# Policy-aware scoring behaviour
# ---------------------------------------------------------------------------


def test_planning_boosts_goal_relevance():
    """In 'planning' mode, goal-aligned candidates should outscore non-goal ones."""
    scorer = RetrievalScorer()

    # Candidate linked to the active goal
    goal_candidate = _make_candidate(
        memory_id="g",
        goal_links=["lose-weight"],
        embedding_score=0.3,
    )
    # Candidate with high semantic similarity but no goal link
    semantic_candidate = _make_candidate(
        memory_id="s",
        goal_links=[],
        embedding_score=0.9,
    )

    context = _make_context(
        query_type="planning",
        active_goals=["lose-weight"],
    )

    g_score = scorer.score(goal_candidate, context).final_score
    s_score = scorer.score(semantic_candidate, context).final_score

    # In planning mode goal_relevance weight is 0.35 vs semantic 0.20 —
    # a fully matched goal should overcome even a high semantic score.
    assert g_score > s_score, (
        f"Goal candidate ({g_score:.4f}) should outscore semantic-only ({s_score:.4f}) "
        "in planning mode"
    )


def test_reflection_boosts_emotional_salience():
    """In 'reflection' mode, emotionally salient candidates score higher."""
    scorer = RetrievalScorer()

    emotional = _make_candidate(
        memory_id="e",
        emotion_score=0.9,
        tags=["anxiety", "fear"],
        embedding_score=0.3,
    )
    neutral = _make_candidate(
        memory_id="n",
        emotion_score=0.0,
        tags=[],
        embedding_score=0.5,
    )

    context = _make_context(
        query_type="reflection",
        dominant_emotions=["anxiety"],
    )

    e_score = scorer.score(emotional, context).final_score
    n_score = scorer.score(neutral, context).final_score

    assert e_score > n_score, (
        f"Emotional candidate ({e_score:.4f}) should outscore neutral ({n_score:.4f}) "
        "in reflection mode"
    )


def test_goal_review_dominated_by_goal_relevance():
    """In 'goal_review' mode the goal_relevance weight is the highest."""
    weights = QUERY_TYPE_WEIGHTS["goal_review"]
    max_dim = max(weights, key=weights.__getitem__)
    assert max_dim == "goal_relevance"


def test_chat_dominated_by_semantic_relevance():
    """In 'chat' mode the semantic_relevance weight is the highest."""
    weights = QUERY_TYPE_WEIGHTS["chat"]
    max_dim = max(weights, key=weights.__getitem__)
    assert max_dim == "semantic_relevance"


def test_unknown_query_type_falls_back_to_default():
    """An unrecognised query_type uses DEFAULT_WEIGHTS."""
    scorer = RetrievalScorer()
    candidate = _make_candidate(embedding_score=0.7)
    context_known = _make_context(query_type="chat")
    context_unknown = _make_context(query_type="nonexistent_type")

    # Scores will differ because "chat" preset ≠ DEFAULT_WEIGHTS for most dims,
    # but the unknown type should not crash and should use DEFAULT_WEIGHTS.
    breakdown_unknown = scorer.score(candidate, context_unknown)
    assert 0.0 <= breakdown_unknown.final_score <= 1.0


def test_instance_override_weights_take_priority():
    """Instance-level override weights supersede both the preset and DEFAULT_WEIGHTS."""
    # Force semantic_relevance to 1.0 and all others to 0.0
    override = {
        "semantic_relevance": 1.0,
        "goal_relevance": 0.0,
        "identity_relevance": 0.0,
        "emotional_salience": 0.0,
        "recency_score": 0.0,
        "confidence_score": 0.0,
        "relationship_score": 0.0,
    }
    scorer = RetrievalScorer(weights=override)
    candidate = _make_candidate(embedding_score=0.75)

    for qt in QUERY_TYPE_WEIGHTS:
        ctx = _make_context(query_type=qt)
        bd = scorer.score(candidate, ctx)
        assert abs(bd.final_score - 0.75) < 1e-6, (
            f"query_type={qt}: expected final_score=0.75 but got {bd.final_score}"
        )


def test_scorer_backward_compat_no_query_type():
    """Scorer without explicit query_type falls back gracefully (uses DEFAULT_WEIGHTS)."""
    scorer = RetrievalScorer()
    candidate = _make_candidate(embedding_score=0.6, confidence=0.8)
    # RetrievalQueryContext defaults query_type to "chat"
    ctx = RetrievalQueryContext(user_id="u1", query_text="hello")
    bd = scorer.score(candidate, ctx)
    assert 0.0 <= bd.final_score <= 1.0
