"""Rule-based, explainable retrieval scorer for SELF-OS.

``RetrievalScorer`` scores a ``RetrievalCandidate`` against a
``RetrievalQueryContext`` and produces a ``RetrievalScoreBreakdown`` that
exposes both the numeric final score and a list of human-readable explanation
strings for strong signals.

The scorer is intentionally rule-based and fully deterministic in v0 so that
its behaviour is easy to reason about, test, and evolve.

Policy-aware weights
--------------------
Different retrieval contexts emphasise different memory dimensions.  The
``query_type`` field of :class:`~core.retrieval.models.RetrievalQueryContext`
selects a weight preset that is **blended** with the caller's custom weights
(if any):

* ``"chat"`` — balanced; semantic and confidence lead.
* ``"planning"`` — goals dominate; recency is still important.
* ``"proactive_action"`` — goal + identity alignment; emotional signal matters.
* ``"reflection"`` — identity and emotional salience lead; recency is reduced.
* ``"goal_review"`` — goal relevance is highest; identity and recency follow.

Callers may still pass explicit ``weights`` to the constructor to override
any preset on a per-instance basis.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Final

from core.retrieval.models import (
    RetrievalCandidate,
    RetrievalQueryContext,
    RetrievalScoreBreakdown,
)

# ---------------------------------------------------------------------------
# Default dimension weights
# ---------------------------------------------------------------------------

#: v0 default weights.  Must sum to 1.0.
DEFAULT_WEIGHTS: Final[dict[str, float]] = {
    "semantic_relevance": 0.30,
    "goal_relevance": 0.20,
    "identity_relevance": 0.15,
    "emotional_salience": 0.10,
    "recency_score": 0.10,
    "confidence_score": 0.10,
    "relationship_score": 0.05,
}

# ---------------------------------------------------------------------------
# Query-type weight presets (policy-aware retrieval)
# ---------------------------------------------------------------------------
# Each preset must contain all seven dimension keys and sum to 1.0.

QUERY_TYPE_WEIGHTS: Final[dict[str, dict[str, float]]] = {
    # Balanced conversational retrieval — semantic quality and confidence lead.
    "chat": {
        "semantic_relevance": 0.35,
        "goal_relevance": 0.15,
        "identity_relevance": 0.10,
        "emotional_salience": 0.10,
        "recency_score": 0.15,
        "confidence_score": 0.10,
        "relationship_score": 0.05,
    },
    # Planning retrieval — goals dominate, recency still matters.
    "planning": {
        "semantic_relevance": 0.20,
        "goal_relevance": 0.35,
        "identity_relevance": 0.10,
        "emotional_salience": 0.05,
        "recency_score": 0.15,
        "confidence_score": 0.10,
        "relationship_score": 0.05,
    },
    # Proactive action — goal + identity alignment, emotional signal matters.
    "proactive_action": {
        "semantic_relevance": 0.20,
        "goal_relevance": 0.25,
        "identity_relevance": 0.20,
        "emotional_salience": 0.15,
        "recency_score": 0.10,
        "confidence_score": 0.05,
        "relationship_score": 0.05,
    },
    # Reflection — identity and emotional salience lead; recency is reduced.
    "reflection": {
        "semantic_relevance": 0.20,
        "goal_relevance": 0.10,
        "identity_relevance": 0.25,
        "emotional_salience": 0.25,
        "recency_score": 0.05,
        "confidence_score": 0.10,
        "relationship_score": 0.05,
    },
    # Goal review — goal relevance highest; identity and recency follow.
    "goal_review": {
        "semantic_relevance": 0.15,
        "goal_relevance": 0.40,
        "identity_relevance": 0.15,
        "emotional_salience": 0.05,
        "recency_score": 0.15,
        "confidence_score": 0.05,
        "relationship_score": 0.05,
    },
}

# Threshold above which a dimension is considered a "strong signal" worth
# including in the explanation.
_EXPLANATION_THRESHOLD: Final[float] = 0.6

# Half-life for the recency decay in days.  A memory this old receives a
# recency score of ~0.5.
_RECENCY_HALF_LIFE_DAYS: Final[float] = 30.0

# Maximum graph distance that still contributes positively to relationship
# score.  Beyond this the score is clamped to 0.
_MAX_GRAPH_DISTANCE: Final[int] = 5


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the range [*lo*, *hi*]."""
    return max(lo, min(hi, value))


