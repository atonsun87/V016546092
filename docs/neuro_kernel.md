# Memory Kernel — Neurobiological Foundations & Engineering Design

## Overview

The **Memory Kernel** (`core/kernel/`) defines the lowest-level primitive
contracts that all higher-level memory and agent modules must honour.  It
introduces four core types:

| Type | Module | Role |
|------|--------|------|
| `RawSignal` | `core/kernel/signal.py` | Immutable, timestamped event record |
| `DerivedBelief` | `core/kernel/signal.py` | Revisable, confidence-weighted conclusion |
| `BeliefStore` | `core/kernel/belief_store.py` | Graph-backed persistence + revision for beliefs |
| `MemoryScope` | `core/kernel/memory_scope.py` | Policy-aware, scoped read view for agents |

These four types solve four architectural weaknesses that prevent simple
local mechanisms from composing into a powerful memory system:

1. **No clear separation between raw events and derived memories.**
2. **Beliefs stored as fixed facts with no revision path.**
3. **Retrieval not constrained by policy — agents access the full graph.**
4. **Agents directly own memory internals instead of scoped views.**

---

## Neurobiological Principles (in Engineering Terms)

### 1. Sensory Input vs Semantic Memory → `RawSignal` vs `DerivedBelief`

The brain maintains a fundamental distinction between:

* **Episodic / sensory traces** — what actually happened, stamped with time
  and context.  These are laid down by the hippocampus and are highly
  resistant to retroactive modification.
* **Semantic memories** — generalised knowledge derived from many episodic
  episodes.  These live in the cortex and *can* be updated as new evidence
  arrives.

In SELF-OS this maps to:

```
User types a message  →  RawSignal  (immutable, archived in JournalStorage)
                               │
                    ┌──────────▼──────────┐
                    │  Consolidation      │  (MemoryConsolidator, nightly)
                    └──────────┬──────────┘
                               │
                        DerivedBelief  (revisable, stored as BELIEF node)
```

`RawSignal` is `frozen=True` in Python.  Once created, it cannot be altered.
This mirrors the hippocampal trace that forms the ground truth of "what was
said."

`DerivedBelief` has a `revise()` method that updates the statement and
confidence *while preserving the previous state in `revision_history`*.
This mirrors reconsolidation — memories are not simply overwritten; the
prior version is retained as a chain of evidence.

### 2. Reconsolidation → `DerivedBelief.revise()` + `BeliefStore.revise()`

In neuroscience, **reconsolidation** is the process by which a retrieved
memory becomes temporarily labile (malleable) and can be updated before
being re-stored.  This allows the brain to incorporate new context without
losing the provenance chain.

```python
belief = await store.get(user_id, belief_id)

# Belief retrieved → now labile.  New evidence found.
await store.revise(
    belief,
    new_statement="I can be outgoing in some contexts.",
    new_confidence=0.6,
    reason="contradicted by signal-42: 'I really enjoyed that party'",
)
```

After this call:

* `belief.statement` = new text
* `belief.confidence` = 0.6
* `belief.revision_history[0]` = `{statement: "I enjoy solitude.", confidence: 0.9, reason: "…"}`

The full chain is persisted in `node.metadata["belief_provenance"]` so any
audit query can trace back to the original raw signals that produced the
belief.

### 3. Context-Gated Retrieval → `MemoryScope`

The prefrontal cortex does not give all brain regions equal access to all
memories at all times.  Access is *gated* by the current task context,
emotional state, and role (e.g. the part of you at work vs. the part at
home).

`MemoryScope` models this gating.  Each agent or interface receives a scope
configured for its role:

```python
# IFS Manager agent — only allowed to read cognitive/motivational nodes
manager_scope = MemoryScope(
    storage=graph_storage,
    user_id=user_id,
    allowed_types=frozenset({"BELIEF", "VALUE", "GOAL", "NEED"}),
    label="ifs_manager_agent",
)

# Background analytics — full access (privileged scope)
analytics_scope = MemoryScope(
    storage=graph_storage,
    user_id=user_id,
    # allowed_types=None → unrestricted
    label="background_analytics",
)
```

Attempting to read outside the scope raises `PermissionError` at
*development time*, making access violations explicit rather than silently
returning wrong data.

Scopes can be narrowed further via `scope.narrow(allowed_types)` — the
intersection rule ensures narrowing can never expand permissions, only
reduce them.

### 4. Local Plasticity → Typed Stores as Domain Boundaries

In the brain, each region specialises.  The amygdala handles emotional
salience; the hippocampus handles episodic encoding; the cortex handles
semantic consolidation.  Regions communicate via well-defined pathways, not
by reaching directly into each other's internals.

