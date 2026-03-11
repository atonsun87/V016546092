"""Belief schema — revisable, confidence-weighted beliefs.

Neurobiological inspiration
---------------------------
In biological memory, beliefs are not stored once and read back unchanged.
Every recall is a *reconsolidation* opportunity: the retrieved trace is
unstable for a short window and can be updated with new information before
being re-stabilised.  Over repeated interactions a belief may drift,
strengthen, or be superseded entirely.

This module models beliefs as **first-class updateable records** rather than
fixed graph node metadata.  Key properties:

* **Confidence** tracks how firmly the system holds a belief, on ``[0, 1]``.
  It rises when supporting evidence arrives, falls when contradictions appear.
* **Provenance** (``evidence_refs``) links back to the raw ``EventRecord``
  or graph node IDs that produced this belief, enabling explanations and
  audit trails.
* **Revision history** captures every change as an immutable
  :class:`BeliefRevision` so the full episodic arc is preserved.
* **Source** distinguishes ``"stated"`` (user said it directly),
  ``"inferred"`` (derived from pattern analysis), and ``"revised"``
  (updated by the offline consolidation worker).

Design notes
------------
- ``BeliefRecord`` is a plain dataclass — no storage dependency.
- The offline worker (:mod:`core.offline.pipeline`) updates beliefs; the
  online path only reads them.
- Graph node IDs are separate from belief IDs so a single graph node can
  contribute to many beliefs and vice-versa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# BeliefRevision
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BeliefRevision:
    """An immutable record of a single belief update.

    Attributes
    ----------
    timestamp:
        When the revision occurred (ISO-8601 UTC).
    previous_confidence:
        Confidence value *before* the update.
    new_confidence:
        Confidence value *after* the update.
    reason:
        Human-readable description of why the belief changed (e.g.
        ``"contra-evidence from event #42"``).
    evidence_ref:
        Optional ID of the event record or graph node that triggered this
        revision.
    """

    timestamp: str
    previous_confidence: float
    new_confidence: float
    reason: str = ""
    evidence_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "previous_confidence": self.previous_confidence,
            "new_confidence": self.new_confidence,
            "reason": self.reason,
            "evidence_ref": self.evidence_ref,
        }


# ---------------------------------------------------------------------------
# BeliefRecord
# ---------------------------------------------------------------------------


@dataclass
class BeliefRecord:
    """A single revisable belief held by the system about a user.

    Attributes
    ----------
    id:
        Unique belief identifier (UUID).
    user_id:
        Owner of this belief.
    key:
        Stable semantic key for de-duplication (e.g. ``"self:worth"``).
        Two beliefs with the same key for the same user are treated as the
        same belief and merged.
    text:
        Human-readable description of the belief content.
    confidence:
        Current confidence in ``[0, 1]``.  ``0.5`` is the neutral starting
        point for inferred beliefs.  Stated beliefs start at ``0.8``.
    source:
        How this belief was acquired: ``"stated"`` | ``"inferred"`` |
        ``"revised"``.
    domain:
        Broad life/work domain (e.g. ``"career"``, ``"relationships"``).
    tags:
        Arbitrary keyword tags for fast filtering.
    evidence_refs:
        IDs of events, graph nodes, or other beliefs that support this
        belief.
    revision_history:
        Ordered list of :class:`BeliefRevision` objects, oldest first.
    created_at:
        ISO-8601 UTC creation timestamp.
    updated_at:
        ISO-8601 UTC last-update timestamp.
    """

    user_id: str
    text: str
    key: str = field(default_factory=lambda: f"belief:{uuid4().hex[:8]}")
    id: str = field(default_factory=lambda: str(uuid4()))
    confidence: float = 0.5
    source: str = "inferred"
    domain: str = ""
    tags: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    revision_history: list[BeliefRevision] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    # ------------------------------------------------------------------
    # Update helpers
    # ------------------------------------------------------------------

    def apply_evidence(
        self,
        *,
        confidence_delta: float,
        reason: str,
        evidence_ref: str | None = None,
    ) -> None:
        """Update confidence by *confidence_delta* and record the revision.

        Parameters
        ----------
        confidence_delta:
            Positive values strengthen the belief; negative values weaken it.
            The result is clamped to ``[0, 1]``.
        reason:
            Why the update happened (stored in revision history).
        evidence_ref:
            Optional ID of the triggering event / node.
        """
        prev = self.confidence
        self.confidence = max(0.0, min(1.0, self.confidence + confidence_delta))
        self.updated_at = _utc_now()
        self.revision_history.append(
            BeliefRevision(
                timestamp=self.updated_at,
                previous_confidence=prev,
                new_confidence=self.confidence,
                reason=reason,
                evidence_ref=evidence_ref,
            )
        )
        if evidence_ref and evidence_ref not in self.evidence_refs:
            self.evidence_refs.append(evidence_ref)

    def add_evidence_ref(self, ref: str) -> None:
        """Add a supporting reference without changing confidence."""
        if ref not in self.evidence_refs:
            self.evidence_refs.append(ref)
            self.updated_at = _utc_now()

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "key": self.key,
            "text": self.text,
            "confidence": self.confidence,
            "source": self.source,
            "domain": self.domain,
            "tags": list(self.tags),
            "evidence_refs": list(self.evidence_refs),
            "revision_history": [r.to_dict() for r in self.revision_history],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BeliefRecord:
        """Reconstruct from a dict produced by :meth:`to_dict`."""
        revisions = [
            BeliefRevision(**r) for r in data.get("revision_history", [])
        ]
        return cls(
            id=data.get("id", str(uuid4())),
            user_id=data["user_id"],
            key=data.get("key", f"belief:{uuid4().hex[:8]}"),
            text=data["text"],
            confidence=float(data.get("confidence", 0.5)),
            source=data.get("source", "inferred"),
            domain=data.get("domain", ""),
            tags=list(data.get("tags", [])),
            evidence_refs=list(data.get("evidence_refs", [])),
            revision_history=revisions,
            created_at=data.get("created_at", _utc_now()),
            updated_at=data.get("updated_at", _utc_now()),
        )
