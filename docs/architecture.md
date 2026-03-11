# Architecture — SELF-OS

## Overview

SELF-OS is organised as a layered architecture. Each layer has a clearly defined
responsibility and communicates with adjacent layers through well-defined interfaces.
The layers are ordered from foundational to user-facing:

```
┌──────────────────────────────────────────────────────┐
│                    Interface Layer                   │
├──────────────────────────────────────────────────────┤
│                     Agent Core                       │
├──────────────────────────────────────────────────────┤
│                  Motivation Core                     │
├──────────────────────────────────────────────────────┤
│     Identity Core     │     Emotional Core           │
├──────────────────────────────────────────────────────┤
│                    Memory Core                       │
├──────────────────────────────────────────────────────┤
│         Bootstrapping / Identity Acquisition         │
└──────────────────────────────────────────────────────┘
```

---

## Layer Definitions

### Bootstrapping / Identity Acquisition Layer

**Responsibility**: Acquire an initial structured identity model for a new user through
onboarding, guided interviewing, and inference. This layer transforms raw input (answers
to onboarding questions, imported notes, biographical summaries) into populated graph
nodes (values, beliefs, needs, domain profiles) and an initial IdentityProfile.

**Current status**: Complete. `IdentityProfileBuilder` builds a populated `IdentityProfile`
from graph nodes. `OnboardingPlanner` generates gap-driven interview questions.
See [docs/onboarding-and-identity-bootstrap.md](onboarding-and-identity-bootstrap.md).

**Key modules**:
- `core/identity/schema.py` — IdentityProfile, DomainProfile, ProfileGap, Role, Skill
- `core/identity/builder.py` — IdentityProfileBuilder (graph → IdentityProfile)
- `core/onboarding/schema.py` — OnboardingQuestion, OnboardingAnswer, GapResolution
- `core/onboarding/planner.py` — OnboardingPlanner (gap-driven question generation)
- `core/graph/` — storage and retrieval of all identity nodes
- `core/parts/` — IFS parts memory (subpersonalities, values, needs)
- `core/psyche/` — PsycheState builder

---

### Memory Core

**Responsibility**: Store, consolidate, retrieve, and forget information across time.
Memory is represented as a directed, typed knowledge graph. Every entity (person, event,
insight, belief, goal, etc.) is a node; relationships are typed edges. The graph is
persisted in SQLite and optionally Neo4j. Vector embeddings support semantic search.

**Current status**: Complete. See `core/graph/`, `core/memory/`, `core/rag/`,
`core/search/`.

**Key modules**:
- `core/graph/storage.py` — SQLite-backed graph persistence
- `core/graph/api.py` — high-level graph query interface
- `core/memory/` — memory lifecycle (consolidation, abstraction, forgetting)
- `core/rag/` — retrieval-augmented generation
- `core/search/` — hybrid vector + keyword search
- `core/journal/` — raw message archival (event sourcing)

**Canonical node types**: PERSON, NOTE, EVENT, INSIGHT, BELIEF, THOUGHT, NEED, VALUE,
TASK, PROJECT, EMOTION, PART, SOMA.

---

### Emotional Core

**Responsibility**: Track the user's emotional state over time using the VAD model
(valence, arousal, dominance). Detect cognitive distortions. Extract emotion labels from
messages. Produce mood snapshots and trend summaries.

**Current status**: Complete. See `core/mood/`, `core/analytics/cognitive_detector.py`.

**Key modules**:
- `core/mood/tracker.py` — VAD tracking, snapshots, trend computation
- `core/analytics/cognitive_detector.py` — cognitive distortion detection
- `core/therapy/` — intervention selection based on emotional state

**Primary outputs**: VAD coordinates, emotion labels, distortion tags, mood trend.

---

### Identity Core

**Responsibility**: Maintain a structured, evolving model of the user's identity:
their values, beliefs, needs, parts (IFS), goals, and behavioral patterns. The identity
model is derived from the memory graph and updated continuously through inference and
onboarding.

