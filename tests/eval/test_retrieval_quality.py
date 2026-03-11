"""Retrieval quality evaluation — parametrised scenario tests.

Each scenario defines a small set of :class:`~core.retrieval.models.RetrievalCandidate`
objects, a :class:`~core.retrieval.models.RetrievalQueryContext`, and the
expected ranking order of the top candidates.  The tests assert that the
correct candidates rank above the alternatives, protecting against scoring
regressions.

Canonical scenarios (from STABILIZATION.md Priority 3):

1. Goal-aligned recall — a goal-linked note ranks above a semantically similar
   but goal-less note when the query context includes an active goal.
2. Emotional salience — a high-emotion memory ranks above a neutral one in a
   reflection context.
3. Recency bias — a freshly created note outranks an older note with identical
   content in a chat context.
4. Identity resonance — an identity-tagged node ranks above an untagged node
   when the query carries identity signals.
5. Confidence filter — a low-confidence candidate ranks below a
   high-confidence one with otherwise equal scores.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.retrieval.models import RetrievalCandidate, RetrievalQueryContext
from core.retrieval.scoring import RetrievalScorer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(offset_days: int = 0) -> str:
    """Return an ISO-8601 UTC timestamp offset by *offset_days* from now."""
    return (datetime.now(UTC) - timedelta(days=offset_days)).isoformat()


def _candidate(
    memory_id: str,
    *,
    embedding_score: float = 0.5,
    confidence: float = 1.0,
    goal_links: list[str] | None = None,
    identity_links: list[str] | None = None,
    emotion_score: float = 0.0,
    tags: list[str] | None = None,
    timestamp: str | None = None,
    content: str = "sample content",
    memory_type: str = "NOTE",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        memory_id=memory_id,
        memory_type=memory_type,
        content=content,
        embedding_score=embedding_score,
        confidence=confidence,
        goal_links=goal_links or [],
        identity_links=identity_links or [],
        emotion_score=emotion_score,
        tags=tags or [],
        timestamp=timestamp,
    )


def _context(
    query_type: str = "chat",
    *,
    active_goals: list[str] | None = None,
    dominant_emotions: list[str] | None = None,
    identity_signals: list[str] | None = None,
    query_text: str = "test query",
) -> RetrievalQueryContext:
    return RetrievalQueryContext(
        user_id="eval-user",
        query_text=query_text,
        query_type=query_type,
        active_goals=active_goals or [],
        dominant_emotions=dominant_emotions or [],
        identity_signals=identity_signals or [],
    )


def _rank(
    candidates: list[RetrievalCandidate],
    ctx: RetrievalQueryContext,
) -> list[str]:
    """Return candidate IDs sorted by descending final score."""
    scorer = RetrievalScorer()
    scored = [(c.memory_id, scorer.score(c, ctx).final_score) for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [mid for mid, _ in scored]


# ---------------------------------------------------------------------------
# Scenario 1 — Goal-aligned recall
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_goal_aligned_note_outranks_goalless_in_planning():
    """A goal-linked note must rank above a high-semantics, goal-less note in
    ``planning`` mode."""
    goal_note = _candidate(
        "goal-linked",
        embedding_score=0.4,
        goal_links=["learn-python"],
        content="Study Python decorators",
    )
    semantic_note = _candidate(
        "semantic-only",
        embedding_score=0.85,
        goal_links=[],
        content="Python decorators explained",
    )

    ctx = _context("planning", active_goals=["learn-python"])
    ranked = _rank([semantic_note, goal_note], ctx)

    assert ranked[0] == "goal-linked", (
        "Goal-linked note should rank first in planning mode even against a "
        f"higher semantic score.  Got order: {ranked}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — Emotional salience
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_emotional_note_outranks_neutral_in_reflection():
    """A high-emotion memory must rank above a neutral memory with higher
    semantic score in ``reflection`` mode."""
    emotional = _candidate(
        "emotional",
        embedding_score=0.3,
        emotion_score=0.9,
        tags=["anxiety", "overwhelm"],
        content="I feel completely overwhelmed by work",
    )
    neutral = _candidate(
        "neutral",
        embedding_score=0.6,
        emotion_score=0.0,
        tags=[],
        content="Work update: project milestone reached",
    )

    ctx = _context("reflection", dominant_emotions=["anxiety"])
    ranked = _rank([neutral, emotional], ctx)

    assert ranked[0] == "emotional", (
        "Emotional note should rank first in reflection mode. "
        f"Got order: {ranked}"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — Recency bias
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_recent_note_outranks_old_note_in_chat():
    """A note created today must rank above an otherwise identical note from
    30 days ago in ``chat`` mode."""
    recent = _candidate(
        "recent",
        embedding_score=0.6,
        timestamp=_ts(offset_days=0),
        content="Meeting notes from today",
    )
    old = _candidate(
        "old",
        embedding_score=0.6,
        timestamp=_ts(offset_days=30),
        content="Meeting notes from today",
    )

    ctx = _context("chat")
    ranked = _rank([old, recent], ctx)

    assert ranked[0] == "recent", (
        "More recent note should rank first in chat mode. "
        f"Got order: {ranked}"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — Identity resonance
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_identity_tagged_note_outranks_untagged_in_proactive():
    """A note tagged with identity signals must rank above an untagged note with
    higher semantic score in ``proactive_action`` mode."""
    identity_tagged = _candidate(
        "identity",
        embedding_score=0.35,
        identity_links=["growth-mindset", "learning"],
        content="Exploring new frameworks keeps me engaged",
    )
    untagged = _candidate(
        "untagged",
        embedding_score=0.7,
        identity_links=[],
        content="Framework comparison blog post",
    )

    ctx = _context(
        "proactive_action",
        identity_signals=["growth-mindset", "learning"],
    )
    ranked = _rank([untagged, identity_tagged], ctx)

    assert ranked[0] == "identity", (
        "Identity-tagged note should rank first in proactive_action mode. "
        f"Got order: {ranked}"
    )


# ---------------------------------------------------------------------------
# Scenario 5 — Confidence filter
# ---------------------------------------------------------------------------


@pytest.mark.eval
def test_high_confidence_outranks_low_confidence():
    """A high-confidence candidate must rank above a low-confidence one when all
    other signals are equal (both have the same semantic score)."""
    high_conf = _candidate(
        "high-conf",
        embedding_score=0.5,
        confidence=0.95,
        content="User stated they prefer async communication",
    )
    low_conf = _candidate(
        "low-conf",
        embedding_score=0.5,
        confidence=0.2,
        content="User stated they prefer async communication",
    )

    ctx = _context("chat")
    ranked = _rank([low_conf, high_conf], ctx)

    assert ranked[0] == "high-conf", (
        "High-confidence candidate should rank first. "
        f"Got order: {ranked}"
    )


# ---------------------------------------------------------------------------
# Precision@K helper (used by scripts/run_retrieval_eval.py)
# ---------------------------------------------------------------------------


def precision_at_k(
    candidates: list[RetrievalCandidate],
    ctx: RetrievalQueryContext,
    relevant_ids: set[str],
    k: int,
) -> float:
    """Return precision@K: fraction of top-K results that are relevant."""
    ranked = _rank(candidates, ctx)
    top_k = ranked[:k]
    hits = sum(1 for mid in top_k if mid in relevant_ids)
    return hits / k if k > 0 else 0.0


@pytest.mark.eval
def test_precision_at_3_goal_scenario():
    """Precision@3 for the goal scenario must be 1.0 (all 3 top results relevant)."""
    relevant = {"g1", "g2", "g3"}
    goal_notes = [
        _candidate(f"g{i}", embedding_score=0.5, goal_links=["fitness"])
        for i in range(1, 4)
    ]
    noise_notes = [
        _candidate(f"n{i}", embedding_score=0.9, goal_links=[])
        for i in range(1, 6)
    ]
    ctx = _context("planning", active_goals=["fitness"])
    p3 = precision_at_k(goal_notes + noise_notes, ctx, relevant, k=3)
    assert p3 == 1.0, f"Expected precision@3=1.0, got {p3}"
