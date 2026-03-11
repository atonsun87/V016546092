"""core.beliefs — revisable probabilistic belief model for SELF-OS.

Beliefs in SELF-OS are NOT stored as facts.  They are probabilistic,
evidence-backed, and revisable.  This package provides:

- ``model.py`` — :class:`RevisableBelief` dataclass with confidence,
  evidence tracking, contradiction tracking, and provenance.
- ``store.py`` — :class:`BeliefStore` for CRUD + revision history.
"""
