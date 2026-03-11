# SELF-OS Architecture Stabilization Backlog

> **Purpose**: Concrete, ordered backlog of architectural tasks to stabilize SELF-OS.
> These tasks enforce clear ring boundaries, online/offline separation, revisable beliefs,
> policy-aware retrieval, and agent contracts — without cutting the product vision.
>
> **Neuro-inspired principle**: simple rules → complex emergent behavior.
> Each task below is small and self-contained, yet together they produce a robust, layered system.

---

## Architectural Target

```
Event Kernel  →  Memory Formation  →  Self Model  →  Goal/Motivation  →  Scoped Retrieval  →  Agent Views
(Ring 0)          (Ring 1 offline)    (Ring 1)        (Ring 1)            (Ring 2)             (Ring 3)
```

Online path (real-time): Event Kernel → fast ACK → deferred to offline
Offline path (background): Event Kernel → Memory Formation → Self Model update

---

## Priority Legend

| Priority | Meaning |
|---|---|
| P0 | Kernel-level — breaks everything downstream if not done |
| P1 | Foundational — needed before phase 5 features |
| P2 | Important — improves correctness and testability |
| P3 | Strategic — needed for marketplace/external API |

---

## RING 0 — Event Kernel Hardening

### [ARCH-01] Formalize the Event Type Registry *(P0)*

**Problem**: `core/pipeline/events.py` has a generic `Event(name, payload)` but no typed registry.
Any module can publish any event name — no compile-time guarantees, hard to audit.

**Tasks**:
- Add `core/kernel/event_types.py` with `KERNEL_EVENTS: dict[str, type]` registry (see implementation).
- Every published event must have a type-checked payload dataclass.
- Replace ad-hoc string event names with `KernelEventType` enum.
- Update `EventBus.publish()` to validate payload type against registry.

**Acceptance criteria**:
- `EventBus.publish("unknown_event_type", {})` raises `ValueError`.
- All 12 core event types are registered and tested.

**Effort**: 2–3 h

---

### [ARCH-02] Separate Online Capture from Offline Consolidation *(P0)*

**Problem**: `MessageProcessor` does both inline extraction and background consolidation,
but the boundary is fuzzy. The online path should be fast and dumb; the offline path should be
thorough and expensive.

**Tasks**:
- Define `OnlineCaptureResult` (fast path output) in `core/kernel/contracts.py`.
- Define `OfflineConsolidationJob` (what offline workers receive).
- `stage_observe.py` produces `OnlineCaptureResult` only (no LLM).
- `stage_orient.py` schedules an `OfflineConsolidationJob` instead of running inline LLM extraction.
- Background worker consumes `OfflineConsolidationJob` and updates the graph.
- Add latency budget constants: `ONLINE_BUDGET_MS = 200`, `OFFLINE_BUDGET_S = 30`.

**Acceptance criteria**:
- Online path is measurable in < 200 ms p95 without LLM.
- Background job picks up and runs consolidation within configured delay.
- Tests cover both paths independently.

**Effort**: 1–2 days

---

### [ARCH-03] Make Event Log Immutable *(P0)*

**Problem**: `JournalStorage` stores raw messages but there is no immutability guarantee.
Events can be re-written; this undermines provenance.

**Tasks**:
- Add `immutable=True` flag to journal records (no UPDATE allowed, only INSERT + soft-delete).
- Add `event_log` table separate from `journal` for system-generated events (distinct from user messages).
- Enforce append-only semantics in `GraphStorage` for `event_log`.
- Add `provenance_chain: list[str]` to derived objects (memory, belief) pointing back to source event IDs.

**Acceptance criteria**:
- Any attempt to mutate a committed event raises `ImmutableEventError`.
- Derived objects always carry `provenance_chain`.

**Effort**: 4–6 h

---

## RING 1 — Memory Formation & Beliefs

### [ARCH-04] Implement Revisable Belief Model *(P0)*

**Problem**: `BELIEF` nodes in the graph have no structured confidence, evidence tracking,
or revisability protocol. A belief can be contradicted but there is no formal mechanism
to detect or resolve contradictions.

**Tasks**:
- Implement `core/beliefs/model.py` with `RevisableBelief` dataclass (see implementation):
  - `confidence: float` (0–1)
  - `evidence_event_ids: list[str]`
  - `contradictory_event_ids: list[str]`
  - `user_confirmed: bool | None`
  - `last_revised_at: str`
  - `revision_count: int`
