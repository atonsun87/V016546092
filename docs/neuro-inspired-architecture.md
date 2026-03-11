# Neuro-Inspired Architecture — Engineering Reference

## Overview

SELF-OS is designed around a **neurobiologically inspired** memory architecture that separates two
fundamentally different processing regimes: **online capture** and **offline consolidation**.
This document explains what that means in concrete engineering terms, why this split matters,
and how the current modules implement it.

---

## 1. The Core Split: Online Path vs Offline Path

### Why biology got there first

In biological memory the same dual-path problem is solved by a clever hardware division:

| Path | Biological | Function | Speed |
|------|-----------|----------|-------|
| Online | Hippocampus | Rapid capture of experiences; immediate response generation | Milliseconds |
| Offline | Neocortex | Slow consolidation; semantic integration; belief updating | Hours (sleep) |

The hippocampus does not try to understand what it encodes — it just encodes fast and faithfully,
with minimal processing.  The neocortex does the deep work later, asynchronously, when the system
is not busy with real-time demands.

This is **not a limitation of biology** — it is an elegant architectural choice.  The online path
can be fast because it is thin.  The offline path can be thorough because it runs without latency
pressure.

### The engineering equivalent

In SELF-OS the same principle is implemented as two clearly bounded subsystems:

```
┌──────────────────────────────────────────────────────────────────┐
│                         ONLINE PATH                              │
│                   (real-time, latency-critical)                  │
│                                                                  │
│  User message → ObserveStage → fast reply → EventRecord written  │
│                                                                  │
│  Rules:                                                          │
│  • Sanitise and classify input (cheap, no LLM required)          │
│  • Capture raw signals (valence estimate, keywords, intent)      │
│  • Generate reply using already-accumulated graph context         │
│  • Write one EventRecord (append-only, never mutate beliefs)     │
│  • Return.  Never block on deep processing.                      │
└──────────────────────────────────────────────────────────────────┘
                              │ EventRecord queue
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                        OFFLINE PATH                              │
│               (async, background, no latency pressure)           │
│                                                                  │
│  EventRecord queue → OfflineConsolidationPipeline                │
│    → belief_update_pass (confidence adjustment)                   │
│    → consolidation_pass (NOTE → BELIEF clustering)               │
│    → abstraction_pass (BELIEF → archetype LLM summarisation)     │
│    → reconsolidation_check (contradiction detection)             │
│    → mark events processed                                        │
│                                                                  │
│  Rules:                                                          │
│  • Read from event queue; never read from live user session      │
│  • May call LLM for abstraction (latency is acceptable)          │
│  • Writes to graph and BeliefStore                               │
│  • Produces OfflineProcessingReport for observability            │
└──────────────────────────────────────────────────────────────────┘
```

**The key invariant**: the online path never directly mutates beliefs, never runs consolidation,
and never calls the LLM for deep analysis.  It only captures and responds.

---

## 2. Online Capture — Engineering Details

### What the online path does

1. **Sanitise** — strip control characters, enforce max length.
2. **Classify intent** — regex/keyword router (no LLM); returns labels like
   `FEELING_REPORT`, `REFLECTION`, `META`.
3. **Capture raw signals** — lightweight heuristics to extract `valence`, `arousal`,
   keyword tags without an LLM call.
4. **Journal entry** — append raw text to `JournalStorage`.
5. **EventRecord** — write a typed `EventRecord` to `EventRecordStore` with intent and raw signals.
6. **Reply** — use the already-built graph context (from previous offline passes) to generate a
   response.  The graph context is a **read-only view** at this point.

### What the online path does NOT do

- It does not update beliefs or identity models.
- It does not run consolidation or forgetting.
- It does not call the LLM for extraction during the critical path (extraction happens in the
  background when `background_mode=True`).

### EventRecord: the handoff contract

`EventRecord` (`core/memory/event_record.py`) is the typed contract between online and offline paths:

