# Task Parallelization Plan — SELF-OS Stabilization

> **Purpose**: Map every open stabilization task to one of five parallel worker
> tracks, grouped into sequential waves. Each wave can run with up to five workers
> without harmful overlap. Dependency boundaries are made explicit so no two
> parallel tasks modify the same module at the same time.

---

## Reading This Document

| Symbol | Meaning |
|--------|---------|
| ✅ | Already complete (Wave 0 — baseline) |
| 🔒 | Blocked until a named prerequisite finishes |
| ⚡ | Independently startable right now |
| 👤 | Suggested worker assignment (A–E) |

**Five workers** are labelled A, B, C, D, E throughout. Within a wave every worker
operates on a disjoint set of modules, so pull requests will not produce merge
conflicts or race on shared state.

---

## Wave 0 — Completed Baseline

All tasks below are already done and constitute the stable foundation on which
Wave 1 builds.

| Task | Phase | Module(s) |
|------|-------|-----------|
| Documentation & architecture (Phase 1) | 1 | `docs/` |
| Identity model + OnboardingPlanner core (Phase 2 core) | 2 | `core/identity/`, `core/onboarding/` |
| MotivationStateBuilder + AgentAction schema (Phase 3 core) | 3 | `core/motivation/`, `core/agent/` |
| RetrievalScorer + RetrievalRanker (Phase 4 core) | 4 | `core/retrieval/` |
| NeuroCore + IFS InnerCouncil (Stage 3–4) | — | `core/neuro/`, `agents/ifs/` |
| PredictiveEngine (Stage 4) | — | `core/prediction/` |
| OODA Pipeline (Stage 2–4) | — | `core/pipeline/` |

---

## Wave 1 — Five Parallel Tracks (Startable Now)

No track in this wave depends on any other track in this wave. All five can
start on the same day from the Wave 0 baseline.

### Worker A — Onboarding Pipeline Completion
**Phase 2 remaining items**

| Task | Module(s) owned |
|------|----------------|
| ⚡ Onboarding pipeline stage — guided first-session OBSERVE/DECIDE hook | `core/pipeline/stage_observe.py`, `core/onboarding/` |
| ⚡ Confidence tracking — per-field confidence scores on identity nodes | `core/identity/schema.py`, `core/identity/builder.py` |

**Shared-module risk**: Worker A touches `core/pipeline/stage_observe.py`. No
other Wave 1 worker writes to that file.

**Exit criteria**: `OnboardingStage` passes its tests; `IdentityProfile` fields
carry a `confidence: float` attribute.

---

### Worker B — Motivation Core Completion
**Phase 3 remaining items**

| Task | Module(s) owned |
|------|----------------|
| ⚡ Value tension detection — conflicts between active goals and core values | `core/motivation/builder.py`, `core/motivation/scoring.py` |
| ⚡ Need-to-goal linkage — suggest goals from unresolved needs | `core/goals/engine.py`, `core/motivation/builder.py` |

**Shared-module risk**: Worker B is the sole writer to `core/motivation/builder.py`
and `core/goals/engine.py` in this wave.

**Exit criteria**: `MotivationStateBuilder.build()` returns `tension_signals` and
`suggested_goals` fields; unit tests green.

---

### Worker C — Retrieval Layer Completion
**Phase 4 remaining items**

| Task | Module(s) owned |
|------|----------------|
| ⚡ Per-mode weight profiles — preset weight vectors for chat / planning / proactive modes | `core/retrieval/scoring.py`, `core/retrieval/models.py` |
| ⚡ RAG integration — wire `RetrievalScorer` into the `core/rag/` pipeline | `core/rag/`, `core/retrieval/` |

**Shared-module risk**: Worker C is the sole writer to `core/rag/` and
`core/retrieval/` in this wave.

**Exit criteria**: `RAGRetriever` accepts a `query_type` and delegates scoring to
`RetrievalScorer`; per-mode weight presets exist as named constants.

---

### Worker D — Goal Continuity Foundation
**Phase 5 first tranche**

| Task | Module(s) owned |
|------|----------------|
| ⚡ Goal progress tracking — update goal status from completed tasks and observations | `core/goals/engine.py` — only `update_progress()` method, disjoint from Worker B's `suggest_from_needs()` |
| ⚡ Action history — queryable log of past `AgentAction` records | `core/agent/store.py`, `core/agent/schema.py` |

**Shared-module risk**: Worker D writes to `core/goals/engine.py` but only adds
`update_progress()`. Worker B adds `suggest_from_needs()`. These are additive and
non-overlapping methods on the same class — coordinate via feature flags or
separate methods to avoid merge conflict.

> ⚠️ **Coordination note**: Workers B and D both touch `core/goals/engine.py`. Agree
> on method signatures before coding and merge Worker B's branch before Worker D's,
> or keep changes in strictly separate methods.

**Exit criteria**: `GoalEngine.update_progress(goal_id, delta)` exists;
`AgentActionStore.query(user_id, since=, action_type=)` returns filtered history.