class RetrievalScorer:
    """Scores memory candidates against a retrieval query context.

    Parameters
    ----------
    weights:
        Optional override for the dimension weights dict.  If supplied it must
        contain all seven dimension keys and the values should sum to 1.0.
        Missing keys fall back to ``DEFAULT_WEIGHTS``.  These instance-level
        weights are applied *on top of* any query-type preset selected at
        :meth:`score` time.
    """

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        # Instance-level overrides (may be empty)
        self._override_weights: dict[str, float] = dict(weights) if weights else {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _effective_weights(self, query_type: str) -> dict[str, float]:
        """Return the effective weight dict for *query_type*.

        Priority: instance-level overrides > query-type preset > DEFAULT_WEIGHTS.
        """
        preset = QUERY_TYPE_WEIGHTS.get(query_type, DEFAULT_WEIGHTS)
        return {**DEFAULT_WEIGHTS, **preset, **self._override_weights}

    def score(
        self,
        candidate: RetrievalCandidate,
        context: RetrievalQueryContext,
    ) -> RetrievalScoreBreakdown:
        """Score *candidate* against *context* and return a full breakdown.

        Each dimension is computed independently, then combined via a weighted
        sum that is clamped to ``[0, 1]``.  The weights are selected based on
        ``context.query_type`` (policy-aware retrieval) and then merged with
        any instance-level override weights.  Explanation strings are added for
        any dimension whose score exceeds ``_EXPLANATION_THRESHOLD``.
        """
        semantic = self._semantic_relevance(candidate)
        goal = self._goal_relevance(candidate, context)
        identity = self._identity_relevance(candidate, context)
        emotional = self._emotional_salience(candidate, context)
        recency = self._recency_score(candidate)
        confidence = self._confidence_score(candidate)
        relationship = self._relationship_score(candidate)

        w = self._effective_weights(context.query_type)
        final = _clamp(
            semantic * w["semantic_relevance"]
            + goal * w["goal_relevance"]
            + identity * w["identity_relevance"]
            + emotional * w["emotional_salience"]
            + recency * w["recency_score"]
            + confidence * w["confidence_score"]
            + relationship * w["relationship_score"]
        )

        explanation = self._build_explanation(
            semantic=semantic,
            goal=goal,
            identity=identity,
            emotional=emotional,
            recency=recency,
            confidence=confidence,
            relationship=relationship,
        )

        return RetrievalScoreBreakdown(
            semantic_relevance=semantic,
            goal_relevance=goal,
            identity_relevance=identity,
            emotional_salience=emotional,
            recency_score=recency,
            confidence_score=confidence,
            relationship_score=relationship,
            final_score=final,
            explanation=explanation,
        )

    # ------------------------------------------------------------------
    # Individual dimension scorers
    # ------------------------------------------------------------------

    def _semantic_relevance(self, candidate: RetrievalCandidate) -> float:
        """Pass through the embedding similarity score, clamped to [0, 1]."""
        return _clamp(candidate.embedding_score)

    def _goal_relevance(
        self,
        candidate: RetrievalCandidate,
        context: RetrievalQueryContext,
    ) -> float:
        """Fraction of the candidate's goal links that match active goals.

        If the context has no active goals, returns 0.0 to avoid inflating
        scores when goals are unknown.
        """
        if not context.active_goals or not candidate.goal_links:
            return 0.0
        active_lower = {g.lower() for g in context.active_goals}
        candidate_lower = {g.lower() for g in candidate.goal_links}
        overlap = len(active_lower & candidate_lower)
        return _clamp(overlap / len(active_lower))

    def _identity_relevance(
        self,
        candidate: RetrievalCandidate,
        context: RetrievalQueryContext,
    ) -> float:
        """Fraction of the candidate's identity links that match context signals.

        Tags and domain are also considered so that even loosely-linked nodes
        can surface when they resonate with the identity model.
        """
        if not context.identity_signals:
            return 0.0

        signals_lower = {s.lower() for s in context.identity_signals}

        candidate_signals: set[str] = set()
        candidate_signals.update(lnk.lower() for lnk in candidate.identity_links)
        candidate_signals.update(t.lower() for t in candidate.tags)
        if candidate.domain:
            candidate_signals.add(candidate.domain.lower())

        if not candidate_signals:
            return 0.0

        overlap = len(signals_lower & candidate_signals)
        return _clamp(overlap / len(signals_lower))

    def _emotional_salience(
        self,
        candidate: RetrievalCandidate,
        context: RetrievalQueryContext,
    ) -> float:
        """Blend the candidate's intrinsic emotion score with context resonance.

        If the user's dominant emotions overlap with the candidate's tags, a
        resonance bonus is added on top of the candidate's own emotion score.
        """
        base = _clamp(candidate.emotion_score)

        resonance = 0.0
        if context.dominant_emotions and candidate.tags:
            emotions_lower = {e.lower() for e in context.dominant_emotions}
            tags_lower = {t.lower() for t in candidate.tags}
            if emotions_lower & tags_lower:
                resonance = 0.3  # fixed contextual resonance bonus

        return _clamp(base + resonance)

    def _recency_score(self, candidate: RetrievalCandidate) -> float:
        """Exponential time-decay score.

        Uses a half-life of ``_RECENCY_HALF_LIFE_DAYS``.  Returns ``0.5`` if
        the timestamp is missing or unparseable so that the absence of
        timestamp information does not unduly penalise a candidate.
        """
        if not candidate.timestamp:
            return 0.5

        try:
            ts = datetime.fromisoformat(candidate.timestamp)
        except ValueError:
            return 0.5

        # Ensure timezone-aware comparison.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        age_days = max(0.0, (now - ts).total_seconds() / 86_400.0)
        # Exponential decay: score = 2^(-age / half_life)
        return _clamp(math.pow(2.0, -age_days / _RECENCY_HALF_LIFE_DAYS))

    def _confidence_score(self, candidate: RetrievalCandidate) -> float:
        """Direct passthrough of the candidate confidence, clamped to [0, 1]."""
        return _clamp(candidate.confidence)

    def _relationship_score(self, candidate: RetrievalCandidate) -> float:
        """Inverse-distance score based on graph hop distance.

        Distance 0 → score 1.0; distance >= ``_MAX_GRAPH_DISTANCE`` → score 0.0.
        """
        d = candidate.graph_distance
        if d <= 0:
            return 1.0
        if d >= _MAX_GRAPH_DISTANCE:
            return 0.0
        return _clamp(1.0 - d / _MAX_GRAPH_DISTANCE)

    # ------------------------------------------------------------------
    # Explanation builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_explanation(
        *,
        semantic: float,
        goal: float,
        identity: float,
        emotional: float,
        recency: float,
        confidence: float,
        relationship: float,
    ) -> list[str]:
        """Return human-readable strings for dimensions above the threshold."""
        items: list[str] = []
        threshold = _EXPLANATION_THRESHOLD

        if semantic >= threshold:
            items.append(
                f"High semantic relevance ({semantic:.2f}): "
                "content closely matches the query."
            )
        if goal >= threshold:
            items.append(
                f"Strong goal alignment ({goal:.2f}): "
                "memory is linked to one or more active goals."
            )
        if identity >= threshold:
            items.append(
                f"Strong identity relevance ({identity:.2f}): "
                "memory resonates with core identity signals."
            )
        if emotional >= threshold:
            items.append(
                f"High emotional salience ({emotional:.2f}): "
                "memory carries significant emotional weight."
            )
        if recency >= threshold:
            items.append(
                f"Recent memory ({recency:.2f}): "
                "memory was created or updated recently."
            )
        if confidence >= threshold:
            items.append(
                f"High confidence ({confidence:.2f}): "
                "memory is considered reliable."
            )
        if relationship >= threshold:
            items.append(
                f"Close graph relationship ({relationship:.2f}): "
                "memory is near the query anchor in the knowledge graph."
            )

        return items
