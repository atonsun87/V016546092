"""core.offline — offline memory consolidation pipeline.

This package provides the explicit offline processing path that is kept
architecturally separate from the online interaction path.

Public API::

    from core.offline import OfflineConsolidationPipeline, OfflineProcessingReport

The offline worker runs asynchronously and is never invoked on the hot path
that produces user replies.  It reads from the event record store and writes
back to the graph and belief store.
"""

from core.offline.pipeline import OfflineConsolidationPipeline, OfflineProcessingReport

__all__ = ["OfflineConsolidationPipeline", "OfflineProcessingReport"]