- Implement `core/beliefs/store.py` with CRUD + revision history.
- Migrate `BELIEF` node creation to go through `BeliefStore` (backward compatible via metadata).
- Add `revise_belief(belief_id, new_confidence, evidence_ids)` method.
- Expose beliefs with confidence < 0.4 as "uncertain" in context builder.

**Acceptance criteria**:
- A belief created with one evidence event can be revised with contradictory evidence.
- `user_confirmed=True` overrides confidence-based filtering.
- Low-confidence beliefs are not presented as facts in agent context.

**Effort**: 1 day

---

### [ARCH-05] Clarify Memory Lifecycle Phases *(P1)*

**Problem**: `MemoryConsolidator` has `consolidate()`, `abstract()`, and `forget()` but
the phases aren't formally named or versioned, making it hard to track which phase a node is in.

**Tasks**:
- Add `MemoryPhase` enum: `RAW → CONSOLIDATED → ABSTRACTED → ARCHIVED → DELETED`.
- Store current phase in node metadata.
- `consolidator.consolidate()` transitions `RAW → CONSOLIDATED`.
- `consolidator.abstract()` transitions `CONSOLIDATED → ABSTRACTED`.
- `consolidator.forget()` transitions any phase to `ARCHIVED` (soft-delete, then `DELETED` after grace period).
- Add `reinforce(node_id)` that resets decay and pulls `ARCHIVED → CONSOLIDATED`.
- Add `MemoryLifecycleAudit` log: record every phase transition.

**Acceptance criteria**:
- Every node has a readable `memory_phase` in metadata.
- Phase transitions are logged with timestamp and reason.
- `reinforce()` rescues a decayed memory correctly.

**Effort**: 4–6 h

---

### [ARCH-06] Implement Provenance Chain on All Derived Nodes *(P1)*

**Problem**: When an LLM extraction creates a BELIEF or VALUE node, there is no link
back to the raw events that caused it. Users cannot understand why the system believes something.

**Tasks**:
- Add `source_event_ids: list[str]` to `Node.metadata` schema (reserved key).
- Update `stage_orient.py` / extraction pipeline to populate `source_event_ids`.
- Update `MemoryConsolidator` to carry forward provenance when merging nodes.
- Add `/explain <node_id>` tool that returns provenance chain in human-readable form.
- Surface provenance in agent explanation strings.

**Acceptance criteria**:
- Every BELIEF, VALUE, INSIGHT, and THOUGHT node has non-empty `source_event_ids`.
- `/explain` returns a chain: `belief → event_ids → raw journal messages`.

**Effort**: 1 day

---

## RING 1 → RING 2 — Scoped Retrieval & Policy

### [ARCH-07] Introduce a Policy Engine *(P0)*

**Problem**: There is no centralized access-control layer. Any module can read any memory.
This is a fundamental barrier to marketplace (agent SDK) and privacy compliance.

**Tasks**:
- Implement `core/kernel/policy.py` with `PolicyEngine` (see implementation):
  - `PolicyScope` enum: `PRIVATE | HEALTH | RELATIONSHIP | PROFESSIONAL | PUBLIC`
  - `AgentPermission` dataclass: `agent_id`, `allowed_scopes`, `max_confidence_visible`, `can_write`
  - `PolicyEngine.check(agent_id, node) → bool`
  - `PolicyEngine.filter(agent_id, nodes) → list[Node]`
- Add `privacy_scope` to every `Node.metadata` (default `PRIVATE`).
- Route all retrieval through `PolicyEngine.filter()`.
- Log every filtered-out access attempt for audit.

**Acceptance criteria**:
- Agent with `PROFESSIONAL` scope cannot retrieve `HEALTH` nodes.
- Policy violations are logged.
- All retrieval tests pass policy filter.

**Effort**: 1 day

---

### [ARCH-08] Add Scope Parameter to Retrieval Context *(P1)*

**Problem**: `RetrievalQueryContext` has no `agent_scope` field.
Any caller can retrieve any memory regardless of who is asking.

**Tasks**:
- Add `agent_id: str` and `agent_scope: PolicyScope` to `RetrievalQueryContext`.
- `RetrievalRanker` passes candidates through `PolicyEngine.filter()` before ranking.
- Update all callers to specify `agent_id`.
- Add tests: confirm that out-of-scope candidates are excluded from results.

