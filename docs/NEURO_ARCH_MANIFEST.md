# Architectural Manifest v1 — Neuro-Inspired Memory System

> **Status**: Living design document. Principles in Section 1 are fixed.
> Implementation details in Sections 4–8 are v1 baselines subject to revision.
>
> **See also**: [`docs/PARALLEL_PLAN.md`](PARALLEL_PLAN.md) for the implementation
> wave schedule; [`docs/architecture.md`](architecture.md) for the module reference.

---

## 1. Immutable Principles

These principles are architectural invariants. No pull request may violate them.

1. **Append-only raw experience.** Raw events are never mutated after creation.
   The raw log is the ground truth of what happened. All derived structures
   (episodes, beliefs) are computed views, not replacements.

2. **Derived memories are revisable.** Episodic traces, beliefs, and self-model
   hypotheses are NOT permanent truths. They carry confidence scores and can be
   weakened, contradicted, compressed, or archived. Only the raw event log is
   immutable.

3. **Importance emerges from local salience rules.** No central authority decides
   what is important. A small, transparent salience formula (`novelty + emotion +
   goal_relevance + repetition + unresolved_tension + social_weight`) produces
   global importance ranking from local signal components.

4. **Retrieval depends on current state and task.** The same memory object may be
   retrieved, suppressed, or invisible depending on the current emotional state,
   active goal, task type, and privacy scope. Retrieval is never a raw database
   read.

5. **Every recall can update the memory.** Retrieval is reconsolidating.
   Accessing a memory may strengthen, weaken, or recontextualise it. The system
   must not treat retrieval as read-only.

6. **Identity is probabilistic and multi-timescale.** The user's identity is a
   graph of beliefs with confidence scores and decay rates, NOT a static profile
   with boolean attributes. Fast beliefs (mood, current stress) update in minutes.
   Slow beliefs (values, character traits) update over weeks.

7. **Agents get views, not total access.** No agent receives a raw handle to the
   memory store. Every agent receives a scoped `MemoryView` shaped by task type,
   privacy policy, and current state. The PolicyLayer mediates all access.

8. **Offline consolidation does the heavy lifting.** Online processing is fast and
   shallow. Deep pattern extraction, belief updating, salience recalculation,
   compression, and summary generation happen in background jobs — never blocking
   a user-facing response.

9. **Forgetting and compression are features, not failures.** The system actively
   maintains memory health through decay (reducing access probability of unused
   traces), compression (merging similar episodes into patterns), and suppression
   (hiding context-irrelevant memories). Unbounded growth is a bug.

10. **Emotion changes routing, not just labels.** Emotion intensity affects
    salience scores, consolidation priority, retrieval gating, agent intervention
    selection, and belief confidence updates. An `emotion` field on a memory
    object is not metadata — it is a routing signal.

---

