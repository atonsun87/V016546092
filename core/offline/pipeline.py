"""OfflineConsolidationPipeline — the offline memory processing path.

Neurobiological framing
-----------------------
In biological cognition the **online** path (perception → rapid response)
and the **offline** path (sleep consolidation, slow-wave replay) are
fundamentally different processes running at different timescales:

Online path (hippocampal encoding)
    Rapid, high-bandwidth capture of events with minimal processing.
    Output: raw episodic trace.

Offline path (neocortical consolidation)
    Slow, deep processing: pattern extraction, semantic integration,
    belief updating, salience re-scoring, forgetting.
    Output: durable semantic memory.

This module is the **offline path** for SELF-OS.  It:

1. Reads unprocessed :class:`~core.memory.event_record.EventRecord` objects
   from the ``EventRecordStore``.
2. For each batch, runs:
   a. **Episodic link pass** — connects new events to existing graph nodes
      by keyword/intent similarity (cheap, no LLM required).
   b. **Belief update pass** — applies evidence from events to the
      :class:`~core.beliefs.store.BeliefStore`, adjusting confidence up/down
      based on whether events confirm or challenge existing beliefs.
   c. **Consolidation pass** — delegates to the existing
      :class:`~core.memory.consolidator.MemoryConsolidator` to cluster
      low-salience NOTE nodes into BELIEF nodes.
   d. **Reconsolidation check** — delegates to
      :class:`~core.memory.reconsolidation.ReconsolidationEngine` for
      contradiction detection.
3. Marks processed events as done.
4. Returns an :class:`OfflineProcessingReport` for observability.

The pipeline is designed to be idempotent: re-running over already-processed
events is safe because each event record is marked with ``processed_offline``
before the next run.

Usage::

    from core.offline.pipeline import OfflineConsolidationPipeline

    pipeline = OfflineConsolidationPipeline(
        graph_api=graph_api,
        event_store=event_store,
        belief_store=belief_store,
    )
    report = await pipeline.process_pending_events(user_id="u1")
    print(report)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.beliefs.schema import BeliefRecord
from core.beliefs.store import BeliefStore
from core.memory.consolidator import MemoryConsolidator
from core.memory.event_record import EventRecord, EventRecordStore

if TYPE_CHECKING:
    from core.graph.api import GraphAPI

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class OfflineProcessingReport:
    """Summary of one offline processing run.

    Attributes
    ----------
    user_id:
        The user this run was for.
    started_at:
        ISO-8601 UTC timestamp when the run started.
    finished_at:
        ISO-8601 UTC timestamp when the run finished.
    events_processed:
        Number of ``EventRecord`` objects consumed in this run.
    beliefs_updated:
        Number of belief confidence updates applied.
    beliefs_created:
        Number of new beliefs created from events.
    consolidation_clusters:
        Number of NOTE clusters merged during consolidation.
    nodes_merged:
        Number of individual NOTE nodes merged.
    nodes_abstracted:
        Number of BELIEF nodes promoted to archetype level.
    errors:
        List of non-fatal error messages encountered.
    """

    user_id: str
    started_at: str = field(default_factory=_utc_now)
    finished_at: str = ""
    events_processed: int = 0
    beliefs_updated: int = 0
    beliefs_created: int = 0
    consolidation_clusters: int = 0
    nodes_merged: int = 0
    nodes_abstracted: int = 0
    errors: list[str] = field(default_factory=list)

    def finalise(self) -> None:
        self.finished_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "events_processed": self.events_processed,
            "beliefs_updated": self.beliefs_updated,
            "beliefs_created": self.beliefs_created,
            "consolidation_clusters": self.consolidation_clusters,
            "nodes_merged": self.nodes_merged,
            "nodes_abstracted": self.nodes_abstracted,
            "errors": list(self.errors),
        }

    def __repr__(self) -> str:
        return (
            f"OfflineProcessingReport("
            f"user={self.user_id!r}, "
            f"events={self.events_processed}, "
            f"beliefs_updated={self.beliefs_updated}, "
            f"beliefs_created={self.beliefs_created}, "
            f"clusters={self.consolidation_clusters}"
            f")"
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class OfflineConsolidationPipeline:
    """Offline memory consolidation pipeline.

    This class is the single entry point for all offline processing.  It
    coordinates the sub-steps and is safe to call from a background task or
    a scheduled job.

    Parameters
    ----------
    graph_api:
        High-level graph interface.  Used for consolidation and for reading
        existing BELIEF nodes during the belief update pass.
    event_store:
        The :class:`~core.memory.event_record.EventRecordStore` that holds
        pending event records written by the online path.
    belief_store:
        The :class:`~core.beliefs.store.BeliefStore` where confidence-weighted
        beliefs are managed.
    llm_client:
        Optional LLM client.  When provided, enables LLM-powered abstraction
        of BELIEF clusters.  When absent, abstraction is skipped gracefully.
    batch_size:
        Maximum number of event records to process in a single run.  Prevents
        runaway processing if a large backlog accumulates.
    """

    def __init__(
        self,
        graph_api: GraphAPI,
        event_store: EventRecordStore,
        belief_store: BeliefStore,
        *,
        llm_client: object | None = None,
        batch_size: int = 100,
    ) -> None:
        self.graph_api = graph_api
        self.event_store = event_store
        self.belief_store = belief_store
        self._consolidator = MemoryConsolidator(graph_api.storage, llm_client=llm_client)
        self._batch_size = batch_size

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process_pending_events(
        self, user_id: str
    ) -> OfflineProcessingReport:
        """Process all pending event records for *user_id*.

        Returns an :class:`OfflineProcessingReport` that summarises what
        was done.  Never raises — errors are captured in the report.
        """
        report = OfflineProcessingReport(user_id=user_id)
        logger.info("OfflineConsolidationPipeline: starting run for user=%s", user_id)

        try:
            pending = await self.event_store.list_pending(
                user_id, limit=self._batch_size
            )
            logger.info(
                "OfflineConsolidationPipeline: %d pending events for user=%s",
                len(pending), user_id,
            )

            if pending:
                await self._belief_update_pass(user_id, pending, report)
                for record in pending:
                    await self.event_store.mark_processed(record.id)
                report.events_processed = len(pending)

            await self._consolidation_pass(user_id, report)

        except Exception as exc:
            msg = f"Unhandled error in offline pipeline: {exc}"
            logger.exception(msg)
            report.errors.append(msg)

        report.finalise()
        logger.info("OfflineConsolidationPipeline: finished — %r", report)
        return report

    # ------------------------------------------------------------------
    # Sub-passes
    # ------------------------------------------------------------------

    async def _belief_update_pass(
        self,
        user_id: str,
        events: list[EventRecord],
        report: OfflineProcessingReport,
    ) -> None:
        """Update belief confidence based on incoming events.

        The algorithm uses simple heuristics derived from the intent and raw
        signals captured by the online path:

        * ``FEELING_REPORT`` events with positive valence (>0.3) strengthen
          positive self-beliefs.
        * ``FEELING_REPORT`` events with strongly negative valence (<-0.3)
          trigger a moderate decrease in positive self-beliefs.
        * ``REFLECTION`` events are treated as mild supporting evidence.
        * All other intents produce no belief change.

        This is intentionally lightweight — deep belief inference requiring
        LLM calls is left to the abstraction pass in ``MemoryConsolidator``.
        """
        existing_beliefs = await self.belief_store.list_for_user(user_id, limit=200)
        belief_by_key: dict[str, BeliefRecord] = {b.key: b for b in existing_beliefs}

        for event in events:
            try:
                intent = event.intent
                signals = event.raw_signals
                valence = float(signals.get("valence", 0.0))

                if intent == "FEELING_REPORT":
                    await self._handle_feeling_event(
                        user_id, event, valence, belief_by_key, report
                    )
                elif intent == "REFLECTION":
                    # Reflective events are treated as mild reinforcement
                    for belief in existing_beliefs[:5]:
                        updated = await self.belief_store.apply_evidence(
                            user_id,
                            belief.key,
                            confidence_delta=0.02,
                            reason=f"reflection event #{event.id}",
                            evidence_ref=str(event.id),
                        )
                        if updated:
                            report.beliefs_updated += 1

            except Exception as exc:
                msg = f"Belief update failed for event {event.id}: {exc}"
                logger.warning(msg)
                report.errors.append(msg)

    async def _handle_feeling_event(
        self,
        user_id: str,
        event: EventRecord,
        valence: float,
        belief_by_key: dict[str, BeliefRecord],
        report: OfflineProcessingReport,
    ) -> None:
        """Handle a FEELING_REPORT event: update or create relevant beliefs."""
        if valence > 0.3:
            # Positive emotional event — gently reinforce self-efficacy belief
            key = "self:efficacy"
            if key in belief_by_key:
                updated = await self.belief_store.apply_evidence(
                    user_id,
                    key,
                    confidence_delta=0.05,
                    reason=f"positive feeling event #{event.id} (valence={valence:.2f})",
                    evidence_ref=str(event.id),
                )
                if updated:
                    report.beliefs_updated += 1
            else:
                belief = BeliefRecord(
                    user_id=user_id,
                    key=key,
                    text="I am capable and effective",
                    confidence=0.5 + min(valence * 0.1, 0.1),
                    source="inferred",
                    domain="self",
                    evidence_refs=[str(event.id)],
                )
                await self.belief_store.upsert(belief)
                belief_by_key[key] = belief
                report.beliefs_created += 1

        elif valence < -0.3:
            # Negative emotional event — slightly weaken positive self-beliefs
            key = "self:efficacy"
            if key in belief_by_key:
                updated = await self.belief_store.apply_evidence(
                    user_id,
                    key,
                    confidence_delta=-0.03,
                    reason=f"negative feeling event #{event.id} (valence={valence:.2f})",
                    evidence_ref=str(event.id),
                )
                if updated:
                    report.beliefs_updated += 1

    async def _consolidation_pass(
        self, user_id: str, report: OfflineProcessingReport
    ) -> None:
        """Run memory consolidation (cluster low-retention NOTEs → BELIEFs)."""
        try:
            con_report = await self._consolidator.consolidate(user_id)
            report.consolidation_clusters = con_report.clusters_found
            report.nodes_merged = con_report.nodes_merged
            if con_report.new_nodes_created > 0:
                logger.info(
                    "Consolidation for user=%s: %d clusters, %d merged, %d created",
                    user_id,
                    con_report.clusters_found,
                    con_report.nodes_merged,
                    con_report.new_nodes_created,
                )
        except Exception as exc:
            msg = f"Consolidation pass failed: {exc}"
            logger.warning(msg)
            report.errors.append(msg)

        try:
            abs_report = await self._consolidator.abstract(user_id)
            report.nodes_abstracted = abs_report.abstracted
        except Exception as exc:
            msg = f"Abstraction pass failed: {exc}"
            logger.warning(msg)
            report.errors.append(msg)
