"""SELF-OS Memory Kernel.

The kernel package defines the primitive contracts that all higher-level
memory and agent modules must honour:

* :class:`~core.kernel.signal.RawSignal` — immutable, timestamped event records.
* :class:`~core.kernel.signal.DerivedBelief` — revisable, confidence-weighted
  conclusions with full provenance tracking.
* :class:`~core.kernel.belief_store.BeliefStore` — graph-backed persistence and
  revision store for ``DerivedBelief`` objects.
* :class:`~core.kernel.memory_scope.MemoryScope` — policy-aware, permission-
  controlled read-only view over the memory graph for agents.

See ``docs/neuro_kernel.md`` for design rationale and neurobiological inspiration.
"""

from core.kernel.belief_store import BeliefStore
from core.kernel.memory_scope import MemoryScope
from core.kernel.signal import DerivedBelief, RawSignal

__all__ = [
    "BeliefStore",
    "DerivedBelief",
    "MemoryScope",
    "RawSignal",
]
