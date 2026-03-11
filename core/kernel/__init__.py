"""core.kernel — stable kernel boundary for SELF-OS.

This package defines the thin, stable contracts at the heart of the system:

- ``event_types`` — typed event registry (all system events must be registered here)
- ``policy`` — policy engine controlling memory access by scope
- ``contracts`` — agent contracts, memory views, and violation handling

Architecture rule: ``core.kernel`` has NO imports from other ``core.*`` subpackages
(no graph, no identity, no motivation). Everything depends on kernel; kernel
depends on nothing but the standard library and dataclasses.
"""
