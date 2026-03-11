# Roadmap — SELF-OS

This roadmap defines the ordered sequence of development phases, from the current state
through to the protocol-ready public layer. Each phase builds on the previous one.
Phases are not strictly time-boxed — they represent logical milestones rather than
calendar deadlines.

> **Parallelization plan**: See [`docs/PARALLEL_PLAN.md`](PARALLEL_PLAN.md) for a
> five-worker wave schedule mapping all open tasks to parallel tracks without
> harmful overlap.
>
> **Architecture manifest**: See [`docs/NEURO_ARCH_MANIFEST.md`](NEURO_ARCH_MANIFEST.md)
> for the neurobiologically-inspired memory system design — online/offline split,
> four memory types, memory lifecycle, agent access policy, and forbidden patterns.

---

## Phase 1 — Documentation and Architecture Stabilisation ✅

**Goal**: Establish a coherent internal architecture and documentation set that
accurately reflects the current codebase and clearly defines the target direction.

**Deliverables**:
- [x] `docs/vision.md` — long-term vision and product/protocol distinction
- [x] `docs/architecture.md` — layered architecture with data flow diagrams
- [x] `docs/domain-model.md` — canonical domain object definitions
- [x] `docs/product-positioning.md` — what the product is, what it is not
- [x] `docs/roadmap.md` — this document
- [x] `docs/onboarding-and-identity-bootstrap.md` — onboarding design
- [x] `docs/retrieval-strategy.md` — retrieval design
- [x] `core/motivation/schema.py` — MotivationState dataclass (initial)
- [x] `core/motivation/builder.py` — MotivationStateBuilder (initial)
- [x] `core/agent/schema.py` — AgentAction dataclass (initial)
- [x] Updated `README.md`

**Current status**: Complete (this PR).

---

## Phase 2 — Identity Bootstrapping / Onboarding ✅

**Goal**: Implement a structured onboarding flow that acquires an initial identity model
from a new user. After onboarding, the system has a populated IdentityProfile with
values, goals, beliefs, and at least one DomainProfile.

**Deliverables**:
- [x] `DomainProfile` implementation — `core/identity/schema.py`
- [x] `ProfileGap` detection — `core/identity/builder.py` (`_detect_gaps`)
- [x] `IdentityProfile` aggregation — `core/identity/builder.py` (`IdentityProfileBuilder`)
- [x] `OnboardingPlanner` — `core/onboarding/planner.py` (gap-driven question generation)
- [x] `OnboardingQuestion` / `OnboardingAnswer` schema — `core/onboarding/schema.py`
- [ ] Onboarding pipeline stage — dedicated pipeline stage for guided first-session flow
- [ ] Confidence tracking — per-field confidence scores on identity nodes
- [ ] Graph population — onboarding answers create VALUE, BELIEF, NEED nodes in graph

**Current status**: Core identity model and onboarding planner complete. Pipeline
integration and graph write-back from onboarding answers remain.

**Dependencies**: Phase 1 (documentation), existing graph layer.

See [docs/onboarding-and-identity-bootstrap.md](onboarding-and-identity-bootstrap.md)
and [docs/onboarding-flow.md](onboarding-flow.md).

---

## Phase 3 — Motivation Core ✅

**Goal**: Implement a working MotivationState that synthesises current identity, goals,
emotional state, and needs into actionable priority signals. Use it to drive proactive
agent behaviour.

**Deliverables**:
- [x] `MotivationStateBuilder` — `core/motivation/builder.py` (full implementation)
- [x] `MotivationScorer` — `core/motivation/scoring.py` (rule-based, stateless)
- [x] Action readiness score — computed from goals, needs, emotional pressure
- [x] `RecommendedAction` generation — ordered priority actions from PsycheState
- [x] `AgentAction` schema + persistence — `core/agent/schema.py` + `core/agent/store.py`
- [x] `MotivationStateStore` — snapshot persistence in `core/motivation/schema.py`
- [ ] Value tension detection — conflicts between active goals and core values (v1)
- [ ] Need-to-goal linkage — suggest goals from unresolved needs

