"""MessageProcessor — thin OODA orchestrator.

Two modes of operation controlled by ``background_mode``:

  **background_mode=True** (production/chat):
    OBSERVE → reply from *existing* graph context → return fast.
    ORIENT + DECIDE run asynchronously via ``asyncio.create_task``.

  **background_mode=False** (tests / CLI / sync):
    Classic sequential: OBSERVE → ORIENT → DECIDE → ACT.

Each stage is a self-contained class with an ``async def run()`` method.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from config import USE_LLM  # noqa: F401 — backward compat
from core.context.builder import GraphContextBuilder
from core.context.session_memory import SessionMemory
from core.graph.api import GraphAPI
from core.graph.model import Edge, Node
from core.insights.engine import InsightEngine
from core.journal.storage import JournalStorage
from core.llm.embedding_service import EmbeddingService
from core.llm_client import LLMClient, MockLLMClient
from core.memory.provenance import ProvenanceStore
from core.mood.tracker import MoodTracker
from core.observability.metrics import MetricsCollector
from core.parts.memory import PartsMemory
from core.pipeline.event_store import EventStore
from core.pipeline.events import EventBus
from core.pipeline.stage_observe import ObserveStage
from core.pipeline.stage_orient import OrientStage
from core.pipeline.stage_decide import DecideStage
from core.pipeline.stage_act import ActStage
from core.pipeline.stage_observe import _sanitize_text  # noqa: F401 — backward compat
from core.search.qdrant_storage import QdrantVectorStorage
from core.tools.base import ToolRegistry
from core.tools.memory_tools import build_default_tools

if TYPE_CHECKING:
    from core.analytics.calibrator import ThresholdCalibrator
    from core.neuro.bridge import NeuroBridge
    from core.pipeline.orchestrator import AgentOrchestrator

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessResult:
    intent: str
    reply_text: str
    nodes: list[Node]
    edges: list[Edge]


class MessageProcessor:
    """Thin orchestrator: OBSERVE → ORIENT → DECIDE → ACT.

    In ``background_mode`` the heavy LLM extraction (ORIENT+DECIDE) is
    deferred to a background ``asyncio.Task`` so that the user receives
    a reply immediately based on the *already accumulated* graph context.
    """

    def __init__(
        self,
        graph_api: GraphAPI,
        journal: JournalStorage,
        qdrant: QdrantVectorStorage,
        session_memory: SessionMemory,
        llm_client: LLMClient | None = None,
        embedding_service: EmbeddingService | None = None,
        calibrator: "ThresholdCalibrator | None" = None,
        use_llm: bool | None = None,  # backward compat, ignored
        event_bus: EventBus | None = None,
        background_mode: bool = False,
        neuro_bridge: "NeuroBridge | None" = None,
        orchestrator: "AgentOrchestrator | None" = None,
        event_store: EventStore | None = None,
        provenance_store: ProvenanceStore | None = None,
        metrics: MetricsCollector | None = None,
    ) -> None:
        self.graph_api = graph_api
        self.journal = journal
        self.session_memory = session_memory
        self.calibrator = calibrator
        self.background_mode = background_mode
        self.provenance_store = provenance_store
        # Use the shared singleton by default so production metrics are always
        # collected.  Tests that need isolation should pass an explicit
        # ``MetricsCollector()`` instance to avoid cross-test contamination.
        self._metrics = metrics or MetricsCollector.get_instance()
        effective_llm = llm_client or MockLLMClient()
        effective_bus = event_bus or EventBus()

        self.context_builder = GraphContextBuilder(graph_api.storage, embedding_service=embedding_service)
        self.pattern_analyzer = self.context_builder.pattern_analyzer
        self.mood_tracker = MoodTracker(graph_api.storage)
        self.parts_memory = PartsMemory(graph_api.storage)

        # Expose for tests / external access
        self.llm_client = effective_llm
        self.event_bus = effective_bus
        self.embedding_service = embedding_service
        self.qdrant = qdrant
        self.live_reply_enabled: bool = os.getenv("LIVE_REPLY_ENABLED", "true").lower() == "true"

        self._calibrator_loaded: set[str] = set()
        self._pending_tasks: list[asyncio.Task] = []
        self._orchestrator = orchestrator
        self._neuro_bridge = neuro_bridge

        # Insight engine — runs after every analysis pass
        self._insight_engine = InsightEngine(graph_api=graph_api)

        # Tool registry — tools are bound per-user in process_message
        self.tool_registry = ToolRegistry()

        # Build OODA stages
        self._observe = ObserveStage(
            journal=journal,
            session_memory=session_memory,
            event_bus=effective_bus,
            event_store=event_store,
        )
        self._orient = OrientStage(
            graph_api=graph_api,
            llm_client=effective_llm,
            embedding_service=embedding_service,
            qdrant=qdrant,
            context_builder=self.context_builder,
            session_memory=session_memory,
        )
        self._decide = DecideStage(
            graph_api=graph_api,
            mood_tracker=self.mood_tracker,
            parts_memory=self.parts_memory,
            neuro_bridge=neuro_bridge,
        )
        self._act = ActStage(
            llm_client=effective_llm,
            session_memory=session_memory,
            event_bus=effective_bus,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def process_message(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "cli",
        timestamp: str | None = None,
    ) -> ProcessResult:
        with self._metrics.timed("pipeline_ms"):
            result = await self._process_message_inner(
                user_id=user_id,
                text=text,
                source=source,
                timestamp=timestamp,
            )
        self._metrics.increment("messages_processed")
        return result

    async def _process_message_inner(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "cli",
        timestamp: str | None = None,
    ) -> ProcessResult:
        # Auto-load calibrator thresholds once per user
        if self.calibrator and user_id not in self._calibrator_loaded:
            try:
                await self.calibrator.load(user_id)
            except Exception as exc:
                logger.warning("ThresholdCalibrator.load failed: %s", exc)
            self._calibrator_loaded.add(user_id)

        # 1. OBSERVE — sanitise, journal, session, classify
        obs = await self._observe.run(user_id, text, source=source, timestamp=timestamp)

        if self.background_mode:
            return await self._process_background(user_id, obs)

        return await self._process_sync(user_id, obs)

    # ------------------------------------------------------------------
    # Sync path (tests / CLI): OBSERVE → ORIENT → DECIDE → ACT
    # ------------------------------------------------------------------

    async def _process_sync(self, user_id: str, obs) -> ProcessResult:
        """Full sequential pipeline — used in tests and sync CLI."""

        # 2. ORIENT — LLM extract, graph persist, embed, search, context
        ori = await self._orient.run(user_id, obs.text, obs.intent)

        # 3. DECIDE — policy, task links, conflicts, mood
        dec = await self._decide.run(
            user_id=user_id,
            created_nodes=ori.created_nodes,
            created_edges=ori.created_edges,
            retrieved_context=ori.retrieved_context,
            graph_context=ori.graph_context,
        )

        all_edges = [*ori.created_edges, *dec.created_edges]

        # Inject NeuroCore brain state into graph context (if available)
        if dec.brain_state:
            ori.graph_context["brain_state"] = dec.brain_state

        # 3b. ORCHESTRATOR (optional) — run agent chain and merge results
        if self._orchestrator is not None:
            try:
                from core.pipeline.orchestrator import AgentContext
                agent_ctx = AgentContext(
                    user_id=user_id,
                    text=obs.text,
                    intent=ori.intent,
                    graph_context=ori.graph_context,
                    mood_context=dec.mood_context,
                    parts_context=dec.parts_context,
                )
                orch_result = await self._orchestrator.run(agent_ctx)
                if orch_result.reply_fragment:
                    ori.graph_context["orchestrator_fragment"] = orch_result.reply_fragment
                if orch_result.metadata:
                    ori.graph_context.setdefault("orchestrator_meta", {}).update(orch_result.metadata)
            except Exception as exc:
                logger.warning("AgentOrchestrator failed: %s — continuing without it", exc)

        # 3c. INSIGHT — detect cross-pattern insights from new + historical data
        insight_nodes = await self._insight_engine.run(
            user_id=user_id,
            new_nodes=ori.created_nodes,
            new_edges=all_edges,
            graph_context=ori.graph_context,
        )
        if insight_nodes:
            ori.graph_context["recent_insights"] = [
                {"title": n.name, "description": n.text, "severity": n.metadata.get("severity", "info")}
                for n in insight_nodes
            ]

        # 4. ACT — reply, session memory, event
        act = await self._act.run(
            user_id=user_id,
            text=obs.text,
            intent=ori.intent,
            created_nodes=ori.created_nodes,
            created_edges=all_edges,
            graph_context=ori.graph_context,
            mood_context=dec.mood_context,
            parts_context=dec.parts_context,
            retrieved_context=ori.retrieved_context,
            policy=dec.policy,
        )

        self._metrics.set_gauge("node_count", float(len(ori.created_nodes)))

        return ProcessResult(
            intent=ori.intent,
            reply_text=act.reply_text,
            nodes=ori.created_nodes,
            edges=all_edges,
        )

    # ------------------------------------------------------------------
    # Background path (production chat): fast reply → background analysis
    # ------------------------------------------------------------------

    async def _process_background(self, user_id: str, obs) -> ProcessResult:
        """Fast path: reply from existing context, extraction in background.

        1. Build graph context from ALREADY EXTRACTED data (prev messages).
        2. Get current mood snapshot (fast DB read).
        3. Register per-user tools + inject tool descriptions into context.
        4. Generate reply using existing context (1 LLM call).
        5. Spawn background task for: ORIENT + DECIDE + INSIGHT.
        """

        # ── Existing context (fast, no LLM) ──────────────────────
        graph_context = await self.context_builder.build(user_id)
        mood_context = await self.mood_tracker.get_current(user_id)
        parts_context = graph_context.get("known_parts", [])[:3]

        # ── Inject brain_state from NeuroCore (if available) ────────
        if self._neuro_bridge is not None:
            try:
                brain_state = await self._neuro_bridge.get_latest_brain_state(user_id)
                if brain_state:
                    graph_context["brain_state"] = brain_state
            except Exception as exc:
                logger.debug("Background brain_state fetch failed: %s", exc)

        # ── Register per-user tools ──────────────────────────────
        self._register_user_tools(user_id)
        graph_context["available_tools"] = self.tool_registry.schemas_compact()

        # ── Include recent insights in context ───────────────────
        recent_insights = await self._load_recent_insights(user_id)
        if recent_insights:
            graph_context["recent_insights"] = recent_insights

        # ── ACT — reply based on accumulated memory ──────────────
        act = await self._act.run(
            user_id=user_id,
            text=obs.text,
            intent=obs.intent,
            created_nodes=[],
            created_edges=[],
            graph_context=graph_context,
            mood_context=mood_context or {},
            parts_context=parts_context,
            retrieved_context=[],
            policy="REFLECT",
        )

        # ── Handle tool calls in reply ───────────────────────────
        final_reply = await self._handle_tool_calls(
            user_id=user_id,
            reply_text=act.reply_text,
            text=obs.text,
            intent=obs.intent,
            graph_context=graph_context,
            mood_context=mood_context or {},
            parts_context=parts_context,
        )

        # ── Background: heavy LLM extraction + analysis ──────────
        task = asyncio.create_task(
            self._background_analysis(user_id, obs.text, obs.intent),
        )
        self._pending_tasks.append(task)

        return ProcessResult(
            intent=obs.intent,
            reply_text=final_reply,
            nodes=[],
            edges=[],
        )

    async def _background_analysis(
        self, user_id: str, text: str, intent: str,
    ) -> None:
        """ORIENT + DECIDE + INSIGHT in background — enriches graph without blocking chat."""
        try:
            ori = await self._orient.run(user_id, text, intent)
            dec = await self._decide.run(
                user_id=user_id,
                created_nodes=ori.created_nodes,
                created_edges=ori.created_edges,
                retrieved_context=ori.retrieved_context,
                graph_context=ori.graph_context,
            )

            all_edges = [*ori.created_edges, *dec.created_edges]

            # Run insight engine on freshly extracted data
            insight_nodes = await self._insight_engine.run(
                user_id=user_id,
                new_nodes=ori.created_nodes,
                new_edges=all_edges,
                graph_context=ori.graph_context,
            )

            logger.info(
                "BG analysis done: nodes=%d edges=%d policy=%s insights=%d",
                len(ori.created_nodes),
                len(all_edges),
                dec.policy,
                len(insight_nodes),
            )
        except Exception as exc:
            logger.error("Background analysis failed: %s", exc)

    async def flush_pending(self) -> None:
        """Await all background tasks. Call in tests / shutdown."""
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
            self._pending_tasks.clear()

    # ------------------------------------------------------------------
    # Tool & insight helpers
    # ------------------------------------------------------------------

    def _register_user_tools(self, user_id: str) -> None:
        """Register default tools bound to the current user."""
        if self.tool_registry.tools:
            return  # already registered
        for tool in build_default_tools(
            graph_api=self.graph_api,
            qdrant=self.qdrant,
            user_id=user_id,
            embedding_service=self.embedding_service,
        ):
            self.tool_registry.register(tool)

    async def _load_recent_insights(self, user_id: str, limit: int = 3) -> list[dict]:
        """Load the N most recent INSIGHT nodes from the graph."""
        try:
            insights = await self.graph_api.storage.find_nodes(
                user_id, node_type="INSIGHT", limit=limit,
            )
            insights.sort(
                key=lambda n: n.metadata.get("created_at", n.created_at or ""),
                reverse=True,
            )
            return [
                {
                    "title": n.name or "",
                    "description": (n.text or "")[:200],
                    "severity": n.metadata.get("severity", "info"),
                    "pattern_type": n.metadata.get("pattern_type", ""),
                }
                for n in insights[:limit]
            ]
        except Exception as exc:
            logger.warning("Failed to load insights: %s", exc)
            return []

    async def _handle_tool_calls(
        self,
        user_id: str,
        reply_text: str,
        text: str,
        intent: str,
        graph_context: dict,
        mood_context: dict,
        parts_context: list,
    ) -> str:
        """Parse tool calls in LLM reply, execute, and re-generate if needed."""
        calls = self.tool_registry.parse_tool_calls(reply_text)
        if not calls:
            return reply_text

        # Execute tools
        tool_results: list[str] = []
        for tool_name, args in calls[:3]:  # max 3 tools per turn
            result = await self.tool_registry.dispatch(tool_name, args)
            if result.success:
                import json
                tool_results.append(
                    f"[{tool_name}]: {json.dumps(result.data, ensure_ascii=False, default=str)[:500]}"
                )
            else:
                tool_results.append(f"[{tool_name}]: ошибка — {result.error}")

        if not tool_results:
            return reply_text

        # Re-generate reply with tool results injected into context
        graph_context["tool_results"] = "\n".join(tool_results)
        act = await self._act.run(
            user_id=user_id,
            text=text,
            intent=intent,
            created_nodes=[],
            created_edges=[],
            graph_context=graph_context,
            mood_context=mood_context,
            parts_context=parts_context,
            retrieved_context=[],
            policy="REFLECT",
        )
        return act.reply_text

    # ------------------------------------------------------------------
    # Aliases & utilities kept on processor for backward compatibility
    # ------------------------------------------------------------------

    async def process(
        self,
        user_id: str,
        text: str,
        *,
        source: str = "cli",
        timestamp: str | None = None,
    ) -> ProcessResult:
        return await self.process_message(user_id=user_id, text=text, source=source, timestamp=timestamp)

    async def build_weekly_report(self, user_id: str) -> str:
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)

        snapshots = await self.graph_api.storage.get_mood_snapshots(user_id, limit=30)
        weekly = []
        for snapshot in snapshots:
            ts = snapshot.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt >= week_ago:
                weekly.append(snapshot)

        graph_context = await self.context_builder.build(user_id)
        top_parts = graph_context.get("known_parts", [])[:3]
        active_values = graph_context.get("known_values", [])[:5]

        if not weekly:
            part_line = ", ".join(p.get("name") or p.get("key") or "part" for p in top_parts) or "нет"
            value_line = ", ".join(v.get("name") or v.get("key") or "value" for v in active_values) or "нет"
            return (
                "📊 Недельный отчёт\n"
                "За последние 7 дней mood-срезов пока нет.\n"
                f"Топ частей: {part_line}\n"
                f"Активные ценности: {value_line}"
            )

        avg_valence = sum(float(s.get("valence_avg", 0.0)) for s in weekly) / len(weekly)
        avg_arousal = sum(float(s.get("arousal_avg", 0.0)) for s in weekly) / len(weekly)
        avg_dominance = sum(float(s.get("dominance_avg", 0.0)) for s in weekly) / len(weekly)

        labels: dict[str, int] = {}
        for snapshot in weekly:
            label = str(snapshot.get("dominant_label") or "").strip()
            if not label:
                continue
            labels[label] = labels.get(label, 0) + 1
        top_label = max(labels.items(), key=lambda item: item[1])[0] if labels else "не определено"

        part_line = ", ".join(
            f"{p.get('name') or p.get('key') or 'part'} ({p.get('appearances', 1)})"
            for p in top_parts
        ) or "нет"
        value_line = ", ".join(v.get("name") or v.get("key") or "value" for v in active_values) or "нет"

        return (
            "📊 Недельный отчёт\n"
            f"Срезов: {len(weekly)}\n"
            f"Среднее состояние: valence={avg_valence:.2f}, arousal={avg_arousal:.2f}, dominance={avg_dominance:.2f}\n"
            f"Чаще всего: {top_label}\n"
            f"Топ частей: {part_line}\n"
            f"Активные ценности: {value_line}"
        )