**Current status**: Complete. PsycheState, IFS parts, IdentityProfile, and DomainProfile
are all implemented.

**Key modules**:
- `core/psyche/state.py` — PsycheState (unified snapshot)
- `core/parts/memory.py` — IFS parts (subpersonality) memory
- `core/goals/engine.py` — goal tracking and decomposition
- `core/neuro/engine.py` — NeuroCore (Hebbian activation model)
- `core/prediction/state_model.py` — PsycheState prediction DTOs
- `core/identity/` — IdentityProfile + IdentityProfileBuilder

**Primary outputs**: PsycheState, active goals, dominant parts, dominant needs.

---

### Motivation Core

**Responsibility**: Synthesise the current identity state, active goals, unresolved
needs, emotional state, and value tensions into a `MotivationState` — a structured
representation of what the user is motivated to do right now. The motivation core
drives proactive agent behaviour.

**Current status**: Complete. MotivationStateBuilder, MotivationScorer, and
MotivationStateStore are all implemented. See `core/motivation/`.

**Key modules**:
- `core/motivation/schema.py` — MotivationState dataclass + MotivationStateStore
- `core/motivation/builder.py` — MotivationStateBuilder
- `core/motivation/scoring.py` — MotivationScorer (rule-based, stateless)

**Primary outputs**: MotivationState (active goals, priority signals, recommended next
actions, action readiness).

---

### Agent Core

**Responsibility**: Execute actions on behalf of the user, informed by the motivation
state and the current memory/identity context. Agent actions are first-class objects,
persisted with their triggering context, motivation references, and outcomes. The agent
core uses the OODA pipeline (Observe → Orient → Decide → Act) and a tool registry.

**Current status**: OODA pipeline is complete. AgentAction schema and persistence are
complete. See `core/pipeline/`, `core/tools/`, `core/agent/`.

**Key modules**:
- `core/pipeline/` — OODA pipeline stages
- `core/tools/` — tool registry and built-in tools
- `core/agent/schema.py` — AgentAction dataclass
- `agents/ifs/` — IFS inner council agents

**Primary outputs**: AgentAction records, tool call results, response text.

---

### Interface Layer

**Responsibility**: Expose the agent to users via communication channels (Telegram,
future REST/WebSocket API). Handles message ingestion, user authentication, session
routing, and response delivery.

**Current status**: Telegram interface is implemented. See `interfaces/`.

**Key modules**:
- `interfaces/` — Telegram bot and future API adapters
- `main.py` — entry point

---

## Data Flows

### Ingest Flow

```
User message
  → Interface Layer (receive, parse)
  → Pipeline: ObserveStage (extract emotion, classify message)
  → Pipeline: OrientStage (build context from graph + psyche state)
  → Pipeline: DecideStage (select tools, build prompt)
  → Pipeline: ActStage (call LLM, execute tools, store results)
  → Graph write (new nodes, updated relationships)
  → Journal write (raw event)
  → Memory consolidation (background)
```

### State Flow

```
Graph (raw nodes)
  → MoodTracker (VAD extraction)        → EmotionState
  → CognitiveDetector (patterns)        → distortion tags
  → PartsMemory (IFS activation)        → active parts
  → GoalEngine (goal lookup)            → active goals
  → PsycheStateBuilder                  → PsycheState
  → MotivationStateBuilder              → MotivationState
  → PredictiveEngine (EWMA)             → PsycheStateForecast
```

### Retrieval Flow

```
Query (user message / planning context / reflection context)
  → ContextBuilder (session memory + persistent memory)
  → RAGRetriever (hybrid vector + keyword search)
  → RetrievalScorer (7-dimensional: semantic, goal, identity, emotional, recency, confidence, relationship)
  → RetrievalRanker (filter by confidence, sort, cap by limit)
  → Prompt injection
```

See [docs/retrieval-strategy.md](retrieval-strategy.md) and
[docs/retrieval-architecture.md](retrieval-architecture.md) for full retrieval design.

### Proactive Agent Flow

