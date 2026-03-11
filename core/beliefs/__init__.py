"""core.beliefs — revisable, confidence-weighted belief store.

Re-exports the public API from sub-modules.
"""

from core.beliefs.schema import BeliefRecord, BeliefRevision
from core.beliefs.store import BeliefStore

__all__ = ["BeliefRecord", "BeliefRevision", "BeliefStore"]