## 2. Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│                        Interface Layer                          │
│              (Telegram, REST API, Webhook, Calendar)            │
├─────────────────────────────────────────────────────────────────┤
│                         Agent Loops                             │
│        (Reflection · Planner · Emotional Reg · Social)          │
├────────────────────────┬────────────────────────────────────────┤
│      Policy Layer      │         Retrieval Engine               │
│  (access gating,       │   (scoped MemoryView per context)      │
│   privacy control)     │                                        │
├────────────────────────┴────────────────────────────────────────┤
│                       Belief Engine                             │
│         (revisable identity · fast / medium / slow beliefs)     │
├─────────────────────────────────────────────────────────────────┤
│                      Salience Engine                            │
│       (novelty · emotion · goal_relevance · repetition ·        │
│        unresolved_tension · social_weight)                      │
├───────────────────────────┬─────────────────────────────────────┤
│     Online Path           │         Offline Path                │
│  (EventKernel →           │   (ConsolidationEngine —            │
│   MemoryFormation →       │    background jobs only)            │
│   fast salience check →   │                                     │
│   candidate traces)       │                                     │
├───────────────────────────┴─────────────────────────────────────┤
│                       Raw Event Store                           │
│              (append-only, immutable, source of truth)          │
└─────────────────────────────────────────────────────────────────┘
```

### Layer Ownership Rules

| Layer | May read from | May write to | May NOT access |
|-------|--------------|-------------|----------------|
| Interface | Agent Loops, Policy | Raw Event Store (new events only) | Memory internals directly |
| Agent Loops | PolicyLayer (MemoryView) | Raw Event Store (actions) | Raw Event Store (past events) |
| Policy + Retrieval | Belief Engine, Salience, Memory | — | Raw event content beyond scoped view |
| Belief Engine | Memory (episodic + semantic) | Belief store | Raw Event Store |
| Salience Engine | Raw events, Memory traces | Salience scores only | Beliefs |
| Online Path | Raw Event Store | Candidate traces, salience queue | ConsolidationEngine |
| Offline Path | Everything below | Episodic, semantic, belief stores | Interface Layer |
| Raw Event Store | — | — | Never mutated |

---

## 3. Memory Types

SELF-OS uses four distinct memory types. They are not interchangeable.

### 3.1 Raw Event Log (Sensory Layer)
- **What it stores**: Every incoming signal — messages, voice transcripts, calendar
  events, tool results, telemetry, manual notes.
- **Properties**: Immutable, append-only, timestamped, source-tagged.
- **Analogous to**: Sensory stream / working memory buffer.
- **Managed by**: `EventKernel` (online path).

### 3.2 Episodic Memory
- **What it stores**: Concrete experienced episodes — "Monday's call felt anxious",
  "idea emerged during afternoon walk", "conflict with colleague on Friday".
- **Required fields**: `episode_id`, `timestamp_start`, `timestamp_end`,
  `participants[]`, `emotion_label`, `emotion_intensity`, `causal_links[]`,
  `salience_score`, `state` (active | latent | archived | disputed).
- **Properties**: Temporally anchored, contextualised, causally linked, revisable.
- **Analogous to**: Hippocampal episodic index.
- **Managed by**: `MemoryFormation` (online) + `ConsolidationEngine` (offline).

### 3.3 Semantic Memory (Belief Store)
- **What it stores**: Stable generalised knowledge about the user — "works better
  in the morning", "values autonomy over structure", "cycle of motivation every
  ~6 weeks".
- **Required fields**: `belief_id`, `hypothesis`, `confidence: float [0,1]`,
  `supporting_episodes[]`, `contradictory_episodes[]`, `last_confirmed_at`,
  `contradiction_score`, `decay_rate` (low | medium | high), `timescale`
  (fast | medium | slow).
- **Properties**: Abstract, probabilistic, contradiction-aware, multi-timescale.
- **Analogous to**: Cortical semantic abstraction / self-model.
- **Managed by**: `BeliefEngine` + `ConsolidationEngine`.

### 3.4 Procedural-Agent Memory
- **What it stores**: What works — effective intervention patterns, which agent
  behaviours produce good outcomes, which response strategies resonate.
- **Required fields**: `pattern_id`, `context_signature`, `action_taken`,
  `outcome_score`, `use_count`, `last_used_at`.
- **Properties**: Action-oriented, outcome-weighted, context-specific.
- **Analogous to**: Basal ganglia action policies / habits.
- **Managed by**: `AgentAction` store + offline policy-weight updates.

---

## 4. Memory Object Lifecycle

```
Raw Event
    │ (EventKernel: parse, tag, persist)
    ▼
Candidate Trace
    │ (SalienceEngine: score novelty + emotion + goal_relevance + ...)
    │
    ├─ [salience < threshold] ──► Salience Queue (for offline review)
    │
    └─ [salience ≥ threshold] ──► Active Episodic Trace
                                         │
                    ┌────────────────────┤
                    │ ONLINE (immediate) │ OFFLINE (background)
                    └────────────────────┤
                                         │
              ConsolidationEngine picks up:
                    │
                    ├─ merge similar episodes → Compressed Pattern
                    ├─ extract recurring pattern → Belief hypothesis
                    ├─ raise/lower confidence on existing Beliefs
                    ├─ update salience scores (decay unused traces)
                    └─ archive low-strength dormant traces

State transitions:
  active ──(no access > 30 days)──► latent
  latent ──(no access > 90 days)──► archived
  active/latent ──(contradiction detected)──► disputed
  disputed ──(resolved by user or new evidence)──► active | archived