**Acceptance criteria**:
- `retrieval_ranker.rank()` with `agent_scope=PROFESSIONAL` never returns `HEALTH` nodes.
- Existing tests still pass (add `agent_id="system"` with full scope to existing fixtures).

**Effort**: 3–4 h

---

### [ARCH-09] Implement Agent Scoped Memory Views *(P0)*

**Problem**: Agents currently access graph storage directly.
They should only receive a pre-filtered, scoped memory view — not the whole brain.

**Tasks**:
- Implement `core/kernel/contracts.py` with `AgentMemoryView` (see implementation):
  - `nodes: list[Node]` — only what the agent is allowed to see.
  - `goals: list[str]` — active goals (public summary only).
  - `emotional_tone: str` — summary level, not raw VAD.
  - `retrieval_context: str` — why these memories were included.
  - `scope: PolicyScope` — the scope under which the view was built.
- Add `MemoryViewBuilder` that uses `PolicyEngine` + `RetrievalRanker` to produce a view.
- Replace direct graph API calls in `stage_orient.py` and agents with `MemoryViewBuilder`.

**Acceptance criteria**:
- Agent receives `AgentMemoryView`, not raw `GraphStorage`.
- Tests confirm the view respects policy scope.

**Effort**: 1 day

---

## RING 2 — Cognitive Services Isolation

### [ARCH-10] Extract Self-Model Update into Its Own Service *(P1)*

**Problem**: Identity profile updates happen inline in `stage_orient.py` and
`IdentityProfileBuilder`. This tightly couples the online path to expensive identity computation.

**Tasks**:
- Create `core/identity/update_service.py` with `SelfModelUpdateService`.
- It consumes `OfflineConsolidationJob` events (see ARCH-02).
- It rebuilds `IdentityProfile` from updated graph state.
- It detects value conflicts and creates `CONFLICTS_WITH` edges.
- Online path publishes an event; offline service processes it asynchronously.

**Acceptance criteria**:
- Online message handling does not call `IdentityProfileBuilder` directly.
- `SelfModelUpdateService` can be run in isolation with a mock graph.

**Effort**: 4–6 h

---

### [ARCH-11] Formalize Agent Contracts *(P0)*

**Problem**: There are no formal contracts specifying what each agent type is allowed to
read, write, or decide. This makes it impossible to enforce boundaries or audit behavior.

**Tasks**:
- Define `AgentContract` in `core/kernel/contracts.py` (see implementation):
  - `agent_id`, `agent_role`, `allowed_read_scopes`, `allowed_write_types`, `max_nodes_per_request`, `can_proactively_act`
- Define contracts for built-in agents: `CompanionAgent`, `PlannerAgent`, `ReflectionAgent`, `AnalyticsAgent`.
- Add `AgentContractRegistry` that maps agent IDs to contracts.
- Enforce contracts in `AgentOrchestrator`.

**Acceptance criteria**:
- `AnalyticsAgent` cannot create BELIEF or VALUE nodes (write-restricted).
- `CompanionAgent` cannot read `PROFESSIONAL` nodes (scope-restricted) unless user consents.
- Violation raises `AgentContractViolation`.

**Effort**: 1 day

---

## RING 3 — External API / Marketplace Boundary

### [ARCH-12] Design the Marketplace API Boundary *(P2)*

**Problem**: External agents (marketplace) will need to access memory views.
The internal cognitive engine must not be exposed directly.

**Tasks**:
- Define `ExternalAgentRequest` and `ExternalAgentResponse` schemas in `core/kernel/contracts.py`.
- Create `interfaces/api/` directory with FastAPI skeleton.
- External agents receive `AgentMemoryView` only (ARCH-09).
- All external agent actions go through `PolicyEngine` (ARCH-07).
- Add rate limiting and audit logging stubs.
- Document the API contract: what external agents can/cannot do.

**Acceptance criteria**:
- External API endpoint returns `AgentMemoryView` not raw nodes.
- Requests without valid `AgentPermission` are rejected.
- API schema documented as OpenAPI spec.

**Effort**: 2–3 days

---

### [ARCH-13] Add Dependency Rule Enforcement *(P2)*

**Problem**: Nothing prevents `core/memory/` from importing `agents/` or `interfaces/`.
Violations are discovered late and hard to fix.