```
Scheduler (background)
  → PsycheStateBuilder → PsycheState
  → MotivationStateBuilder → MotivationState
  → AgentCore.decide_action(motivation_state)
  → Tool execution (if warranted)
  → AgentAction record written
  → Notification to user (if warranted)
```

### Reflection Flow

```
Periodic trigger (scheduler)
  → Memory retrieval (recent events + long-term patterns)
  → PredictiveEngine (trend / forecast)
  → InnerCouncil (IFS debate — CriticAgent, FirefighterAgent, ExileAgent, SelfAgent)
  → Insight generation
  → Graph write (new INSIGHT nodes)
  → Optional user notification
```

---

## Architectural Rules and Boundaries

1. **Layers communicate downward, not upward.** The Agent Core depends on Motivation
   Core and Identity Core. Identity Core depends on Memory Core. Memory Core is
   self-contained. No lower layer imports from a higher layer.

2. **PsycheState is the universal state contract.** All agent logic receives a
   `PsycheState` rather than querying subsystems directly. This decouples the agent
   from the specifics of storage and computation.

3. **Additive changes only.** New modules extend the system without replacing working
   implementations. Existing subsystems (graph, mood, parts, goals, prediction) are not
   refactored unless necessary to satisfy a hard architectural constraint.

4. **Graph is the source of truth.** All persistent knowledge lives in the graph. In-
   memory structures (PsycheState, MotivationState) are derived views, not sources of
   truth.

5. **Tool results are written back to the graph.** Every tool invocation that produces
   knowledge (search results, task creation, insight generation) creates graph nodes.
   This ensures the agent's actions accumulate in long-term memory.

6. **AgentAction records are persisted.** The system maintains an auditable history of
   every action taken by the agent, including what triggered it, what it did, and what
   the outcome was.

---

## Module Reference

| Module | Layer | Purpose |
|---|---|---|
| `core/graph/` | Memory Core | Knowledge graph storage and retrieval |
| `core/memory/` | Memory Core | Memory lifecycle (consolidation, forgetting) |
| `core/journal/` | Memory Core | Raw event sourcing |
| `core/rag/` | Memory Core | Retrieval-augmented generation |
| `core/search/` | Memory Core | Hybrid vector + keyword search |
| `core/mood/` | Emotional Core | VAD mood tracking |
| `core/analytics/` | Emotional Core | Cognitive distortion detection, graph analytics |
| `core/therapy/` | Emotional Core | Intervention selection (CBT/ACT/IFS) |
| `core/identity/` | Identity Core | IdentityProfile + IdentityProfileBuilder |
| `core/onboarding/` | Bootstrapping Layer | OnboardingPlanner + gap-driven interviews |
| `core/retrieval/` | Memory Core | Identity-aware retrieval scoring and ranking |
| `core/psyche/` | Identity Core | PsycheState (unified snapshot) |
| `core/parts/` | Identity Core | IFS parts memory |
| `core/goals/` | Identity Core | Goal engine |
| `core/neuro/` | Identity Core | NeuroCore (Hebbian activation) |
| `core/prediction/` | Identity Core | PsycheState prediction (EWMA) |
| `core/motivation/` | Motivation Core | MotivationState schema, builder, and store |
| `core/pipeline/` | Agent Core | OODA pipeline stages |
| `core/tools/` | Agent Core | Tool registry and built-in tools |
| `core/agent/` | Agent Core | AgentAction schema + persistence |
| `agents/ifs/` | Agent Core | IFS InnerCouncil agents |
| `core/context/` | Agent Core | Context builder |
| `core/llm/` | Agent Core | LLM client abstraction |
| `core/scheduler/` | Agent Core | Background job scheduling |
| `interfaces/` | Interface Layer | Telegram bot and API adapters |

See also: [docs/domain-model.md](domain-model.md) | [docs/retrieval-strategy.md](retrieval-strategy.md) | [docs/NEURO_ARCH_MANIFEST.md](NEURO_ARCH_MANIFEST.md) | [docs/PARALLEL_PLAN.md](PARALLEL_PLAN.md)
