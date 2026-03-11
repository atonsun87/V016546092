# Online Capture vs Offline Consolidation

> **Purpose**: Define the boundary between the fast online capture path and the
> slow, thorough offline consolidation path in SELF-OS.
>
> **Neuro-inspired basis**: The hippocampus rapidly encodes experiences online
> (fast synaptic potentiation), while the cortex consolidates memories during
> rest via slow-wave sleep replay. SELF-OS mirrors this: online = fast + cheap,
> offline = thorough + expensive.

---

## The Two Paths

```
User Message
     │
     ▼
┌──────────────────────────────────────────────────────────────┐
│  ONLINE PATH  (< 200 ms, no LLM extraction)                  │
│  ┌──────────┐   ┌────────────┐   ┌──────────────────────┐   │
│  │ Sanitize │ → │  Classify  │ → │  Fast Reply (cached  │   │
│  │ + Journal│   │  Intent    │   │  context + LLM live) │   │
│  └──────────┘   └────────────┘   └──────────────────────┘   │
│       │                │                                     │
│       ▼                ▼                                     │
│  Event Log       Schedule offline job                        │
└──────────────────────────────────────────────────────────────┘
                          │
                          ▼  (async, seconds to minutes later)
┌──────────────────────────────────────────────────────────────┐
│  OFFLINE PATH  (background worker, LLM-powered)              │
│  ┌──────────────────┐   ┌──────────────────────────────────┐ │
│  │  Memory Extract  │ → │  Graph Update + Belief Revision  │ │
│  │  (LLM semantic   │   │  + Identity Model Update         │ │
│  │  extraction)     │   │  + Motivation Recalc             │ │
│  └──────────────────┘   └──────────────────────────────────┘ │
│                                    │                         │
│                          ┌─────────▼──────────┐             │
│                          │ Consolidation Runs │             │
│                          │ (daily/weekly)     │             │
│                          └────────────────────┘             │
└──────────────────────────────────────────────────────────────┘
```

---

## Online Path (Fast Capture)

**Goal**: Acknowledge the user's message and provide a useful response as quickly as possible.

**Latency budget**: < 200 ms (excluding LLM live reply generation)

**What the online path does**:

1. **Sanitize** — remove PII artifacts, normalize whitespace
2. **Journal** — write raw event to immutable `event_log` (append only)
3. **Classify intent** — rule-based or tiny model (REFLECTION / FEELING / EVENT / TASK / IDEA / META)
4. **Build fast context** — retrieve cached `PsycheState` + `MotivationState` (no re-computation)
5. **Generate live reply** — LLM call with cached context (this is the only LLM call online)
6. **Schedule offline job** — enqueue `OfflineConsolidationJob` with raw event ID

**What the online path does NOT do**:
- ❌ LLM semantic extraction (graph node creation)
- ❌ Belief revision
- ❌ Identity profile rebuild
- ❌ Memory consolidation
- ❌ Embedding generation
- ❌ Pattern analysis

**Output**: `OnlineCaptureResult`

```python
@dataclass
class OnlineCaptureResult:
    event_id: str            # immutable event log ID
    intent_class: str        # classified intent
    reply: str               # live reply text
    job_id: str              # offline job ID (for tracking)
    latency_ms: float        # measured end-to-end latency
```

---

## Offline Path (Consolidation)

**Goal**: Extract structured knowledge from raw events and integrate into the long-term memory graph.

**Latency budget**: 30 seconds per job (soft), 5 minutes (hard)

**Trigger conditions**:
- New `OfflineConsolidationJob` in the queue (from online path)
- Scheduled consolidation run (configurable interval, default 5 min)
- Manual trigger via admin API

**What the offline path does**:

### Phase 1: Memory Extraction (per message)
1. **Semantic extraction** — LLM extracts: beliefs, emotions, needs, values, projects, tasks, events
2. **Embedding generation** — embed extracted nodes for vector search
3. **Graph integration** — find_or_create nodes with deduplication by key
4. **Provenance tagging** — tag all new nodes with `source_event_ids`