---

### Worker E — Standalone Infrastructure Utilities
**Phase 6 / Phase 7 decoupled items (no Phase 5 dependency)**

| Task | Module(s) owned |
|------|----------------|
| ⚡ Error handling & graceful degradation — audit and harden all pipeline stages | `core/pipeline/`, `interfaces/` — audit only, no logic changes |
| ⚡ Web search tool — allow agent to search the web and add results to memory | `core/tools/` — new `web_search.py` tool |
| ⚡ Webhook ingestion — HTTP endpoint that accepts external events | `interfaces/` — new `webhook.py` adapter |

**Shared-module risk**: Worker E adds new files under `core/tools/` and `interfaces/`
and makes hardening-only edits to pipeline stages. No other worker touches these
modules in Wave 1.

**Exit criteria**: `WebSearchTool` runs a query and writes a SEARCH_RESULT node to
the graph; webhook adapter parses a JSON payload and emits it into the pipeline.

---

## Wave 2 — Five Parallel Tracks (After Wave 1 Stabilizes)

Each track has at most one Wave 1 prerequisite. All five can start in parallel
once their specific prerequisite is merged.

### Worker A — Onboarding Graph Write-back
**Prerequisite**: Wave 1-A merged. 🔒

| Task | Module(s) owned |
|------|----------------|
| 🔒 Graph population — onboarding answers create VALUE / BELIEF / NEED nodes | `core/onboarding/planner.py`, `core/graph/api.py` |

**Exit criteria**: `OnboardingPlanner.process_answer()` creates graph nodes for each
answered question; integration test verifies node presence after onboarding.

---

### Worker B — Goal Continuity Second Tranche
**Prerequisite**: Wave 1-D (action history) merged. 🔒

| Task | Module(s) owned |
|------|----------------|
| 🔒 Blocker detection — identify blocked goals and surface proactively | `core/goals/engine.py` — `detect_blockers()` method |
| 🔒 Goal/task retrospective — periodic review of completed/abandoned goals | `core/scheduler/`, `core/goals/` |

**Exit criteria**: `GoalEngine.detect_blockers()` returns a list of `BlockedGoal`
objects; retrospective job runs on a configurable cron schedule.

---

### Worker C — Response Quality Pass
**Prerequisite**: Wave 1-C (RAG integration) merged. 🔒

| Task | Module(s) owned |
|------|----------------|
| 🔒 Response quality audit — review and improve LLM prompts across all pipeline stages | `core/pipeline/stage_decide.py`, `core/context/` — prompt templates and MotivationState injection |

**Note**: Worker C explicitly does NOT touch `core/pipeline/stage_act.py` in this
wave. The summary rendering hook in `stage_act.py` is deferred to Wave 3 (Worker F
or re-assigned), ensuring no overlap with Worker D below.

**Exit criteria**: All `stage_decide.py` prompts reference the user's active
`MotivationState`; prompt templates are extracted to named constants. `stage_act.py`
is unchanged by this worker in this wave.

---

### Worker D — Longitudinal Goal Summary
**Prerequisite**: Wave 2-B (retrospective) merged. 🔒

| Task | Module(s) owned |
|------|----------------|
| 🔒 Longitudinal goal summary — build the summary data layer | `core/goals/` — new `summary.py` (`GoalSummaryBuilder`) only |

**Note**: `GoalSummaryBuilder` exposes a clean API but is NOT wired into
`stage_act.py` in this wave. The pipeline integration (which touches `stage_act.py`)
is a Wave 3 item assigned after Worker C's prompt audit lands, eliminating the
overlap entirely.

**Exit criteria**: `GoalSummaryBuilder.build(user_id, since=)` produces a
structured summary containing completed goals, active goals, and trend commentary;
unit tests pass without any pipeline modifications.

---

### Worker E — Calendar Ingestion
**Prerequisite**: Wave 1-E (webhook ingestion) merged. 🔒

| Task | Module(s) owned |
|------|----------------|
| 🔒 Calendar ingestion — read calendar events as context signals | `interfaces/` — new `calendar.py` adapter; `core/tools/` — `calendar_tool.py` |

**Exit criteria**: Calendar adapter reads events from an iCal URL and creates EVENT
nodes in the graph; these nodes appear in retrieval context.

---

## Wave 3 — Product Surface Stabilization

**Prerequisite**: Wave 2 fully merged. All five tracks are parallel.

| Worker | Task | Module(s) owned |
|--------|------|----------------|
| A | Onboarding UX — guided first-session experience for new users | `interfaces/`, `core/onboarding/` |
| B | User settings — configurable notification frequency, onboarding depth | `core/` — new `settings/` module; `interfaces/` |
| C | Admin dashboard — basic system health monitoring | `interfaces/` — new `admin/` adapter |
| D | Native note layer + `stage_act.py` summary rendering — wire `GoalSummaryBuilder` into ACT stage; zero-friction note capture | `core/tools/` — `note_tool.py`; `core/graph/`; `core/pipeline/stage_act.py` |
| E | Agent-driven task creation — proactive agent generates/prioritises tasks | `core/agent/`, `core/goals/`, `core/scheduler/` |

