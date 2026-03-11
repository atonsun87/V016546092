# Changelog

All notable changes to SELF-OS are documented in this file.

Format: `[vX.Y.Z — Title] (date)` followed by categorised changes.

---

## v0.4.0 — Trust Layer + Observability (2026-03-11)

### New Modules

- **`core/memory/provenance.py`** — Memory provenance and audit trail:
  - `MemorySource` (StrEnum) — six origin categories: `user`, `llm`,
    `onboarding`, `agent`, `system`, `import`
  - `EditRecord` — immutable record of a single field edit (field, author,
    previous value, timestamp)
  - `ProvenanceRecord` — full provenance for a node: source, author,
    session_id, pipeline_stage, and ordered edit history
  - `ProvenanceStore` — async SQLite-backed persistence with `save`,
    `get`, `append_edit`, `list_for_user`, `count_by_source`, `delete`

- **`core/memory/trust.py`** — User control over stored memories:
  - `MemoryTrustService.edit_node_text` / `edit_node_name` — update node
    content with a full audit trail written to `ProvenanceStore`
  - `MemoryTrustService.soft_delete_node` — reversible deletion (sets
    `is_deleted = 1`, node excluded from normal queries, provenance kept)
  - `MemoryTrustService.hard_delete_node` — permanent deletion, cascades
    to edges and provenance record
  - `MemoryTrustService.export_user_data` — portable `MemoryExport`
    (JSON) containing all nodes, edges, and provenance for a user
  - `MemoryTrustService.get_memory_provenance` — inspect origin of any
    node with ownership enforcement

- **`core/observability/metrics.py`** — Lightweight in-process metrics:
  - `MetricsCollector` — thread-safe singleton with counters, latency
    ring-buffers (500-sample, p50/p95), and float gauges
  - `MetricsCollector.timed(name)` — context manager for latency capture
  - `SystemSnapshot.to_dict()` — JSON-serialisable health snapshot
  - `MetricsCollector.reset_instance()` — test isolation helper

- **`core/observability/__init__.py`** — New `core.observability` package

### Documentation

- **`STABILIZATION.md`** — Comprehensive stabilization plan with 8 priority
  areas: trust layer, observability, retrieval quality evaluation, schema/
  migration hardening, error boundaries, pipeline wiring, goal continuity,
  and product surface hardening

### Tests

- `tests/test_memory_provenance.py` — 11 tests for `ProvenanceStore`
  (save/get, overwrite, edit history accumulation, list/filter, count by
  source, delete, all MemorySource values)
- `tests/test_memory_trust.py` — 13 tests for `MemoryTrustService`
  (edit text/name, soft/hard delete, export, provenance inspection,
  wrong-user rejection, cascade edge deletion)
- `tests/test_observability_metrics.py` — 18 tests for `MetricsCollector`
  (counters, latencies, gauges, snapshot, timed, singleton, thread safety)

---

## v0.3.0 — Stage 3 Stabilization (2026-03-04)

### New Modules

- **`agents/ifs/`** — InnerCouncil IFS debate system incorporated from PR #7:
  - `agents/ifs/signals.py` — shared `PART_SIGNALS` and `EMOTION_SIGNALS`
    dictionaries (single source of truth for both IFS agents and Orchestrator)
  - `agents/ifs/parts.py` — four IFS Part agents (`CriticAgent`,
    `FirefighterAgent`, `ExileAgent`, `SelfAgent`) with optional LLM client
    support via `voice_prompt`
  - `agents/ifs/council.py` — `InnerCouncil` two-round debate orchestrator
- **`core/prediction/`** — PredictiveEngine incorporated from PR #7:
  - `core/prediction/state_model.py` — `PsycheState`, `PsycheStateForecast`,
    `InterventionImpact` DTOs with bidirectional `BrainState` conversion
  - `core/prediction/engine.py` — EWMA-based forecasting using only public
    `GraphStorage` APIs

### Bug Fixes

- **Fix #1** (`core/graph/storage.py`): Added public
  `get_avg_intervention_delta(user_id, intervention_type)` method that
  encapsulates the `intervention_outcomes` aggregation query.
- **Fix #2** (`core/prediction/engine.py`): PredictiveEngine no longer accesses
  private `_ensure_initialized()` / `_get_conn()` from GraphStorage; uses
  `get_avg_intervention_delta()` and `get_mood_snapshots()` instead.
- **Fix #3** (`agents/ifs/parts.py`): InnerCouncil Round 2 now produces
  genuinely different output — each Part agent adjusts confidence and position
  text based on peers' Round-1 positions (`council_log`).