### Phase 2: Belief Revision (per extraction batch)
1. **Conflict detection** — scan for new nodes that contradict existing beliefs
2. **Confidence update** — apply Bayesian update to affected beliefs
3. **User flag** — if conflict is significant, flag for user review

### Phase 3: Self-Model Update (per session / periodically)
1. **Identity profile rebuild** — `SelfModelUpdateService` synthesizes updated `IdentityProfile`
2. **Value tension detection** — detect new `CONFLICTS_WITH` edges between VALUES
3. **Motivation recalc** — rebuild `MotivationState` from updated graph

### Phase 4: Periodic Consolidation (daily / weekly)
1. **Daily**: merge similar NOTE clusters → BELIEF/THOUGHT nodes
2. **Weekly**: abstract BELIEF/THOUGHT → higher-level semantic nodes
3. **Weekly**: apply forgetting curve, archive low-retention nodes
4. **Monthly**: spaced repetition scheduling for important memories

**Input**: `OfflineConsolidationJob`

```python
@dataclass
class OfflineConsolidationJob:
    job_id: str
    user_id: str
    event_id: str          # source event in event_log
    raw_text: str          # original message text (for extraction)
    intent_class: str      # already classified (from online path)
    scheduled_at: str      # when to run (ISO timestamp)
    priority: int          # 0=immediate, 1=normal, 2=background
```

---

## Neurobiological Parallel

| Neuroscience | SELF-OS Equivalent |
|---|---|
| Sensory cortex rapid encoding | Online path — raw event capture |
| Hippocampal indexing (fast) | Online intent classification + journal |
| Hippocampal replay during sleep | Offline consolidation jobs |
| Cortical slow-wave consolidation | Daily/weekly consolidation phases |
| Hebbian potentiation | Confidence increase for reinforced beliefs |
| Synaptic decay / pruning | Forgetting curve + archive phase |
| Reconsolidation (memory update) | Belief revision with new evidence |
| Working memory | `PsycheState` + `SessionMemory` (in-memory cache) |

---

## Configuration

```python
# core/defaults.py additions

ONLINE_BUDGET_MS: int = 200          # p95 target for online path (excluding LLM)
OFFLINE_JOB_MAX_DELAY_S: int = 300   # max delay before offline job runs
OFFLINE_JOB_TIMEOUT_S: int = 30      # soft timeout per extraction job
OFFLINE_CONSOLIDATION_INTERVAL_MIN: int = 5   # how often to poll for new jobs
OFFLINE_DAILY_CONSOLIDATION_HOUR: int = 3     # 3 AM local time
OFFLINE_WEEKLY_CONSOLIDATION_DAY: int = 0     # Sunday
```

---

## Implementation Notes

### Queue Backend (v0)
For v0, `OfflineConsolidationJob` objects are stored in SQLite:
- Table: `consolidation_jobs`
- Worker: APScheduler polling every `OFFLINE_CONSOLIDATION_INTERVAL_MIN`
- Status: `pending | running | completed | failed`

### Future Queue Backend (v1+)
Replace SQLite queue with Redis Streams or Celery for higher throughput.

### Background Worker
`MemoryScheduler` (already exists) is extended to poll `consolidation_jobs` and
dispatch `OfflineConsolidationJob` to `OfflineConsolidator`.

---

## Key Invariants

1. **The online path never blocks on LLM extraction** — only on live reply generation.
2. **Every raw event is journaled before any reply is sent** — durability first.
3. **Offline jobs are idempotent** — re-running a job with the same `event_id` is safe.
4. **Offline jobs carry provenance forward** — every node created has `source_event_ids`.
5. **The reply uses cached PsycheState** — no rebuild inline; freshness trades off for speed.
6. **If offline fails, the event is not lost** — it stays in `event_log`; retry is possible.
