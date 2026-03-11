# SELF-OS Stabilization Plan

This document describes the concrete, ordered stabilization work needed to
harden the SELF-OS architecture before the product surface grows further.
It is a living document — mark items complete in-code as they are shipped.

---

## Why stabilization matters now

The core engine (Stages 1–4, Phases 1–4) is functionally complete.
The next risk is **architectural drift**: subsystems that were built
independently start coupling in ways that are hard to untangle later,
and user trust degrades when the system cannot explain, edit, or delete
its own memories.

Stabilization is not about removing features. It is about ensuring every
feature rests on a foundation that can be trusted, monitored, and evolved
safely.

---

## Priority 1 — Trust layer (user control over memories)

**Status: foundation shipped (v0.4.0)**

Users must be able to inspect, correct, and delete every piece of
information the system holds about them. Without this, no amount of
retrieval quality matters — users will not adopt a system they cannot
understand or control.

### Completed

- [x] `core/memory/provenance.py` — `ProvenanceStore` and `ProvenanceRecord`
      tracking the origin (user / LLM / onboarding / agent / system / import),
      author, pipeline stage, and a full edit history for every node.
- [x] `core/memory/trust.py` — `MemoryTrustService` providing:
  - `edit_node_text` / `edit_node_name` with audit trail
  - `soft_delete_node` (reversible, is_deleted flag)
  - `hard_delete_node` (permanent, cascades to edges and provenance)
  - `export_user_data` (portable JSON snapshot of all nodes, edges, provenance)
  - `get_memory_provenance` (inspect origin of any node)

### Remaining

- [ ] Wire `ProvenanceStore` into `OnboardingPlanner` so onboarding-derived
      nodes are tagged `MemorySource.ONBOARDING`.
- [ ] Wire `ProvenanceStore` into agent tool writes (`memory_tools.py`,
      `task_tool.py`) so agent-created nodes are tagged `MemorySource.AGENT`.
- [ ] In `stage_orient.py`, pass `provenance_store` from `MessageProcessor`
      and stamp each `upsert_node` call with `MemorySource.LLM` provenance.
- [ ] Expose trust operations via the Telegram interface:
  - `/memory list` — show recent memories
  - `/memory edit <id>` — edit a memory
  - `/memory delete <id>` — soft-delete a memory
  - `/memory export` — DM the user a JSON file of all their data
  - `/memory show <id>` — show provenance for a specific node
- [ ] Implement consent scopes: per-agent permission flags stored in
      `node_provenance.session_id` or a dedicated `consent` table.
  - `read:notes`, `read:emotions`, `read:beliefs`, `write:tasks`, etc.

---

## Priority 2 — Observability

**Status: foundation shipped (v0.4.0); pipeline instrumentation shipped**

You cannot stabilize what you cannot measure. The observability layer
must record what the system is doing so you can detect regressions,
performance degradation, and unexpected behaviour.

### Completed

- [x] `core/observability/metrics.py` — `MetricsCollector` (thread-safe,
      singleton) with:
  - Named counters (`messages_processed`, `nodes_created`, etc.)
  - Latency ring-buffers with p50/p95 summaries (`pipeline_ms`, `retrieval_ms`)
  - Float gauges (`active_users`, `node_count`)
  - `timed()` context manager for zero-boilerplate latency capture
  - `snapshot().to_dict()` for export / logging
- [x] `MetricsCollector` injected into `MessageProcessor` via `processor_factory.py`.
- [x] `process_message` increments `messages_processed` and times `pipeline_ms`
      for every call.

### Remaining

- [ ] Instrument `MessageProcessor.process_message` with `timed("pipeline_ms")`
      and `increment("messages_processed")`.
- [ ] Instrument `RetrievalRanker` with `timed("retrieval_ms")` and
      `increment("retrievals_performed")`.
