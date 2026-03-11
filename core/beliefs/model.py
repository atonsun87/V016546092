"""Revisable probabilistic belief model for SELF-OS.

A :class:`RevisableBelief` represents a single belief about the user.
Unlike a raw ``BELIEF`` graph node (which is a simple text string), a
``RevisableBelief`` carries:

- A **confidence** score (0–1) reflecting how strongly the system believes this.
- A list of **evidence event IDs** that support the belief.
- A list of **contradictory event IDs** that argue against it.
- A **user_confirmed** flag: ``True`` means the user explicitly endorsed the
  belief; ``False`` means they rejected it; ``None`` means unreviewed.
- A **revision history**: every change is logged with timestamp and reason.

Neurobiological parallel
------------------------
This mirrors the brain's reconsolidation mechanism: every time a memory is
retrieved it becomes labile and can be updated by new information before being
re-stored.  The ``revise()`` method implements this: it updates confidence and
logs the change, but never silently overwrites.

Usage::

    from core.beliefs.model import RevisableBelief

    belief = RevisableBelief.create(
        user_id="u1",
        content="I prefer working alone on deep-focus tasks",
        confidence=0.6,
        source_event_ids=["event-abc", "event-def"],
    )

    # New contradictory evidence arrives:
    belief.revise(
        new_confidence=0.4,
        reason="User mentioned enjoying collaborative work multiple times",
        contradictory_event_ids=["event-xyz"],
    )

    # User confirms the original belief:
    belief.confirm_by_user()

    assert belief.user_confirmed is True
    assert belief.is_uncertain is False  # user_confirmed overrides low confidence
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Confidence below this is considered "uncertain" (not surfaced as fact).
UNCERTAIN_THRESHOLD: float = 0.35

#: Confidence above this is considered "strong".
STRONG_THRESHOLD: float = 0.70


# ---------------------------------------------------------------------------
# RevisionEntry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RevisionEntry:
    """A single entry in a belief's revision history.

    Attributes
    ----------
    revised_at:
        ISO timestamp of the revision.
    old_confidence:
        Confidence score before the revision.
    new_confidence:
        Confidence score after the revision.
    reason:
        Human-readable explanation of why the belief was revised.
    contradictory_event_ids:
        Event IDs that triggered this revision (evidence against).
    supporting_event_ids:
        Event IDs that reinforced this revision (evidence for).
    revised_by:
        Who or what triggered the revision: ``"system"``, ``"user"``,
        or an agent ID.
    """

    revised_at: str
    old_confidence: float
    new_confidence: float
    reason: str = ""
    contradictory_event_ids: list[str] = field(default_factory=list)
    supporting_event_ids: list[str] = field(default_factory=list)
    revised_by: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "revised_at": self.revised_at,
            "old_confidence": self.old_confidence,
            "new_confidence": self.new_confidence,
            "reason": self.reason,
            "contradictory_event_ids": self.contradictory_event_ids,
            "supporting_event_ids": self.supporting_event_ids,
            "revised_by": self.revised_by,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RevisionEntry:
        return cls(
            revised_at=data["revised_at"],
            old_confidence=data["old_confidence"],
            new_confidence=data["new_confidence"],
            reason=data.get("reason", ""),
            contradictory_event_ids=list(data.get("contradictory_event_ids", [])),
            supporting_event_ids=list(data.get("supporting_event_ids", [])),
            revised_by=data.get("revised_by", "system"),
        )


# ---------------------------------------------------------------------------
# RevisableBelief
# ---------------------------------------------------------------------------


@dataclass
class RevisableBelief:
    """A probabilistic, revisable belief about the user.

    Attributes
    ----------
    id:
        Unique belief identifier.
    user_id:
        Owner of this belief.
    content:
        The belief text (e.g. "I prefer working alone on deep-focus tasks").
    confidence:
        Current confidence in the belief, range ``[0, 1]``.
    source_event_ids:
        Event IDs that originally gave rise to this belief (provenance).
    contradictory_event_ids:
        Event IDs that argue against this belief.
    user_confirmed:
        ``True`` — user explicitly endorsed.
        ``False`` — user explicitly rejected.
        ``None`` — not yet reviewed.
    last_revised_at:
        ISO timestamp of the most recent revision.
    revision_count:
        How many times this belief has been revised.
    revision_history:
        Full audit trail of revisions.
    created_at:
        ISO timestamp of creation.
    graph_node_id:
        Optional reference to the corresponding ``BELIEF`` graph node.
    topic:
        Broad topic label (e.g. ``"values/autonomy"``, ``"work/style"``).
        Used for grouping and non-identifying summaries.
    """

    id: str
    user_id: str
    content: str
    confidence: float
    source_event_ids: list[str] = field(default_factory=list)
    contradictory_event_ids: list[str] = field(default_factory=list)
    user_confirmed: bool | None = None
    last_revised_at: str = field(default_factory=_utc_now)
    revision_count: int = 0
    revision_history: list[RevisionEntry] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    graph_node_id: str | None = None
    topic: str = ""

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        user_id: str,
        content: str,
        confidence: float,
        source_event_ids: list[str] | None = None,
        topic: str = "",
        graph_node_id: str | None = None,
    ) -> RevisableBelief:
        """Create a new belief with validated confidence."""
        confidence = max(0.0, min(1.0, confidence))
        return cls(
            id=_new_id(),
            user_id=user_id,
            content=content,
            confidence=confidence,
            source_event_ids=source_event_ids or [],
            topic=topic,
            graph_node_id=graph_node_id,
        )

    # ------------------------------------------------------------------
    # Status properties
    # ------------------------------------------------------------------

    @property
    def is_uncertain(self) -> bool:
        """Return ``True`` if confidence is below the uncertain threshold.

        If the user has explicitly confirmed the belief, this always
        returns ``False`` (user override wins).
        """
        if self.user_confirmed is True:
            return False
        return self.confidence < UNCERTAIN_THRESHOLD

    @property
    def is_strong(self) -> bool:
        """Return ``True`` if confidence is above the strong threshold."""
        return self.user_confirmed is True or self.confidence >= STRONG_THRESHOLD

    @property
    def is_contradicted(self) -> bool:
        """Return ``True`` if there is at least one contradictory evidence event."""
        return len(self.contradictory_event_ids) > 0

    @property
    def status(self) -> str:
        """Return a human-readable status string.

        One of: ``"user_confirmed"`` / ``"user_rejected"`` /
        ``"strong"`` / ``"uncertain"`` / ``"contradicted"``.
        """
        if self.user_confirmed is True:
            return "user_confirmed"
        if self.user_confirmed is False:
            return "user_rejected"
        if self.is_contradicted and self.confidence < STRONG_THRESHOLD:
            return "contradicted"
        if self.is_uncertain:
            return "uncertain"
        return "strong"

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def revise(
        self,
        new_confidence: float,
        reason: str = "",
        contradictory_event_ids: list[str] | None = None,
        supporting_event_ids: list[str] | None = None,
        revised_by: str = "system",
    ) -> RevisionEntry:
        """Update the belief's confidence and log the revision.

        Parameters
        ----------
        new_confidence:
            Replacement confidence score (clamped to ``[0, 1]``).
        reason:
            Human-readable explanation.
        contradictory_event_ids:
            New contradictory evidence event IDs (appended to existing list).
        supporting_event_ids:
            New supporting evidence event IDs (appended to source_event_ids).
        revised_by:
            Identity of the reviser (``"system"``, ``"user"``, or agent ID).

        Returns
        -------
        RevisionEntry
            The logged revision entry.
        """
        old_confidence = self.confidence
        new_confidence = max(0.0, min(1.0, new_confidence))

        entry = RevisionEntry(
            revised_at=_utc_now(),
            old_confidence=old_confidence,
            new_confidence=new_confidence,
            reason=reason,
            contradictory_event_ids=contradictory_event_ids or [],
            supporting_event_ids=supporting_event_ids or [],
            revised_by=revised_by,
        )

        self.confidence = new_confidence
        self.last_revised_at = entry.revised_at
        self.revision_count += 1
        self.revision_history.append(entry)

        if contradictory_event_ids:
            for eid in contradictory_event_ids:
                if eid not in self.contradictory_event_ids:
                    self.contradictory_event_ids.append(eid)

        if supporting_event_ids:
            for eid in supporting_event_ids:
                if eid not in self.source_event_ids:
                    self.source_event_ids.append(eid)

        return entry

    def confirm_by_user(self) -> None:
        """Mark this belief as explicitly confirmed by the user."""
        self.user_confirmed = True
        self.last_revised_at = _utc_now()

    def reject_by_user(self) -> None:
        """Mark this belief as explicitly rejected by the user."""
        old_confidence = self.confidence
        self.user_confirmed = False
        self.confidence = 0.0
        self.last_revised_at = _utc_now()
        self.revision_count += 1
        self.revision_history.append(RevisionEntry(
            revised_at=self.last_revised_at,
            old_confidence=old_confidence,
            new_confidence=0.0,
            reason="Rejected by user",
            revised_by="user",
        ))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "content": self.content,
            "confidence": self.confidence,
            "source_event_ids": self.source_event_ids,
            "contradictory_event_ids": self.contradictory_event_ids,
            "user_confirmed": self.user_confirmed,
            "last_revised_at": self.last_revised_at,
            "revision_count": self.revision_count,
            "revision_history": [r.to_dict() for r in self.revision_history],
            "created_at": self.created_at,
            "graph_node_id": self.graph_node_id,
            "topic": self.topic,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RevisableBelief:
        belief = cls(
            id=data["id"],
            user_id=data["user_id"],
            content=data["content"],
            confidence=data["confidence"],
            source_event_ids=list(data.get("source_event_ids", [])),
            contradictory_event_ids=list(data.get("contradictory_event_ids", [])),
            user_confirmed=data.get("user_confirmed"),
            last_revised_at=data.get("last_revised_at", _utc_now()),
            revision_count=data.get("revision_count", 0),
            revision_history=[
                RevisionEntry.from_dict(r)
                for r in data.get("revision_history", [])
            ],
            created_at=data.get("created_at", _utc_now()),
            graph_node_id=data.get("graph_node_id"),
            topic=data.get("topic", ""),
        )
        return belief

    def snapshot(self) -> RevisableBelief:
        """Return a deep copy of this belief (for immutable snapshots)."""
        return copy.deepcopy(self)
