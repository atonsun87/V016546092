"""Memory kernel — Signal / Belief separation.

In SELF-OS the fundamental primitive split mirrors a key neurobiological
distinction:

* **RawSignal** — an immutable, timestamped record of *something that happened*
  (user message, sensor reading, external event).  Analogous to sensory /
  episodic input arriving at the hippocampus.  Once written it cannot be
  modified.

* **DerivedBelief** — a revisable, confidence-weighted *conclusion* drawn from
  one or more signals.  Analogous to semantic long-term memory that is subject
  to reconsolidation when retrieved in a new context.

Both types carry a ``source`` / ``source_signal_ids`` field so that later
modules can reason about provenance (where did this belief come from?).

Design principles
-----------------
* Immutability of raw signals prevents silent data mutation and provides a
  stable audit log (analogous to the hippocampal episodic trace).
* Explicit provenance (``source_signal_ids``) enables auditable belief revision
  — the same mechanism the brain uses during reconsolidation.
* Confidence decays over time without re-confirmation, mirroring Ebbinghaus
  forgetting (applied externally by the memory scheduler).
* ``DerivedBelief.revise()`` appends the old state to ``revision_history``
  before overwriting, preserving the full reconsolidation chain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# RawSignal
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawSignal:
    """An immutable, timestamped record of a user event or system observation.

    ``RawSignal`` objects are the *source of truth* — they represent what
    actually happened before any interpretation.  They must never be mutated
    after creation (enforced by ``frozen=True``).

    Attributes
    ----------
    user_id:
        The user this signal belongs to.
    source:
        Human-readable label for the signal origin, e.g.
        ``"telegram_message"``, ``"journal_entry"``, ``"tool_observation"``.
    content:
        The raw text or structured payload of the event.
    id:
        Unique identifier (UUID4).  Auto-generated if omitted.
    metadata:
        Optional extra fields (channel id, session id, emotion tags, etc.).
    recorded_at:
        ISO-8601 UTC timestamp when this signal was received.
    """

    user_id: str
    source: str
    content: str
    id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = field(default_factory=_utc_now)


# ---------------------------------------------------------------------------
# DerivedBelief
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DerivedBelief:
    """A revisable, confidence-weighted conclusion derived from signals.

    Unlike :class:`RawSignal`, a ``DerivedBelief`` *can* change over time as
    new evidence arrives or existing evidence is reweighted.  Each revision is
    appended to ``revision_history`` so that the full provenance chain is
    preserved (analogous to neural reconsolidation).

    Attributes
    ----------
    user_id:
        The user this belief belongs to.
    statement:
        The textual content of the belief.
    confidence:
        Float in ``[0, 1]`` — how strongly this belief is held.
        ``1.0`` = full certainty; ``0.0`` = effectively dismissed.
    id:
        Unique identifier (UUID4).  Stable across revisions.
    source_signal_ids:
        IDs of the :class:`RawSignal` objects that were used to derive this
        belief.  Enables full provenance tracing back to raw events.
    graph_node_id:
        Optional reference to the corresponding ``BELIEF`` node in the
        knowledge graph when persisted via :class:`~core.kernel.belief_store.BeliefStore`.
    revision_history:
        Ordered list of past ``{statement, confidence, revised_at, reason}``
        snapshots, most recent last.
    created_at:
        ISO-8601 UTC timestamp of belief creation.
    updated_at:
        ISO-8601 UTC timestamp of most recent revision.
    """

    user_id: str
    statement: str
    confidence: float = 1.0
    id: str = field(default_factory=lambda: str(uuid4()))
    source_signal_ids: list[str] = field(default_factory=list)
    graph_node_id: str | None = None
    revision_history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def revise(
        self,
        new_statement: str,
        new_confidence: float,
        reason: str = "",
    ) -> None:
        """Revise this belief in place, appending the old state to history.

        This mirrors the neurobiological process of reconsolidation: when a
        belief is retrieved and contradicting evidence is present, it can be
        updated while retaining memory of its previous form.

        Parameters
        ----------
        new_statement:
            The revised belief text.
        new_confidence:
            New confidence level clamped to ``[0, 1]``.
        reason:
            Optional human-readable reason for the revision (e.g. which
            contradicting signal triggered the update).
        """
        self.revision_history.append(
            {
                "statement": self.statement,
                "confidence": self.confidence,
                "revised_at": self.updated_at,
                "reason": reason,
            }
        )
        self.statement = new_statement
        self.confidence = max(0.0, min(1.0, new_confidence))
        self.updated_at = _utc_now()