- [ ] Instrument `NeuroCore.activate` with `timed("neuro_activate_ms")`.
- [ ] Add a `/health` endpoint (or periodic log line) that emits
      `snapshot().to_dict()` for monitoring.
- [ ] Add `set_gauge("node_count", ...)` after each pipeline run so you
      can trend knowledge graph growth.
- [ ] Define SLOs (service-level objectives):
  - Pipeline latency p95 < 3 000 ms (LLM path)
  - Pipeline latency p95 < 200 ms (no-LLM path)
  - Retrieval latency p95 < 500 ms
  - Zero unhandled exceptions per 100 messages

---

## Priority 3 — Retrieval quality evaluation

**Status: not started**

The 7-dimension `RetrievalScorer` is implemented and rule-based.
But there is no automated way to verify that retrieval is actually
*better* than a naive baseline, or that changes to the scoring weights
do not degrade recall.

### Tasks

- [ ] Create `tests/eval/test_retrieval_quality.py` with parametrised
      scenario fixtures:
  - Each fixture defines a small graph, a query context, and the
    expected top-K node IDs.
  - Tests assert that the correct nodes rank above alternatives.
- [ ] Define at least five canonical retrieval scenarios:
  1. Goal-aligned recall — query tied to active goal surfaces goal-linked nodes
  2. Emotional salience — high-emotion query surfaces emotional nodes
  3. Recency bias — recent note beats older note with same content
  4. Identity resonance — query with identity signals surfaces identity nodes
  5. Confidence filter — low-confidence nodes are ranked below high-confidence ones
- [ ] Add a `scripts/run_retrieval_eval.py` script that runs all scenarios
      and prints a precision@K report.
- [ ] Track precision@3 and precision@5 as regression metrics in CI
      (add a pytest mark so they can be run separately from unit tests).

---

## Priority 4 — Schema and migration hardening

**Status: partial**

The current migration system in `GraphStorage._ensure_initialized` uses
a `contextlib.suppress(sqlite3.OperationalError)` loop for
backward-compatible `ALTER TABLE` statements. This works but:

- Migrations have no version tracking, so it is impossible to know which
  migrations have been applied.
- Failed migrations are silently swallowed, which may leave the DB in a
  partially migrated state.

### Tasks

- [ ] Add a `schema_versions` table with one row per applied migration
      (migration ID, description, applied_at).
- [ ] Convert the `_migrations` list in `GraphStorage._ensure_initialized`
      to named, idempotent migration functions that check `schema_versions`
      before executing.
- [ ] Add a `scripts/migrate.py` CLI that applies pending migrations and
      prints a migration log.
- [ ] Write a test that starts from an empty DB and a DB that simulates an
      older schema, and verifies that both end up at the current version.

---

## Priority 5 — Error boundaries and graceful degradation

**Status: partial**

`MessageProcessor` already has some error handling, but the system can
still surface unhandled exceptions to end users under edge cases.

### Tasks

- [ ] Audit every `await` call in `MessageProcessor.process_message` and
      ensure all LLM and DB failures produce a graceful error reply, not
      a stack trace.
- [ ] Add a global exception handler to the Telegram bot (`aiogram` supports
      `router.errors.register`) that logs the exception and replies with a
      safe error message.
- [ ] Add a retry wrapper (with exponential backoff and a circuit-breaker)
      around LLM calls in `core/llm_client.py` for transient network errors.
- [ ] Test: create a test that injects a failing LLM client and verifies
      that `MessageProcessor` returns a non-error response.

---

## Priority 6 — Pipeline integration for provenance and metrics

**Status: partially shipped**

The provenance and metrics foundations are in place.  They need to be
wired into the actual pipeline so every message that flows through the
system is instrumented.

### Completed

- [x] `interfaces/processor_factory.py` constructs and injects
      `ProvenanceStore`, `MetricsCollector`, and `EventStore` into
      `MessageProcessor`.