```

### Memory Object Fields (Minimum Required)

```python
@dataclass
class MemoryTrace:
    trace_id: str
    memory_type: Literal["episodic", "semantic", "procedural"]
    content: str                       # human-readable summary
    embedding: list[float]             # semantic vector
    source_events: list[str]           # raw event IDs
    timestamp_created: datetime
    timestamp_last_accessed: datetime
    strength: float                    # 0.0–1.0, decays over time
    confidence: float                  # 0.0–1.0, updated by evidence
    salience_score: float              # current salience
    retrieval_count: int
    last_confirmed_by_user_at: datetime | None
    contradiction_score: float         # 0.0–1.0
    state: Literal["active", "latent", "archived", "disputed"]
    emotion_label: str | None
    emotion_intensity: float | None    # 0.0–1.0
    goal_links: list[str]              # goal IDs
    belief_links: list[str]            # belief IDs
    privacy_level: Literal["private", "agent", "external"]
```

---

## 5. Online Path — What It Can and Cannot Do

The online path runs synchronously inside the OODA pipeline. Its only job is to
capture and do the minimum necessary to generate a coherent reply.

### Online path MAY:
- Accept raw events and persist them to the Raw Event Store.
- Perform lightweight intent classification (router).
- Compute a fast salience estimate using only the current event and recent session
  context (no graph queries beyond the last N messages).
- Create candidate episodic traces with provisional salience scores.
- Read the most recent `MemoryView` prepared by the last offline consolidation.
- Call the `RetrievalEngine` for a scoped, pre-built `MemoryView`.
- Read the current `BeliefEngine` snapshot (pre-computed by offline jobs).
- Generate a reply using the above context.
- Emit completion events for the offline path to pick up.

### Online path MUST NOT:
- Run graph-wide analytics or pattern extraction.
- Update belief confidence scores or salience scores.
- Rebuild the semantic self-model.
- Compress or merge episodic traces.
- Query raw event history beyond the current session window.
- Access another user's memory.
- Perform any operation whose latency exceeds the acceptable reply deadline.

---

## 6. Offline Path — What It Must Do

The offline path runs as background jobs on a configurable schedule (e.g., every
15 minutes for salience refresh; nightly for deep consolidation).

### ConsolidationEngine responsibilities:
1. **Episodic compression**: Cluster similar active episodes; replace cluster with
   a compressed `EpisodicPattern` node. Preserve source event references.
2. **Belief extraction**: Detect recurring patterns across episodes; create or
   strengthen `Belief` hypotheses with confidence scores.
3. **Contradiction detection**: Compare new episodes against existing beliefs;
   raise `contradiction_score` when evidence conflicts.
4. **Salience decay**: Apply time-based decay to all active traces; move
   low-strength traces to `latent` state.
5. **Strength boosting**: Increase `strength` for traces accessed or confirmed
   recently.
6. **Belief timescale update**: Update fast beliefs (mood, stress) on every run;
   medium beliefs (habits, rhythms) weekly; slow beliefs (values, traits) monthly.
7. **Archive**: Move `latent` traces older than 90 days to `archived` state.
8. **MemoryView pre-build**: For each user, pre-compute the top-K retrieval
   candidates for the most common query types (chat, planning, reflection),
   so the online path reads a cached view rather than building it live.

### SalienceEngine responsibilities:
- Compute and update `salience_score` for every memory trace.
- Formula: `novelty + emotion_weight + goal_relevance + repetition_bonus +
  unresolved_tension + social_weight` (all components in [0, 1], configurable
  per-component weights).
- Novelty: how different the event is from the last N events (embedding distance).
- Emotion weight: `emotion_intensity * emotion_priority_factor[emotion_label]`.
- Goal relevance: fraction of the user's active goals the trace links to.
- Repetition bonus: log-scaled count of similar events in the past 30 days.
- Unresolved tension: binary flag — trace links to a goal or need with no
  resolution node.
- Social weight: whether the trace involves a person node marked as `significant`.

---

## 7. Agent Access Policy

All agent memory access goes through the `PolicyLayer`. No agent reads the
database directly.

### Access tiers

| Tier | Who gets it | What they see |
|------|-------------|--------------|
| `private` | User only, reflection agent | All memory, all beliefs, all raw events (scoped to their own user) |
| `agent` | Internal agents (planner, emotional, social) | Active + latent traces; beliefs with `confidence ≥ 0.4`; no raw event content |
| `external` | Marketplace / third-party agents | Only traces explicitly consented by user; `privacy_level == "external"` |

### MemoryView contract

The `PolicyLayer.get_view(agent_id, user_id, query_context)` method returns a
`MemoryView` object — never a raw cursor or list of raw events. A `MemoryView`:
- Is scoped to the requesting agent's tier.
- Is pre-filtered by the current `query_context` (task type, emotional state,
  active goals, time horizon).
- Contains at most `MAX_TRACES` results (configurable, default 50).
- Is read-only — agents cannot modify memory through the view.
- Is invalidated and rebuilt by the offline path every consolidation cycle.

### Retrieval context fields

Every retrieval request must supply:
```python
@dataclass
class RetrievalContext:
    query_text: str
    task_type: Literal["chat", "planning", "reflection", "proactive"]
    current_goal_ids: list[str]
    emotional_state: EmotionState | None
    time_horizon: Literal["immediate", "short_term", "long_term"]
    privacy_scope: Literal["private", "agent", "external"]
    agent_id: str