```python
@dataclass(slots=True)
class EventRecord:
    id: int                         # auto-assigned
    user_id: str
    session_id: str | None
    timestamp: str                  # ISO-8601 UTC
    source: str                     # "cli" | "telegram" | "api"
    text: str                       # sanitised verbatim text
    intent: str                     # router classification
    raw_signals: dict[str, Any]     # cheap heuristic signals
    processed_offline: bool         # False until offline worker runs
    offline_processed_at: str | None
```

This record is **append-only**.  The offline worker reads `processed_offline=False` records,
processes them, and flips the flag.  The raw content of the record is never mutated — this
preserves an auditable event history.

---

## 3. Offline Consolidation — Engineering Details

### OfflineConsolidationPipeline

`core/offline/pipeline.py` implements `OfflineConsolidationPipeline`, the single coordinator
of all offline work:

```python
report = await pipeline.process_pending_events(user_id="u1")
```

The pipeline runs three sub-passes in order:

#### 3a. Belief Update Pass

Reads pending `EventRecord` objects and adjusts belief confidence in `BeliefStore`:

- `FEELING_REPORT` with positive valence → strengthen `self:efficacy` belief.
- `FEELING_REPORT` with negative valence → weaken `self:efficacy` belief.
- `REFLECTION` intent → mild reinforcement of top existing beliefs.
- Other intents → no belief change.

This is intentionally **heuristic and lightweight** — it does not require an LLM.  The point
is to capture the signal, not to analyse it deeply.  Deep analysis is the job of the next pass.

#### 3b. Consolidation Pass

Delegates to `MemoryConsolidator.consolidate()`:

- Finds `NOTE` nodes with low retention scores.
- Clusters them by embedding cosine similarity.
- Merges clusters into `BELIEF` nodes.
- Re-points edges.

This converts high-volume, low-salience episodic traces into lower-volume, higher-salience
semantic memories.  Exactly as the neocortex does during slow-wave sleep.

#### 3c. Abstraction Pass

Delegates to `MemoryConsolidator.abstract()`:

- Finds `BELIEF` nodes at `abstraction_level=1`.
- Clusters them.
- Uses an LLM to produce a one-sentence archetype summary.
- Promotes cluster to `abstraction_level=2`.

This is the "generalisation" step — moving from specific memories to general principles.

---

## 4. Salience and Memory Prioritisation

Memory is not a flat list.  Some memories matter more than others, and what matters changes
with context.  SELF-OS models this with **salience scoring**.

### Ebbinghaus retention

Every edge and node has a decay function based on the Ebbinghaus forgetting curve:

```
retention(t) = e^(-t/S)
```

where `t` is elapsed time and `S` is the stability parameter (raised by each review).
This implements **spaced repetition** — memories that are frequently accessed decay slower.

### Seven-dimensional retrieval scoring

`RetrievalScorer` (`core/retrieval/scoring.py`) scores each candidate along seven dimensions:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Semantic relevance | 30% | Embedding cosine similarity |
| Goal relevance | 20% | Overlap with active goals |
| Identity relevance | 15% | Overlap with identity signals |
| Emotional salience | 10% | Memory emotion × current mood match |
| Recency | 10% | Exponential decay with 30-day half-life |
| Confidence | 10% | Candidate confidence from offline assessment |
| Relationship | 5% | Graph hop distance from query anchor |

The result is a `RetrievalScoreBreakdown` that is fully explainable — every dimension
contributes a score and an explanation string.

---

## 5. Reconsolidation and Revision of Beliefs

### The biological parallel

In biology, a recalled memory is **temporarily destabilised** — it enters a labile state
and can be updated before being re-stabilised.  This is called **reconsolidation**.

Crucially, this means beliefs are **never truly fixed** — they can always be revised given
new evidence.  This is a feature, not a bug.

### BeliefRecord: confidence-weighted, revisable beliefs

`core/beliefs/schema.py` implements beliefs as first-class revisable records:

```python
@dataclass
class BeliefRecord:
    user_id: str
    key: str                          # stable semantic key for de-duplication
    text: str                         # human-readable belief content
    confidence: float                 # [0, 1] — current certainty
    source: str                       # "stated" | "inferred" | "revised"
    domain: str                       # "career" | "health" | "self" | ...
    evidence_refs: list[str]          # IDs of supporting events/nodes
    revision_history: list[BeliefRevision]  # full audit trail
```