---

## Wave 4 — Native Workspace and Load Validation

**Prerequisite**: Wave 3 fully merged. All five tracks are parallel.

| Worker | Task | Module(s) owned |
|--------|------|----------------|
| A | Native knowledge workspace — daily/weekly summaries, topic clusters | `core/analytics/`, `core/scheduler/` |
| B | Load testing — validate system under realistic concurrent usage | `tests/` — new `load/` directory |
| C | Online/offline memory split — explicit `OnlineCapture` vs `OfflineConsolidation` boundary (see `docs/NEURO_ARCH_MANIFEST.md`) | `core/memory/`, `core/pipeline/` |
| D | SalienceEngine v1 — novelty + emotion + goal relevance scoring for memory traces | `core/memory/` — new `salience.py` |
| E | BeliefEngine v1 — revisable `Belief` objects with confidence + decay | `core/identity/` — new `beliefs.py` |

---

## Wave 5 — Protocol-Ready Public Layer

**Prerequisite**: Wave 4 fully merged. All five tracks are parallel.

| Worker | Task | Module(s) owned |
|--------|------|----------------|
| A | Public REST/WebSocket API — authenticated, consent-gated endpoints | `interfaces/` — new `api/` adapter |
| B | Consent model — granular user control over data access | `core/` — new `consent/` module |
| C | Rate limiting and abuse prevention | `interfaces/api/` — middleware |
| D | API documentation and developer guide | `docs/` |
| E | SDK / protocol specification | Repository root — new `sdk/` directory |

---

## Dependency Graph Summary

```
Wave 0 (baseline)
  │
  ├── Wave 1-A (Onboarding pipeline + confidence)
  │     └── Wave 2-A (Graph write-back)
  │           └── Wave 3-A (Onboarding UX)
  │
  ├── Wave 1-B (Value tension + need-to-goal)
  │
  ├── Wave 1-C (Per-mode weights + RAG)
  │     └── Wave 2-C (Response quality)
  │
  ├── Wave 1-D (Goal progress + action history)
  │     └── Wave 2-B (Blockers + retrospective)
  │           └── Wave 2-D (Longitudinal summary)
  │
  └── Wave 1-E (Error hardening + web search + webhooks)
        └── Wave 2-E (Calendar ingestion)
              │
  ┌───────────┘
  │
  Wave 3 (product surface — all Wave 2 merged)
  │
  Wave 4 (native workspace + neuro split)
  │
  Wave 5 (protocol layer)
```

---

## Overlap Risk Matrix

The table below lists every module pair that two workers might touch in the
same wave and the resolution strategy.

| Wave | Module | Workers | Resolution |
|------|--------|---------|-----------|
| 1 | `core/goals/engine.py` | B, D | Pre-agree on method names; merge B before D |
| 2 | `core/pipeline/stage_act.py` | C, D | **Eliminated**: Worker C is scoped to `stage_decide.py` + `core/context/` only; Worker D builds `GoalSummaryBuilder` in `core/goals/summary.py` only. Neither touches `stage_act.py` in Wave 2. Pipeline wiring is deferred to Wave 3-D. |
| 3 | `interfaces/` | A, C | A owns onboarding routes; C owns `/admin` prefix — no file conflicts |
| 4 | `core/memory/` | C, D | C creates `online_capture.py` + `offline_consolidation.py`; D creates `salience.py` — additive, disjoint files |

No other overlap was identified. All other wave-internal assignments operate on
disjoint modules.

---

## Parallelization Verdict

> **Yes — approximately five workers can execute the stabilization backlog in
> parallel without harmful overlap, provided they follow the wave structure
> defined above.**

Key findings:

1. **Wave 1 is fully safe to run in parallel right now.** All five tracks touch
   disjoint modules, with one low-risk coordination note (Workers B and D on
   `core/goals/engine.py` — resolved by method-level separation).

2. **Phases 5–8 tasks do NOT all depend on Phases 2–4 being complete.** Several
   Phase 6 and Phase 7 items (`error hardening`, `web search tool`,
   `webhook ingestion`, `calendar ingestion`) are standalone utilities that can
   begin in Wave 1 or Wave 2 without waiting for identity or motivation work to
   land.

3. **The neuro-inspired architecture items** (online/offline split, SalienceEngine,
   BeliefEngine) are placed in Wave 4 because they require stable Memory Core and
   Product Surface layers to integrate cleanly. See
   [`docs/NEURO_ARCH_MANIFEST.md`](NEURO_ARCH_MANIFEST.md) for the full design.

4. **Total estimated waves to completion**: 5 waves. With five workers each wave
   takes roughly the same calendar time as a single-worker sprint, compressing the
   total timeline by approximately 5×.