- **Fix #4** (`core/psyche/state.py`, `core/prediction/state_model.py`):
  `PsycheState.from_brain_state()` and `PsycheState.to_brain_state()` added to
  both the user-facing and prediction-layer `PsycheState` classes.

### Architecture Improvements

- **Fix #5** (`core/pipeline/processor.py`): `MessageProcessor.__init__()` now
  accepts optional `orchestrator: AgentOrchestrator | None = None`.  In
  `_process_sync()`, if orchestrator is wired, it runs after DECIDE and merges
  `reply_fragment` and `metadata` into `graph_context`.
- **Fix #6** (`core/pipeline/processor.py`): `_process_background()` now loads
  `brain_state` from `NeuroBridge` (when available) and injects it into
  `graph_context`, matching the sync path.
- **Fix #7** (`agents/ifs/parts.py`): Each `IFSPartAgent` accepts an optional
  `llm_client` parameter; when provided, `deliberate()` generates a nuanced
  response using `voice_prompt` as system prompt via the LLM.
- **Fix #8** (`agents/ifs/signals.py`, `core/pipeline/orchestrator.py`):
  Eliminated duplicate keyword dictionaries by creating
  `agents/ifs/signals.py` as the canonical source; both `parts.py` and
  `orchestrator.py` now import from it.

### Code Quality

- **Fix #9** (`core/prediction/engine.py`): `_snapshot_to_state()` now
  extracts `cognitive_load` and `dominant_need` from snapshot data when
  available, falling back to sensible defaults.
- **Fix #10** (`core/neuro/engine.py`): Added `cleanup_dormant(user_id,
  max_age_days=90)` method that soft-deletes neurons below
  `ACTIVATION_THRESHOLD` not activated within the given days.
- **Fix #11** (`core/neuro/engine.py`): `propagate()` rewritten from recursive
  async calls to iterative BFS using `collections.deque` — eliminates deep
  call-stack risk.
- **Fix #12** (`core/neuro/engine.py`): `hebbian_strengthen()` now uses a
  single `SELECT ... WHERE source_neuron_id IN (...)` query with batch
  `UPDATE` — reduced from O(n²) to O(1) SQL round-trips.
- **Fix #13** (`core/neuro/engine.py`): `decay_cycle()` now adds
  `AND activation > 0` to its `WHERE` clause, skipping already-dormant
  neurons and reducing unnecessary DB writes.

### Documentation

- **Doc #14**: `README.md` rewritten with project description, architecture
  overview, module map, quick start, environment variables, current stage, and
  tech stack.
- **Doc #15**: `docs/ARCHITECTURE.md` updated with Stage 3 architecture
  diagram, data flow, DTO reference (with BrainState ↔ PsycheState mapping),
  database schemas, integration points, and extension guide.
- **Doc #16**: `CHANGELOG.md` created (this file).

---

## v0.2.5 — NeuroCore (2026-02-15, PR #8)

- `core/neuro/engine.py` — NeuroCore neurobiological engine with Hebbian
  learning, spreading activation, decay cycle, neurotransmitter modifiers.
- `core/neuro/schema.py` — `Neuron`, `Synapse`, `BrainState` dataclasses.
- `core/neuro/bridge.py` — `NeuroBridge` integrating NeuroCore with the OODA
  pipeline; `DecideStage` accepts optional `neuro_bridge` parameter.
- Brain state injected into `graph_context["brain_state"]` in DECIDE stage.

---

## v0.2.0 — OODA Pipeline (2026-01-20)

- `core/pipeline/processor.py` — `MessageProcessor` with background mode.
- `core/pipeline/stage_observe.py` — OBSERVE: sanitize, journal, intent.
- `core/pipeline/stage_orient.py` — ORIENT: LLM extract, graph, RAG.
- `core/pipeline/stage_decide.py` — DECIDE: policy, mood, parts.
- `core/pipeline/stage_act.py` — ACT: LLM reply generation.
- `core/context/builder.py` — `GraphContextBuilder` for ORIENT context.
- `core/mood/tracker.py` — VAD mood snapshots with trend analysis.

---

## v0.1.0 — Initial (2025-12-01)

- `core/graph/storage.py` — SQLite graph storage with nodes + edges.
- `core/graph/api.py` — `GraphAPI` high-level CRUD.
- `core/graph/model.py` — `Node`, `Edge` dataclasses.
- `core/llm_client.py` — LLM client abstraction (OpenAI-compatible).
- Basic Telegram bot integration.
