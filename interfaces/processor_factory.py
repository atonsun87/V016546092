from __future__ import annotations

import logging

from config import (
    DB_PATH as _DEFAULT_DB_PATH,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_URL,
)
from core.analytics.calibrator import ThresholdCalibrator
from core.context.session_memory import SessionMemory
from core.graph.api import GraphAPI
from core.graph.storage import GraphStorage
from core.journal.storage import JournalStorage
from core.llm.embedding_service import EmbeddingService
from core.llm_client import OpenRouterQwenClient
from core.memory.provenance import ProvenanceStore
from core.pipeline.event_store import EventStore
from core.pipeline.processor import MessageProcessor
from core.search.qdrant_storage import QdrantVectorStorage

logger = logging.getLogger(__name__)


class _NoopQdrant:
    def upsert_embeddings_batch(self, points: list[dict]) -> None:
        return

    def search_similar(self, *args, **kwargs) -> list:
        return []


def build_processor(db_path: str | None = None, *, background_mode: bool = True) -> MessageProcessor:
    resolved = db_path or _DEFAULT_DB_PATH
    graph_storage = GraphStorage(db_path=resolved)
    graph_api = GraphAPI(graph_storage)
    journal_storage = JournalStorage(db_path=resolved)
    llm_client = OpenRouterQwenClient()
    embedding_service: EmbeddingService | None = None
    try:
        get_client = getattr(llm_client, "_get_client", None)
        if callable(get_client):
            client = get_client()
            if client is not None:
                embedding_service = EmbeddingService(client)
    except Exception as exc:
        logger.warning("EmbeddingService init failed, disabling: %s", exc)
        embedding_service = None

    try:
        qdrant: QdrantVectorStorage | _NoopQdrant = QdrantVectorStorage(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY or None,
            collection_name=QDRANT_COLLECTION,
        )
    except Exception as exc:
        logger.warning("Qdrant connection failed, using NoopQdrant: %s", exc)
        qdrant = _NoopQdrant()

    session_memory = SessionMemory()
    calibrator = ThresholdCalibrator(graph_storage)
    event_store = EventStore(db_path=resolved)
    provenance_store = ProvenanceStore(db_path=resolved)
    return MessageProcessor(
        graph_api=graph_api,
        journal=journal_storage,
        qdrant=qdrant,
        session_memory=session_memory,
        llm_client=llm_client,
        embedding_service=embedding_service,
        calibrator=calibrator,
        background_mode=background_mode,
        event_store=event_store,
        provenance_store=provenance_store,
    )
