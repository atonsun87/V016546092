# SELF-OS Architecture Principles

These principles are the **architectural invariants** of the SELF-OS system.
Every pull request must be reviewed against them.  Violations require explicit
justification and team agreement before merge.

---

## The Eight Invariants

1. **All user experience enters the system as append-only events.**
   Every message, observation, or signal is immediately written to the durable
   `EventStore` (online phase) before any processing begins.  Events are never
   updated or deleted — they are the immutable audit trail.

2. **Online interaction is optimised for responsiveness and signal capture.**
   The online path (OBSERVE → fast reply) must never block on consolidation,
   heavy LLM extraction, or long-running DB writes.  Its job is to capture the
   signal and return a timely response.

3. **Offline consolidation performs deep interpretation and memory formation.**
   Clustering, abstraction, belief derivation, salience recalculation, and
   memory compression happen in the offline phase.  The offline worker reads
   from the `EventStore`; it never writes to the in-session context.

4. **Inferred beliefs are probabilistic, revisable, and evidence-backed.**
   `DerivedBelief` carries a `confidence` score, a `source_signal_ids` list,
   and a full `revision_history`.  User corrections always take precedence
   over inferred beliefs.

5. **Retrieval returns scoped memory views, not unrestricted storage access.**
   Agents and interfaces receive a `MemoryScope` or equivalent scoped view.
   Direct access to `GraphStorage` internals from agent code is prohibited.

6. **Agents operate through contracts and policies, never through raw internals.**
   Every agent action is logged as an event (`MemorySource.AGENT` provenance).
   Agents communicate through the `Protocol` contracts defined in
   `core/contracts.py`.

7. **User corrections override inference.**
   Any explicit user correction must immediately revise the affected
   `DerivedBelief` and lower the confidence of competing inferences.
   Provenance of the correction is stored so it can be audited.

8. **Provenance is required for durable memory claims.**
   Every `BELIEF` node written to the knowledge graph must carry a
   `ProvenanceRecord` (source, author, pipeline stage, timestamp).
   Nodes without provenance are considered untrustworthy and must be flagged.

---

## Allowed Dependency Directions

```
interfaces  →  application / retrieval APIs
agents      →  retrieval / policy / action APIs
consolidation  →  memory / beliefs / kernel
retrieval   →  memory / beliefs / policy
beliefs     →  kernel + evidence sources
memory      →  kernel
```

## Prohibited Dependency Directions

```
interfaces      ↛  consolidation internals
agents          ↛  raw storage internals
policy          ↛  interfaces (no circular policy→UI dependency)
memory          ↛  agent-specific classes
beliefs         ↛  chat / session objects
```

Violations of these rules introduce coupling that makes it impossible to
independently evolve online latency, offline quality, and agent safety.

---

## Key Domain Concepts

| Concept | Definition |
|---|---|
| **RawSignal** | Immutable, timestamped record of something that happened. Never modified. |
| **DerivedBelief** | Revisable, confidence-weighted conclusion derived from signals. |
| **EpisodicMemory** | A meaningful episode constructed from one or more raw signals during consolidation. |
| **SemanticBelief** | A probabilistic statement about the user or world (maps to `DerivedBelief`). |
| **GoalState** | Dynamic representation of the user's current goals and progress. |
| **RetrievalView** | Context- and policy-constrained read-only view of memory. |
| **AgentActionRecord** | Auditable record of an agent action, stored as an event with `AGENT` provenance. |

---

## Online / Offline Split

```
User message / signal
  → Online interface (ObserveStage)
  → Raw Event appended to EventStore          ← online boundary
  → Fast reply to user

Background worker / offline cycle
  → Read new events from EventStore
  → Build candidate episodic traces
  → Merge into episodic memories
  → Revise semantic beliefs
  → Recalculate salience
  → Compress / decay low-value traces

Agent request
  → Policy scope check
  → Retrieval query (scoped view)
  → Agent action
  → Action logged as Event (AGENT provenance)
```

The online phase **must not** perform deep consolidation.
The offline phase **must not** write to the active session context.

---

## Module Contracts

Core Protocol interfaces are defined in `core/contracts.py`.  All cross-module
communication must go through these contracts, not through concrete class
imports across domain boundaries.

See [docs/architecture.md](docs/architecture.md) for the full layered
architecture diagram and module responsibilities.

See [STABILIZATION.md](STABILIZATION.md) for the prioritised list of
remaining stabilisation work.