**Current status**: MotivationStateBuilder and AgentAction persistence complete.
Value tension detection is a planned v1 extension.

**Dependencies**: Phase 2 (populated identity model), existing PsycheState.

See [docs/motivation-engine.md](motivation-engine.md) and
[docs/proactive-loop.md](proactive-loop.md).

---

## Phase 4 — Identity-Aware Retrieval ✅

**Goal**: Replace purely similarity-based retrieval with identity-aware retrieval that
weights results by relevance to the user's current identity, goals, emotional state, and
values.

**Deliverables**:
- [x] `RetrievalScorer` — 7-dimensional weighted scoring (`core/retrieval/scoring.py`)
- [x] Identity relevance dimension — weights nodes linked to values and active needs
- [x] Emotional salience dimension — boosts emotionally resonant nodes
- [x] Confidence-weighted retrieval — down-weights low-confidence nodes
- [x] Goal relevance dimension — prioritises nodes linked to active goals
- [x] `RetrievalRanker` — filter, sort, cap pipeline (`core/retrieval/ranker.py`)
- [x] `RetrievalQueryContext` with `query_type` — context-aware retrieval modes
- [ ] Per-mode weight profiles — mode-specific weight presets (chat/planning/proactive)
- [ ] Updated RAG layer — integrate `RetrievalScorer` into the `core/rag/` pipeline

**Current status**: Scoring and ranking layer complete. Per-mode weight profiles and
full RAG integration remain for v1.

**Dependencies**: Phase 2 (identity model), Phase 3 (MotivationState).

See [docs/retrieval-strategy.md](retrieval-strategy.md) and
[docs/retrieval-architecture.md](retrieval-architecture.md).

---

## Phase 5 — Goal and Action Continuity

**Goal**: Ensure that goals and agent actions are tracked continuously across sessions,
with the system maintaining coherent awareness of progress, blockers, and completed work.

**Deliverables**:
- [ ] Goal progress tracking — update goal status based on completed tasks and agent
      observations
- [ ] Blocker detection — identify when a goal is blocked and surface it proactively
- [ ] Action history — queryable log of past AgentAction records
- [ ] Goal/task retrospective — periodic review of completed and abandoned goals
- [ ] Longitudinal goal summary — user-facing summary of goal progress over time

**Dependencies**: Phase 3 (AgentAction records), existing GoalEngine.

---

## Phase 6 — Product Surface Stabilisation

**Goal**: Stabilise the user-facing product: reliable message handling, polished
responses, clear onboarding experience, and stable Telegram interface. Prepare for
early external users.

**Deliverables**:
- [ ] Onboarding UX — guided first-session experience for new users
- [ ] Response quality pass — audit and improve LLM prompt quality across all pipeline
      stages
- [ ] Error handling and graceful degradation — ensure no user-facing crashes
- [ ] User settings — allow users to configure key preferences (notification frequency,
      onboarding depth, etc.)
- [ ] Admin dashboard — basic tooling for monitoring system health
- [ ] Load testing — validate the system under realistic usage conditions

**Dependencies**: Phases 1–5.

---

## Phase 7 — Native Workspace Layer

**Goal**: Build SELF-OS's own analogs to Notion, Obsidian, and task managers — natively,
inside the system. SELF-OS does not integrate with these third-party tools: it **replaces**
them. The proactive agent creates tasks, organises notes, and maintains knowledge graphs
on behalf of the user. There is no need for external PKM tools when the agent handles
everything intelligently from accumulated identity context.

**Strategic principle**: A user should never need to open Obsidian or Notion because
SELF-OS does everything they do — and does it better, because it understands *who the
user is* and acts accordingly. Task creation, note capture, project organisation, and
knowledge management all happen natively inside SELF-OS, driven by the agent rather than
by manual user effort.

**Deliverables**:
- [ ] Native note layer — zero-friction capture; AI automatically structures input into
      the knowledge graph (replaces Obsidian/Notion note-taking)
