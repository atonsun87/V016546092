"""Core boundary contracts for SELF-OS.

Every cross-domain interaction in SELF-OS should go through one of the
``Protocol`` classes defined here rather than importing concrete
implementations across domain boundaries.  This keeps the dependency graph
acyclic and makes each subsystem independently testable with lightweight fakes.

Design notes
------------
* All protocols use ``typing.Protocol`` (structural subtyping) so that
  implementations do not need to inherit from anything — they just need to
  match the method signatures.

* ``async def`` methods are used where I/O is expected.  Sync methods are used
  only for pure, in-process operations.

* Each protocol covers exactly one domain responsibility.  New responsibilities
  should get a new protocol rather than extending an existing one.

Allowed dependency graph (see ``ARCHITECTURE_PRINCIPLES.md``)::

    interfaces  →  EventAppender
    online      →  EventAppender
    agents      →  RetrievalProvider, PolicyEnforcer
    consolidation → EventAppender, BeliefRevisor
    retrieval   →  PolicyEnforcer
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# EventAppender
# ---------------------------------------------------------------------------


@runtime_checkable
class EventAppender(Protocol):
    """Append a raw event to the durable, append-only event log.

    The online phase (``ObserveStage``) uses this contract to persist every
    incoming signal *before* any heavy processing begins.  The offline
    consolidation phase reads these events later.

    Parameters
    ----------
    event:
        The event object to persist.  Must have at least ``name`` (str),
        ``payload`` (dict), and ``timestamp`` (str) attributes.
    user_id:
        Explicit user scope.  When empty the implementation may infer it
        from ``event.payload["user_id"]``.
    """

    async def append(self, event: Any, *, user_id: str = "") -> None: ...


# ---------------------------------------------------------------------------
# ConsolidationRunner
# ---------------------------------------------------------------------------


@runtime_checkable
class ConsolidationRunner(Protocol):
    """Execute one offline consolidation cycle.

    A single call to ``run_once`` should:

    1. Read unprocessed raw events from the ``EventStore``.
    2. Cluster related events into candidate episodic traces.
    3. Build or update ``EpisodicMemory`` nodes in the knowledge graph.
    4. Derive or revise ``DerivedBelief`` objects.
    5. Recalculate salience scores.
    6. Compress or decay low-value memory traces.

    The implementation must **not** write to any active session context.
    """

    async def run_once(self) -> None: ...


# ---------------------------------------------------------------------------
# BeliefRevisor
# ---------------------------------------------------------------------------


@runtime_checkable
class BeliefRevisor(Protocol):
    """Revise beliefs in response to new evidence.

    Parameters
    ----------
    event_ids:
        IDs of the ``RawSignal`` or ``Event`` objects that triggered the
        revision.  These are stored as ``source_signal_ids`` in the revised
        belief so that the provenance chain is preserved.
    """

    async def revise_from_events(self, event_ids: list[str]) -> None: ...


# ---------------------------------------------------------------------------
# RetrievalProvider
# ---------------------------------------------------------------------------


@runtime_checkable
class RetrievalProvider(Protocol):
    """Return a scoped, ranked view of memory for a given query.

    Implementations must apply policy filtering (via ``PolicyEnforcer``) before
    returning results.  Callers must never receive unrestricted storage access.

    Parameters
    ----------
    query:
        A ``RetrievalQueryContext`` (or compatible mapping) describing the
        retrieval intent, confidence threshold, limit, and scope.

    Returns
    -------
    Any
        A ``MemoryView`` or equivalent list of ranked, policy-filtered results,
        each paired with a score breakdown and provenance reference.
    """

    def retrieve_view(self, query: Any) -> Any: ...


# ---------------------------------------------------------------------------
# PolicyEnforcer
# ---------------------------------------------------------------------------


@runtime_checkable
class PolicyEnforcer(Protocol):
    """Apply scope and privacy rules to a retrieval or action request.

    All agent reads and writes must pass through a ``PolicyEnforcer`` before
    touching the knowledge graph.  This is the single enforcement point for:

    * per-agent permission scopes,
    * memory type filters (e.g. ``read:emotions`` consent flag),
    * redaction of sensitive node fields.

    Parameters
    ----------
    actor:
        Identifier for the requesting agent or interface (e.g. ``"telegram"``,
        ``"proactive_scheduler"``).
    intent:
        Human-readable description of why the memory is being accessed (used
        for audit logging).
    query:
        The raw query object.  The implementation filters or transforms it
        according to the actor's permission scope.

    Returns
    -------
    Any
        A policy-filtered query that downstream retrieval can execute safely.
    """

    def scoped_query(self, actor: str, intent: str, query: Any) -> Any: ...
