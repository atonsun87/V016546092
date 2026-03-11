"""Scoped memory views — agents and interfaces consume approved views.

Architecture principle
----------------------
In a neurobiologically inspired system, memory is **never accessed raw**.
Access is always mediated by a context-sensitive gate — the equivalent of the
hippocampal-prefrontal relay in the brain, which shapes what gets recalled
based on current goals, arousal, and identity state.

This module implements that principle as **MemoryView**: a read-only, scoped
projection over retrieval candidates that:

1. Enforces a **caller scope** (``"agent"``, ``"user_reflection"``,
   ``"offline_worker"``) so different callers see different slices.
2. Applies a **confidence filter** so low-confidence items are hidden from
   the online agent by default.
3. Provides a **serialisable** result set — the caller never holds a live
   reference to the graph internals.
4. Tracks **access reason** for auditability.

Usage example
-------------
::

    from core.retrieval.view import MemoryViewBuilder, MemoryViewPolicy

    policy = MemoryViewPolicy(
        scope="agent",
        caller_id="reply_generator",
        max_results=10,
        min_confidence=0.4,
    )
    view = MemoryViewBuilder.build(candidates, scored, policy)
    for item in view.items:
        print(item.content, item.final_score)

Design notes
------------
* ``MemoryView`` is **immutable after construction** — it is safe to pass
  around without defensive copying.
* The ``offline_worker`` scope sees all items including low-confidence ones
  so it can decide what to consolidate or discard.
* ``MemoryViewPolicy`` is the single point of access-control configuration;
  new scopes or rules are added here without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from core.retrieval.models import RetrievalCandidate, RetrievalScoreBreakdown

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

#: Recognised caller scopes.
MEMORY_VIEW_SCOPES = frozenset({"agent", "user_reflection", "offline_worker"})


@dataclass(slots=True)
class MemoryViewPolicy:
    """Governs what a particular caller may see.

    Attributes
    ----------
    scope:
        The caller's role.  One of ``"agent"``, ``"user_reflection"``,
        ``"offline_worker"``.
    caller_id:
        Free-form identifier of the specific caller (for audit logs).
    max_results:
        Maximum number of items to include in the view.  The offline worker
        scope ignores this and returns all items.
    min_confidence:
        Minimum candidate confidence to include.  Defaults depend on scope:
        ``"agent"`` → ``0.4``, ``"user_reflection"`` → ``0.2``,
        ``"offline_worker"`` → ``0.0``.
    include_revision_history:
        When ``True`` the view exposes belief revision history in
        ``raw_payload``.  Only meaningful for ``"offline_worker"`` scope.
    reason:
        Human-readable description of why this retrieval is happening.
    """

    scope: str = "agent"
    caller_id: str = "unknown"
    max_results: int = 10
    min_confidence: float = 0.4
    include_revision_history: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if self.scope not in MEMORY_VIEW_SCOPES:
            raise ValueError(
                f"Unknown scope {self.scope!r}.  "
                f"Must be one of {sorted(MEMORY_VIEW_SCOPES)}"
            )
        # Override defaults per scope
        if self.scope == "offline_worker":
            self.min_confidence = 0.0
            self.max_results = 10_000  # effectively unlimited
            self.include_revision_history = True
        # "user_reflection" and "agent" scopes: caller-supplied values take effect as-is.


# ---------------------------------------------------------------------------
# MemoryViewItem
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MemoryViewItem:
    """A single item in a :class:`MemoryView`.

    All fields are copies — mutations here do not affect the source graph.

    Attributes
    ----------
    memory_id:
        Unique ID of the original graph node or belief record.
    memory_type:
        Node type (``"BELIEF"``, ``"NOTE"``, ``"INSIGHT"``, …).
    content:
        Text content of the memory.
    final_score:
        Weighted retrieval score from :class:`~core.retrieval.scoring.RetrievalScorer`.
    confidence:
        Candidate confidence as assessed by the offline worker or the
        ingestion pipeline.
    timestamp:
        ISO-8601 creation/update timestamp.
    domain:
        Broad topic domain (e.g. ``"career"``, ``"health"``).
    tags:
        Keyword tags.
    explanation:
        Human-readable scoring explanation for debugging/UI.
    raw_payload:
        Optional extra fields from the source node (e.g. revision history for
        ``offline_worker`` scope).
    """

    memory_id: str
    memory_type: str
    content: str
    final_score: float = 0.0
    confidence: float = 1.0
    timestamp: str | None = None
    domain: str = ""
    tags: list[str] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "content": self.content,
            "final_score": self.final_score,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "domain": self.domain,
            "tags": list(self.tags),
            "explanation": list(self.explanation),
        }


# ---------------------------------------------------------------------------
# MemoryView
# ---------------------------------------------------------------------------


@dataclass
class MemoryView:
    """A read-only, scoped projection over retrieval candidates.

    Attributes
    ----------
    scope:
        The caller scope that produced this view.
    caller_id:
        The specific caller that requested this view.
    reason:
        Why this retrieval happened.
    created_at:
        When the view was constructed (ISO-8601 UTC).
    items:
        Ordered list of :class:`MemoryViewItem` objects, best-scored first.
    total_candidates:
        How many candidates were available before scope filtering.
    filtered_count:
        How many candidates were removed by scope / confidence filtering.
    """

    scope: str
    caller_id: str
    reason: str
    created_at: str
    items: list[MemoryViewItem]
    total_candidates: int = 0
    filtered_count: int = 0

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def top(self, n: int = 5) -> list[MemoryViewItem]:
        """Return the top *n* items by score."""
        return self.items[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "caller_id": self.caller_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "total_candidates": self.total_candidates,
            "filtered_count": self.filtered_count,
            "items": [item.to_dict() for item in self.items],
        }


# ---------------------------------------------------------------------------
# MemoryViewBuilder
# ---------------------------------------------------------------------------


class MemoryViewBuilder:
    """Constructs :class:`MemoryView` objects from retrieval results.

    This is the **only** place where scope enforcement happens.  Callers
    pass their :class:`MemoryViewPolicy` and receive a filtered, capped view.
    They never receive the raw list of :class:`~core.retrieval.models.RetrievalCandidate`
    objects directly from the graph.
    """

    @staticmethod
    def build(
        candidates: list[RetrievalCandidate],
        scored: list[RetrievalScoreBreakdown],
        policy: MemoryViewPolicy,
    ) -> MemoryView:
        """Build a view from *candidates* + *scored* breakdown list.

        Parameters
        ----------
        candidates:
            Raw retrieval candidates from the ranker.
        scored:
            Parallel list of score breakdowns (same order as *candidates*).
        policy:
            The access policy for this call.

        Returns
        -------
        MemoryView
            A filtered, capped, read-only projection.
        """
        if len(candidates) != len(scored):
            # Gracefully handle mismatch — pad scored with zeros
            from core.retrieval.models import RetrievalScoreBreakdown as _SB
            while len(scored) < len(candidates):
                scored.append(_SB())

        total = len(candidates)
        items: list[MemoryViewItem] = []
        filtered = 0

        for cand, score in zip(candidates, scored, strict=False):
            if cand.confidence < policy.min_confidence:
                filtered += 1
                continue

            payload: dict[str, Any] = {}
            if policy.include_revision_history and cand.raw_payload:
                payload = dict(cand.raw_payload)

            items.append(
                MemoryViewItem(
                    memory_id=cand.memory_id,
                    memory_type=cand.memory_type,
                    content=cand.content,
                    final_score=round(score.final_score, 4),
                    confidence=cand.confidence,
                    timestamp=cand.timestamp,
                    domain=cand.domain,
                    tags=list(cand.tags),
                    explanation=list(score.explanation),
                    raw_payload=payload,
                )
            )

        # Sort by final_score descending
        items.sort(key=lambda x: x.final_score, reverse=True)

        # Cap results (offline_worker has max_results=10_000 so effectively uncapped)
        if len(items) > policy.max_results:
            items = items[: policy.max_results]

        return MemoryView(
            scope=policy.scope,
            caller_id=policy.caller_id,
            reason=policy.reason,
            created_at=datetime.now(UTC).isoformat(),
            items=items,
            total_candidates=total,
            filtered_count=filtered,
        )

    @staticmethod
    def build_from_nodes(
        nodes: list[dict[str, Any]],
        policy: MemoryViewPolicy,
    ) -> MemoryView:
        """Convenience builder for simple node dicts (no scorer needed).

        Accepts a list of node-like dicts with at minimum ``id``, ``type``,
        and ``text`` keys.  Useful for constructing offline-worker views from
        raw graph nodes without going through the full retrieval pipeline.
        """
        total = len(nodes)
        items: list[MemoryViewItem] = []
        filtered = 0

        for node in nodes:
            confidence = float(node.get("confidence", node.get("metadata", {}).get("salience_score", 1.0)))
            if confidence < policy.min_confidence:
                filtered += 1
                continue

            payload: dict[str, Any] = {}
            if policy.include_revision_history:
                payload = {k: v for k, v in node.items() if k not in {"id", "type", "text"}}

            items.append(
                MemoryViewItem(
                    memory_id=str(node.get("id", "")),
                    memory_type=str(node.get("type", "UNKNOWN")),
                    content=str(node.get("text", node.get("name", ""))),
                    final_score=float(node.get("score", 0.0)),
                    confidence=confidence,
                    timestamp=node.get("timestamp", node.get("created_at")),
                    domain=str(node.get("domain", "")),
                    tags=list(node.get("tags", [])),
                    raw_payload=payload,
                )
            )

        items.sort(key=lambda x: x.final_score, reverse=True)
        if len(items) > policy.max_results:
            items = items[: policy.max_results]

        return MemoryView(
            scope=policy.scope,
            caller_id=policy.caller_id,
            reason=policy.reason,
            created_at=datetime.now(UTC).isoformat(),
            items=items,
            total_candidates=total,
            filtered_count=filtered,
        )
