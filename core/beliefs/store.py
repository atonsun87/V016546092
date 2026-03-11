"""In-memory belief store for SELF-OS.

:class:`BeliefStore` manages the lifecycle of :class:`~core.beliefs.model.RevisableBelief`
objects for a single user.  It is intentionally thin: persistence to SQLite
or the graph is handled by a separate persistence adapter (planned in ARCH-04).

For v0 this store is in-memory; it should be wrapped with a persistence layer
before production use.
"""

from __future__ import annotations

import logging
from typing import Callable

from core.beliefs.model import (
    UNCERTAIN_THRESHOLD,
    RevisableBelief,
    RevisionEntry,
)

logger = logging.getLogger(__name__)


class BeliefStore:
    """In-memory store for revisable beliefs.

    Thread safety: not thread-safe; use a lock externally for concurrent access.

    Parameters
    ----------
    user_id:
        The user whose beliefs this store manages.
    on_revision:
        Optional callback invoked after every revision.  Receives the
        updated belief and the revision entry.
    """

    def __init__(
        self,
        user_id: str,
        on_revision: Callable[[RevisableBelief, RevisionEntry], None] | None = None,
    ) -> None:
        self.user_id = user_id
        self._beliefs: dict[str, RevisableBelief] = {}
        self._on_revision = on_revision

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, belief: RevisableBelief) -> RevisableBelief:
        """Add a belief to the store.

        If a belief with the same ID already exists, it is replaced.
        """
        if belief.user_id != self.user_id:
            raise ValueError(
                f"Belief user_id '{belief.user_id}' does not match store user_id '{self.user_id}'"
            )
        self._beliefs[belief.id] = belief
        logger.debug("BeliefStore: added belief %s (confidence=%.2f)", belief.id, belief.confidence)
        return belief

    def get(self, belief_id: str) -> RevisableBelief | None:
        """Return the belief with *belief_id*, or None."""
        return self._beliefs.get(belief_id)

    def remove(self, belief_id: str) -> bool:
        """Remove a belief. Returns True if it existed."""
        existed = belief_id in self._beliefs
        self._beliefs.pop(belief_id, None)
        return existed

    def all(self) -> list[RevisableBelief]:
        """Return all beliefs, sorted by confidence descending."""
        return sorted(self._beliefs.values(), key=lambda b: b.confidence, reverse=True)

    def count(self) -> int:
        return len(self._beliefs)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def uncertain(self) -> list[RevisableBelief]:
        """Return beliefs below the uncertain confidence threshold.

        User-confirmed beliefs are excluded (they are never uncertain).
        """
        return [b for b in self._beliefs.values() if b.is_uncertain]

    def strong(self) -> list[RevisableBelief]:
        """Return high-confidence or user-confirmed beliefs."""
        return [b for b in self._beliefs.values() if b.is_strong]

    def contradicted(self) -> list[RevisableBelief]:
        """Return beliefs that have contradictory evidence."""
        return [b for b in self._beliefs.values() if b.is_contradicted]

    def by_topic(self, topic: str) -> list[RevisableBelief]:
        """Return beliefs whose topic starts with *topic*."""
        return [b for b in self._beliefs.values() if b.topic.startswith(topic)]

    def user_confirmed_beliefs(self) -> list[RevisableBelief]:
        """Return beliefs explicitly confirmed by the user."""
        return [b for b in self._beliefs.values() if b.user_confirmed is True]

    def user_rejected_beliefs(self) -> list[RevisableBelief]:
        """Return beliefs explicitly rejected by the user."""
        return [b for b in self._beliefs.values() if b.user_confirmed is False]

    def for_agent_context(self) -> list[RevisableBelief]:
        """Return beliefs suitable for surfacing in agent context.

        Excluded:
        - User-rejected beliefs (``user_confirmed=False``)
        - Uncertain and contradicted beliefs (unless user-confirmed)
        """
        return [
            b for b in self._beliefs.values()
            if b.user_confirmed is not False
            and (b.is_strong or b.user_confirmed is True)
            and not b.is_contradicted
        ]

    # ------------------------------------------------------------------
    # Revision helpers
    # ------------------------------------------------------------------

    def revise(
        self,
        belief_id: str,
        new_confidence: float,
        reason: str = "",
        contradictory_event_ids: list[str] | None = None,
        supporting_event_ids: list[str] | None = None,
        revised_by: str = "system",
    ) -> RevisionEntry | None:
        """Revise a belief's confidence and log the change.

        Returns the :class:`RevisionEntry` if the belief exists, else None.
        """
        belief = self._beliefs.get(belief_id)
        if belief is None:
            logger.warning("BeliefStore.revise: belief %s not found", belief_id)
            return None

        entry = belief.revise(
            new_confidence=new_confidence,
            reason=reason,
            contradictory_event_ids=contradictory_event_ids,
            supporting_event_ids=supporting_event_ids,
            revised_by=revised_by,
        )

        if self._on_revision:
            try:
                self._on_revision(belief, entry)
            except Exception:
                logger.exception("BeliefStore: on_revision callback raised")

        return entry

    def confirm_by_user(self, belief_id: str) -> bool:
        """Mark belief as user-confirmed. Returns True if found."""
        belief = self._beliefs.get(belief_id)
        if belief is None:
            return False
        belief.confirm_by_user()
        return True

    def reject_by_user(self, belief_id: str) -> bool:
        """Mark belief as user-rejected. Returns True if found."""
        belief = self._beliefs.get(belief_id)
        if belief is None:
            return False
        belief.reject_by_user()
        return True

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_list(self) -> list[dict]:
        return [b.to_dict() for b in self._beliefs.values()]

    @classmethod
    def from_list(
        cls,
        user_id: str,
        data: list[dict],
        on_revision: Callable[[RevisableBelief, RevisionEntry], None] | None = None,
    ) -> BeliefStore:
        store = cls(user_id=user_id, on_revision=on_revision)
        for entry in data:
            belief = RevisableBelief.from_dict(entry)
            store._beliefs[belief.id] = belief
        return store