- [ ] Agent-driven task creation — the proactive agent generates, prioritises, and manages
      tasks from goal context; users do not manually enter tasks into a separate tool
      (replaces Todoist/Linear/Things)
- [ ] Native knowledge workspace — daily/weekly review summaries, emergent topic clusters,
      atomic note views — all auto-generated from the graph (replaces PKM tools)
- [ ] Calendar ingestion — read calendar events as context signals only (no write-back
      needed; the agent plans, not external calendars)
- [ ] Web search tool — allow agent to search the web and add results to memory
- [ ] Webhook ingestion — accept events from external systems via HTTP

**Dependencies**: Phase 6 (stable product surface), Phases 3–5 (proactive agent and
goal continuity).

---

## Phase 8 — Protocol-Ready Public Layer

**Goal**: Define and implement a stable, privacy-preserving API that allows trusted
external applications to query a user's identity and context with their consent.

**Deliverables**:
- [ ] Public REST/WebSocket API — authenticated, consent-gated endpoints for identity
      and memory queries
- [ ] Consent model — granular user control over what data each application can access
- [ ] Rate limiting and abuse prevention
- [ ] API documentation and developer guide
- [ ] SDK or client library (Python, TypeScript)
- [ ] Protocol specification — formal definition of the identity and memory query API

**Dependencies**: Phase 7 (stable native workspace layer).

---

## Phase 9 — Neuro-Inspired Memory Architecture

**Goal**: Implement the full neurobiologically-inspired memory system described in
[`docs/NEURO_ARCH_MANIFEST.md`](NEURO_ARCH_MANIFEST.md). The key architectural
shift is a hard online/offline boundary: the online path (OODA pipeline) captures
events and reads pre-built memory views; all deep consolidation, belief updating,
and salience recalculation happens in offline background jobs.

**Deliverables**:
- [ ] `SalienceEngine` — multi-component salience scoring (novelty, emotion,
      goal relevance, repetition, unresolved tension, social weight)
- [ ] `ConsolidationEngine` refactor — explicit online-capture vs offline-
      consolidation boundary; episodic compression; belief extraction
- [ ] `BeliefEngine` v1 — revisable beliefs with `confidence`, `decay_rate`,
      `timescale` (fast/medium/slow), contradiction detection
- [ ] `PolicyLayer` — agent access gating; `MemoryView` contract; tiered access
      (private / agent / external)
- [ ] `MemoryTrace` schema — unified `strength`, `confidence`, `state`,
      `emotion_intensity`, `contradiction_score` fields on all memory objects
- [ ] Offline pre-built `MemoryView` cache — online path reads cached views;
      no live graph queries during reply generation

**Dependencies**: Phase 4 (identity-aware retrieval), Phase 5 (goal continuity),
Phase 6 (stable product surface).

See [`docs/NEURO_ARCH_MANIFEST.md`](NEURO_ARCH_MANIFEST.md) for the full design
specification, and [`docs/PARALLEL_PLAN.md`](PARALLEL_PLAN.md) Wave 4 for the
worker assignments.

---

## Existing Stages (Pre-Roadmap)

For historical reference, the earlier development stages were:

- **Stage 1** ✅ Passive Knowledge Graph — basic graph ingestion, keyword search
- **Stage 2** ✅ Consolidating Memory — memory lifecycle, vector search, RAG
- **Stage 3** ✅ Society of Mind — IFS InnerCouncil, PsycheState, GoalEngine, tools
- **Stage 4** ✅ Personal Intelligence OS — PredictiveEngine, NeuroCore, IdentityProfile, OnboardingPlanner, MotivationState, identity-aware retrieval (Phases 1–4 of this roadmap)
- **Stage 5** 🔮 Therapeutic Self — TherapyPlanner, InterventionSelector, epistemic model

The roadmap above supersedes and extends the stage model. Phases 5–8 represent new
directions beyond the existing stage model.