Each revision is recorded immutably:

```python
@dataclass(slots=True)
class BeliefRevision:
    timestamp: str
    previous_confidence: float
    new_confidence: float
    reason: str
    evidence_ref: str | None
```

### Update lifecycle

```
New event arrives
  → online path writes EventRecord (raw signal captured)
  → offline path reads EventRecord
  → belief_update_pass computes confidence_delta
  → BeliefStore.apply_evidence(key, delta, reason, evidence_ref)
     → BeliefRecord.apply_evidence(delta, reason, ref)
        → confidence clamped to [0, 1]
        → BeliefRevision appended to history
     → BeliefStore.upsert(updated_record)
```

The belief's full revision history persists — you can always ask "why does the system think
X?" and get a timestamped chain of evidence.

### Contradiction detection

`ReconsolidationEngine` (`core/memory/reconsolidation.py`) detects when a new event
semantically contradicts an existing belief:

- Cosine similarity ∈ `[0.5, 0.75]` → potential contradiction (related but different).
- Similarity > 0.75 → confirmation (same idea, not a contradiction).
- Similarity < 0.5 → unrelated topic.

When a contradiction is detected the belief is flagged for human review or can be
automatically revised with the contra-evidence.

---

## 6. Context-Sensitive Retrieval: MemoryView

### The problem with raw memory access

If the agent could read any node directly from the graph at any time, several problems emerge:

1. **No context gate** — the agent sees everything regardless of relevance.
2. **No confidence filtering** — low-confidence inferences look as authoritative as stated facts.
3. **No auditability** — there is no record of what was retrieved and why.
4. **No scope control** — future agent marketplace participants could access all memories.

### MemoryView: the solution

`core/retrieval/view.py` implements `MemoryView` — a read-only, scoped projection:

```python
policy = MemoryViewPolicy(
    scope="agent",              # what kind of caller?
    caller_id="reply_generator",
    max_results=10,
    min_confidence=0.4,         # low-confidence items hidden from agent
    reason="generating reply",
)
view = MemoryViewBuilder.build(candidates, scored, policy)
```

Three scopes are supported:

| Scope | Description | min_confidence | max_results |
|-------|-------------|---------------|-------------|
| `agent` | Online reply generator | 0.4 | 10 |
| `user_reflection` | User-facing reflection UI | 0.2 | configurable |
| `offline_worker` | Offline consolidation pipeline | 0.0 | unlimited |

The offline worker sees everything (including low-confidence candidates) because it needs to
decide what to consolidate or discard.  The agent sees only high-confidence, highly relevant
items to avoid hallucination-inducing noise.

### Auditability

Every `MemoryView` records:
- Which scope requested it.
- Which caller ID produced it.
- When it was created.
- Why it was requested.
- How many candidates were filtered.

This creates a full audit trail of memory access — essential for user trust and debugging.

---

## 7. Why Simple Local Rules Produce Richer Emergent Behaviour

A common concern about rule-based systems is that they are "too simple" to capture
complex human cognition.  The neuro-inspired insight reverses this concern:

**Complex behaviour does not require complex rules — it requires simple rules applied
consistently over long time horizons with sufficient state accumulation.**

In SELF-OS this works as follows:

1. **Each event is cheap to process online** — just classify, capture signals, write record.
2. **Each offline pass is modular and composable** — belief update, consolidation, abstraction
   run independently and can be extended without breaking each other.
3. **State accumulates in the graph and belief store** — over hundreds of events the system
   builds a rich picture without any single pass needing to understand "everything".
4. **The retrieval layer contextualises this state** — what gets recalled depends on current
   goals, mood, and identity context, not just recency.
5. **Beliefs evolve gradually** — small confidence deltas per event compound into stable
   patterns over time, naturally reflecting the weight of evidence.

The result is emergent personalisation without hardcoded rules about "what kind of person
this user is" — the model discovers that from the event stream.

### Analogy: neuronal assemblies

In neuroscience, **cell assemblies** are groups of neurons that fire together.  No single
neuron encodes a concept — the concept emerges from the pattern of co-activation.
Similarly in SELF-OS:

- No single graph node "is" the user's identity.
- Identity emerges from the co-activation pattern of beliefs, values, goals, and parts.
- The retrieval scorer approximates this co-activation: nodes that consistently score
  high across identity, goal, and emotional dimensions become the effective "self-model".

---

## 8. Architecture Decision Record: Why This Split Matters for the Roadmap

### Current state (after this PR)

```
Online path:      ObserveStage → EventRecord → fast reply
Offline path:     OfflineConsolidationPipeline → BeliefStore + GraphConsolidation
Retrieval gate:   MemoryViewPolicy → MemoryView (scoped, auditable)
```

### What this enables next

1. **Agent marketplace** — third-party agents only ever receive `MemoryView` objects with
   appropriate scope.  They can never read raw graph internals.  This is the privacy
   architecture that makes the marketplace safe.

2. **Neural interface compatibility** — the online path maps cleanly onto a "fast sensor
   input" model.  Signals from wearables or future neural interfaces feed into
   `EventRecord.raw_signals` without changing the offline pipeline.

3. **Multi-user/tenant isolation** — because all belief and event store operations are keyed
   by `user_id`, the architecture naturally supports per-user data isolation.

4. **Measurable retrieval quality** — because retrieval goes through `MemoryView` with full
   score breakdowns, we can now measure precision, recall, and explanation quality per query.

5. **Collaborative memory** — the `scope` mechanism can be extended to `"shared"` scope for
   team/couple memory systems where beliefs are partially shared.

---

## 9. Module Reference for This Architecture

| Module | Role |
|--------|------|
| `core/memory/event_record.py` | `EventRecord` + `EventRecordStore` — online→offline handoff contract |
| `core/offline/pipeline.py` | `OfflineConsolidationPipeline` — coordinates all offline work |
| `core/beliefs/schema.py` | `BeliefRecord` + `BeliefRevision` — revisable beliefs with provenance |
| `core/beliefs/store.py` | `BeliefStore` — confidence-weighted belief persistence |
| `core/retrieval/view.py` | `MemoryView`, `MemoryViewPolicy`, `MemoryViewBuilder` — scoped access |
| `core/memory/consolidator.py` | `MemoryConsolidator` — clustering, abstraction, forgetting |
| `core/memory/reconsolidation.py` | `ReconsolidationEngine` — contradiction detection |
| `core/retrieval/scoring.py` | `RetrievalScorer` — 7-dimensional scoring |
| `core/graph/storage.py` | Graph persistence (source of truth) |

---

## 10. Roadmap: Next Steps After This PR

### Stage 5 (next)

- **Integrate `EventRecordStore` into `ObserveStage`**: write an `EventRecord` on every
  message alongside the existing `JournalEntry`.
- **Wire `OfflineConsolidationPipeline` into `MemoryScheduler`**: add it as a daily job
  so the offline path runs automatically alongside consolidation and forgetting.
- **Replace raw graph access in `ActStage`** with `MemoryViewBuilder.build()` calls:
  the agent should receive a `MemoryView(scope="agent")` for context injection.

### Stage 6 (medium-term)

- **Belief-aware onboarding**: `OnboardingPlanner` should read from `BeliefStore` rather
  than raw graph nodes to know what the system already believes about the user.
- **Confidence-gated replies**: when replying, the agent should qualify statements based
  on belief confidence (e.g. "I think you tend to..." vs "You definitely...").
- **User-facing belief inspection**: expose `BeliefStore` contents via a `/beliefs` command
  in the Telegram interface — "here is what I know about you and how confident I am".

### Stage 7 (long-term)

- **Agent marketplace policy enforcement**: extend `MemoryViewPolicy` with `allowed_types`
  and `allowed_domains` fields.  Third-party agents receive pre-filtered views.
- **Cross-session salience propagation**: use `PredictiveEngine` outputs to pre-weight the
  salience of beliefs and events before retrieval.
- **Privacy controls**: add per-belief `access_level` field (`"private"`, `"agent"`,
  `"export"`) so users can control exactly what gets shared.