In SELF-OS, the kernel establishes the same pattern:

```
Agent / Interface
       │
       ▼
 MemoryScope (read-only, scoped)
       │
       ▼
 BeliefStore (typed read/write for beliefs)
       │
       ▼
 GraphStorage (raw persistence layer — not touched directly by agents)
```

`BeliefStore` is the *only* path for creating or revising beliefs.  It
enforces that every write includes provenance (`source_signal_ids`) and that
every revision preserves history.  This is the engineering equivalent of the
hippocampus-to-cortex transfer: structured, traceable, never silent.

---

## Usage Examples

### Creating a belief from signals

```python
from core.kernel import RawSignal, DerivedBelief, BeliefStore

# Raw signals (immutable — record what happened)
sig1 = RawSignal(user_id="u1", source="telegram_message",
                 content="I always feel anxious at parties.")
sig2 = RawSignal(user_id="u1", source="journal_entry",
                 content="Cancelled plans again because of social anxiety.")

# Derived belief (revisable — what we conclude from the signals)
belief = DerivedBelief(
    user_id="u1",
    statement="This user experiences social anxiety.",
    confidence=0.85,
    source_signal_ids=[sig1.id, sig2.id],
)

store = BeliefStore(graph_storage)
await store.save(belief)
```

### Revising a belief on new evidence

```python
# Later, a contradicting signal arrives
new_sig = RawSignal(user_id="u1", source="telegram_message",
                    content="I actually had a great time at the meetup!")

# Retrieve → revise → persist (reconsolidation)
belief = await store.get("u1", belief_id)
belief.source_signal_ids.append(new_sig.id)
await store.revise(
    belief,
    new_statement="User has context-dependent social anxiety; responds well to structured social events.",
    new_confidence=0.65,
    reason=f"contradicted by signal {new_sig.id}",
)
```

### Giving an agent a scoped view

```python
from core.kernel import MemoryScope

# IFS Firefighter agent — emotional access only
firefighter_scope = MemoryScope(
    storage=graph_storage,
    user_id=user_id,
    allowed_types=frozenset({"EMOTION", "NEED", "PART"}),
    label="ifs_firefighter",
)

emotions = await firefighter_scope.find("EMOTION", limit=10)
# Attempting firefighter_scope.find("BELIEF") → PermissionError
```

---

## Integration with Existing Modules

The kernel is *additive*, not destructive.  Existing modules continue to
work without modification.  The kernel provides a foundation that new and
refactored modules can adopt incrementally:

| Existing Module | Kernel Integration Path |
|----------------|------------------------|
| `core/memory/reconsolidation.py` | Use `BeliefStore.revise()` instead of direct node upsert |
| `core/memory/consolidator.py` | Produce `DerivedBelief` objects from clustered NOTE nodes |
| `agents/ifs/parts.py` | Receive `MemoryScope` instead of raw `GraphAPI` |
| `core/pipeline/stage_orient.py` | Pass `MemoryScope` to RAG retriever |
| `core/journal/storage.py` | Persist `RawSignal` objects in addition to raw text |

---

## Design Rules

1. **`RawSignal` is always immutable.** Never create a mutable version.
2. **`DerivedBelief.revise()` always appends to history.**  Never overwrite
   `revision_history` from outside the class.
3. **Agents receive `MemoryScope`, not `GraphStorage`.**  The scope label
   must identify the agent role.
4. **All belief writes go through `BeliefStore`.**  Never call
   `GraphStorage.upsert_node` for a BELIEF node from agent code.
5. **Scope narrowing cannot expand permissions.**  The intersection rule in
   `MemoryScope.narrow()` enforces this structurally.

---

## Future Evolution

The kernel is designed to remain small and stable as the system grows:

* **Salience gating** — `MemoryScope` can gain an `emotional_state` parameter
  that biases `find()` results toward emotionally salient nodes (analogous
  to amygdala modulation of hippocampal retrieval).
* **Signal store** — `RawSignal` objects currently live in `JournalStorage`
  as raw text; a typed `SignalStore` wrapper would mirror `BeliefStore`.
* **Belief confidence decay** — the memory scheduler can periodically decay
  `DerivedBelief.confidence` using Ebbinghaus curves, then call
  `BeliefStore.revise()` to persist the updated confidence.
* **Cross-user belief federation** — `MemoryScope` can be extended with a
  `federation_policy` for future agent marketplace scenarios where one agent
  needs scoped read access to another user's memory (with explicit consent).