```

---

## 8. Anti-Patterns (Forbidden)

The following patterns are explicitly forbidden. If a PR introduces any of these,
it must be rejected at review.

| # | Anti-pattern | Why it is forbidden |
|---|-------------|---------------------|
| 1 | Agent reads `GraphStorage` directly without going through `PolicyLayer` | Bypasses access gating; leaks memory across users or tiers |
| 2 | Belief stored as a boolean attribute (e.g., `is_disciplined: bool`) | Erases uncertainty; prevents reconsolidation; locks identity |
| 3 | Memory object without a `confidence` and `state` field | Cannot be decayed, archived, or disputed |
| 4 | Consolidation logic inside the OODA pipeline (online path) | Blocks the user-facing response; violates online/offline boundary |
| 5 | Raw event log mutated after creation | Destroys the immutable audit trail |
| 6 | Single emotion label on a memory with no intensity score | Emotion cannot influence salience or routing without intensity |
| 7 | `MotivationState` or `PsycheState` stored as source of truth | These are derived views; the graph is the source of truth |
| 8 | Retrieval that ignores current emotional state and task type | Produces context-blind results; violates Principle 4 |
| 9 | Agent loop that queries ALL memories without a scope limit | No cap on result set; latency and privacy risk |
| 10 | Belief update during the online path | Violates Principle 8 (offline consolidation only) |

---

## 9. Implementation Wave Reference

Refer to [`docs/PARALLEL_PLAN.md`](PARALLEL_PLAN.md) for the full wave schedule.
The neuro-architecture items map to waves as follows:

| Manifest component | Wave | Worker | Notes |
|-------------------|------|--------|-------|
| `EventKernel` (raw event persistence) | Already implemented (`core/journal/`) | — | No new work needed |
| `RetrievalEngine` scoped views | Wave 1–2 (C) | C | Extends existing `core/retrieval/` — sole owner in both waves |
| `MemoryFormation` (candidate traces + online-capture boundary) | Wave 4 (C) | C | Implemented as `core/memory/online_capture.py` alongside `offline_consolidation.py` by the same worker; single-owner, no split |
| `SalienceEngine` v1 | Wave 4 (D) | D | New file `core/memory/salience.py`; no overlap with Wave 4-C's files |
| `ConsolidationEngine` (offline consolidation refactor) | Wave 4 (C) | C | `core/memory/offline_consolidation.py`; same worker as `MemoryFormation` |
| `BeliefEngine` v1 | Wave 4 (E) | E | New `core/identity/beliefs.py`; disjoint from Wave 4-C and 4-D |
| `PolicyLayer` (agent access gating) | Wave 5 (B) | B | Part of consent model; new `core/consent/` module |
| `MemoryTrace` schema | Wave 4 (C) | C | Added to `core/memory/` alongside `MemoryFormation`; one owner |
| Agent Loops (reflection, planner, emotional, social) | Ongoing across waves | All | Each loop is a separate file; no single-owner conflicts |