- [x] `MessageProcessor.__init__` accepts optional `provenance_store`,
      `metrics`, and `event_store` parameters (existing tests unaffected).
- [x] `MessageProcessor.process_message` records `messages_processed` and
      `pipeline_ms` latency via `MetricsCollector`.
- [x] `ObserveStage` receives `event_store` and appends every sanitised
      message to the durable append-only log (online→offline boundary).

### Remaining

- [ ] In `stage_orient.py`, after each `upsert_node` call, call
      `provenance_store.save(...)` (pass `provenance_store` from processor).
- [ ] In `stage_act.py`, after each AgentAction write, record a
      `MemorySource.AGENT` provenance entry.

---

## Priority 7 — Goal and action continuity (Phase 5 roadmap)

**Status: not started — see `docs/roadmap.md` Phase 5**

Active goals must survive across sessions. The system should know when
a goal is blocked and surface that proactively.

### Tasks (from roadmap)

- [ ] Goal progress tracking — update goal status from completed tasks and
      agent observations.
- [ ] Blocker detection — surface stalled goals proactively.
- [ ] Action history — queryable log of past `AgentAction` records.
- [ ] Goal retrospective — periodic review of completed/abandoned goals.

---

## Priority 8 — Product surface hardening (Phase 6 roadmap)

**Status: not started — see `docs/roadmap.md` Phase 6**

- [ ] Guided onboarding UX for first-time users.
- [ ] Response quality audit — improve LLM prompts across all pipeline stages.
- [ ] User settings — configurable notification frequency, onboarding depth.

---

## Architectural principles to protect

These constraints must be preserved as new features are added:

1. **Graph is the single source of truth.** In-memory objects
   (`PsycheState`, `MotivationState`) are always derived views, never
   authoritative.

2. **Async-first.** All I/O must be `await`-ed. Never call blocking code
   from an async context.

3. **Graceful degradation.** Every optional service (Qdrant, Neo4j, LLM)
   must have a code path that works without it. The system should never
   crash because an optional dependency is unavailable.

4. **Privacy by default.** No user data leaves the system without explicit
   user consent. Agent permissions must be scoped to the minimum required.

5. **Testability.** Every new module must be unit-testable with a `tmp_path`
   SQLite DB and a mock LLM. Never require external services in unit tests.

6. **Explainability.** Every retrieval result, agent action, and memory
   write must be traceable to its origin (provenance) and its scoring
   rationale (explanation strings from `RetrievalScoreBreakdown`).

---

## Quick reference: new modules added in v0.4.0

| Module | Purpose |
|---|---|
| `core/memory/provenance.py` | Provenance tracking (origin, author, edit history) |
| `core/memory/trust.py` | User memory control (edit, delete, export, inspect) |
| `core/observability/metrics.py` | In-process metrics (counters, latencies, gauges) |

Tests added:

| Test file | Coverage |
|---|---|
| `tests/test_memory_provenance.py` | 11 tests for `ProvenanceStore` |
| `tests/test_memory_trust.py` | 13 tests for `MemoryTrustService` |
| `tests/test_observability_metrics.py` | 18 tests for `MetricsCollector` |

---

## Quick reference: architecture consolidation shipped

| Artefact | Purpose |
|---|---|
| `ARCHITECTURE_PRINCIPLES.md` | Eight architectural invariants; allowed/prohibited dependency directions; online/offline split contract |
| `core/contracts.py` | `Protocol` interfaces: `EventAppender`, `ConsolidationRunner`, `BeliefRevisor`, `RetrievalProvider`, `PolicyEnforcer` |
| `interfaces/processor_factory.py` | Now injects `ProvenanceStore`, `MetricsCollector`, and `EventStore` into `MessageProcessor` |
| `core/pipeline/processor.py` | Accepts `provenance_store`, `metrics`, `event_store`; instruments `process_message` with counter + latency timer; wires `event_store` into `ObserveStage` |