**Tasks**:
- Add `import-linter` or `ruff` rule to `pyproject.toml`:
  - `interfaces` → may only import from `core/` (application services layer)
  - `agents` → may only import from `core/retrieval/`, `core/kernel/`, `core/agent/`
  - `core/graph/` → no imports from `core/identity/`, `core/motivation/`, `core/analytics/`
  - `core/kernel/` → no imports from `core/graph/` (kernel must be thin)
- Add CI check that fails if dependency rules are violated.

**Acceptance criteria**:
- CI fails on any new violation.
- Existing violations are documented as `# noqa: ARCH` with a tracking ticket.

**Effort**: 3–4 h

---

## RING 1 — Tests & Contract Enforcement

### [ARCH-14] Add Memory Domain Contract Tests *(P1)*

**Problem**: Unit tests verify behavior inside modules, but no tests verify cross-module
contracts (what memory guarantees to its consumers).

**Tasks**:
- Create `tests/test_memory_contracts.py` (see implementation):
  - Conflicting memories handled without crash.
  - User correction overrides inference (user-confirmed belief wins).
  - Deleted memory not returned by retrieval.
  - Low-confidence belief (< 0.3) not surfaced as fact.
  - Agent without scope cannot access out-of-scope memory.
  - Retrieval result includes provenance reference.

**Acceptance criteria**:
- All 6 contract tests pass.
- Tests are deterministic (no LLM, no network).

**Effort**: 3–4 h

---

## Summary Table

| ID | Title | Priority | Ring | Effort |
|---|---|---|---|---|
| ARCH-01 | Formalize Event Type Registry | P0 | 0 | 3h |
| ARCH-02 | Separate Online/Offline Consolidation | P0 | 0 | 2d |
| ARCH-03 | Make Event Log Immutable | P0 | 0 | 5h |
| ARCH-04 | Revisable Belief Model | P0 | 1 | 1d |
| ARCH-05 | Memory Lifecycle Phases | P1 | 1 | 5h |
| ARCH-06 | Provenance Chain | P1 | 1 | 1d |
| ARCH-07 | Policy Engine | P0 | 1→2 | 1d |
| ARCH-08 | Scope in Retrieval Context | P1 | 2 | 4h |
| ARCH-09 | Agent Scoped Memory Views | P0 | 2 | 1d |
| ARCH-10 | Self-Model Update Service | P1 | 2 | 5h |
| ARCH-11 | Formal Agent Contracts | P0 | 2 | 1d |
| ARCH-12 | Marketplace API Boundary | P2 | 3 | 3d |
| ARCH-13 | Dependency Rule Enforcement | P2 | cross | 4h |
| ARCH-14 | Memory Domain Contract Tests | P1 | cross | 4h |

---

## Recommended Sprint Order

### Sprint 1 — Kernel Foundation (1 week)
1. ARCH-01: Event Type Registry
2. ARCH-03: Immutable Event Log
3. ARCH-04: Revisable Belief Model
4. ARCH-07: Policy Engine
5. ARCH-11: Agent Contracts

### Sprint 2 — Online/Offline Separation (1 week)
1. ARCH-02: Online/Offline Consolidation
2. ARCH-09: Agent Scoped Memory Views
3. ARCH-08: Scope in Retrieval Context
4. ARCH-14: Memory Domain Contract Tests

### Sprint 3 — Completeness (1 week)
1. ARCH-05: Memory Lifecycle Phases
2. ARCH-06: Provenance Chain
3. ARCH-10: Self-Model Update Service
4. ARCH-13: Dependency Rules

### Sprint 4 — External Boundary (1 week)
1. ARCH-12: Marketplace API Boundary

---

## Architectural Principles Reinforced by This Backlog

1. **Online path is dumb and fast** — capture only, no LLM, no graph writes during reply.
2. **Offline path is smart and thorough** — extraction, consolidation, belief revision, self-model updates.
3. **Beliefs are probabilistic, not facts** — every belief has confidence, evidence, and can be revised.
4. **Retrieval is policy-aware** — what an agent sees depends on its contract, not on technical access.
5. **Agents get views, not storage** — agents receive pre-filtered `AgentMemoryView`, never raw graph access.
6. **Everything is provenance-tracked** — every derived object traces back to raw events.
7. **Kernel is thin and stable** — `core/kernel/` has no external dependencies; everything depends on it.
