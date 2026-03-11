"""Tests for core/memory/trust.py — MemoryTrustService."""

import asyncio

from core.graph.api import GraphAPI
from core.graph.model import Node
from core.graph.storage import GraphStorage
from core.memory.provenance import MemorySource, ProvenanceRecord, ProvenanceStore
from core.memory.trust import MemoryTrustService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


async def _make_service(db_path: str):
    storage = GraphStorage(db_path=db_path)
    api = GraphAPI(storage)
    prov_store = ProvenanceStore(db_path=db_path)
    await prov_store.ensure_initialized()
    service = MemoryTrustService(api, prov_store)
    return service, storage, prov_store


# ---------------------------------------------------------------------------
# Edit node text
# ---------------------------------------------------------------------------


def test_edit_node_text_success(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="NOTE", text="original text", key="note:test")
        node = await storage.upsert_node(node)
        await prov.save(
            ProvenanceRecord(
                node_id=node.id, user_id="u1", source=MemorySource.USER, author="u1"
            )
        )

        result = await service.edit_node_text("u1", node.id, "updated text")
        updated = await storage.get_node(node.id)
        prov_after = await prov.get(node.id)
        await storage.close()
        await prov.close()
        return result, updated, prov_after

    result, updated, prov_after = run(scenario())
    assert result.success is True
    assert updated.text == "updated text"
    assert prov_after is not None
    assert len(prov_after.edit_history) == 1
    assert prov_after.edit_history[0].field == "text"
    assert prov_after.edit_history[0].previous_value == "original text"


def test_edit_node_text_wrong_user(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="NOTE", text="secret")
        node = await storage.upsert_node(node)
        result = await service.edit_node_text("u2", node.id, "hacked")
        await storage.close()
        await prov.close()
        return result

    result = run(scenario())
    assert result.success is False
    assert "Permission denied" in result.message


def test_edit_node_text_nonexistent(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        result = await service.edit_node_text("u1", "no-such-node", "text")
        await storage.close()
        await prov.close()
        return result

    result = run(scenario())
    assert result.success is False
    assert "not found" in result.message


# ---------------------------------------------------------------------------
# Edit node name
# ---------------------------------------------------------------------------


def test_edit_node_name_success(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="VALUE", name="old name")
        node = await storage.upsert_node(node)
        result = await service.edit_node_name("u1", node.id, "new name")
        updated = await storage.get_node(node.id)
        prov_after = await prov.get(node.id)
        await storage.close()
        await prov.close()
        return result, updated, prov_after

    result, updated, prov_after = run(scenario())
    assert result.success is True
    assert updated.name == "new name"
    assert prov_after is not None
    assert prov_after.edit_history[0].field == "name"
    assert prov_after.edit_history[0].previous_value == "old name"


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------


def test_soft_delete_success(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="THOUGHT", text="fleeting thought")
        node = await storage.upsert_node(node)
        result = await service.soft_delete_node("u1", node.id)
        # Node should not appear in normal find_nodes
        remaining = await storage.find_nodes("u1")
        prov_after = await prov.get(node.id)
        await storage.close()
        await prov.close()
        return result, remaining, prov_after

    result, remaining, prov_after = run(scenario())
    assert result.success is True
    assert all(n.id != result.node_id for n in remaining)
    # Provenance record with edit history is retained
    assert prov_after is not None
    assert any(e.field == "is_deleted" for e in prov_after.edit_history)


def test_soft_delete_wrong_user(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="BELIEF", text="private belief")
        node = await storage.upsert_node(node)
        result = await service.soft_delete_node("u2", node.id)
        await storage.close()
        await prov.close()
        return result

    result = run(scenario())
    assert result.success is False
    assert "Permission denied" in result.message


# ---------------------------------------------------------------------------
# Hard delete
# ---------------------------------------------------------------------------


def test_hard_delete_removes_node_and_provenance(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="NOTE", text="to be erased")
        node = await storage.upsert_node(node)
        await prov.save(
            ProvenanceRecord(
                node_id=node.id, user_id="u1", source=MemorySource.USER, author="u1"
            )
        )
        result = await service.hard_delete_node("u1", node.id)
        prov_after = await prov.get(node.id)
        nodes_after = await storage.find_nodes("u1")
        await storage.close()
        await prov.close()
        return result, prov_after, nodes_after

    result, prov_after, nodes_after = run(scenario())
    assert result.success is True
    assert prov_after is None
    assert all(n.id != result.node_id for n in nodes_after)


def test_hard_delete_also_removes_edges(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        from core.graph.model import Edge

        n1 = await storage.upsert_node(Node(user_id="u1", type="NOTE", text="n1"))
        n2 = await storage.upsert_node(Node(user_id="u1", type="THOUGHT", text="n2"))
        edge = Edge(
            user_id="u1",
            source_node_id=n1.id,
            target_node_id=n2.id,
            relation="RELATES_TO",
        )
        await storage.add_edge(edge)

        result = await service.hard_delete_node("u1", n1.id)
        edges_after = await storage.list_edges("u1")
        await storage.close()
        await prov.close()
        return result, edges_after

    result, edges_after = run(scenario())
    assert result.success is True
    assert len(edges_after) == 0


def test_hard_delete_wrong_user(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="VALUE", name="integrity")
        node = await storage.upsert_node(node)
        result = await service.hard_delete_node("u2", node.id)
        await storage.close()
        await prov.close()
        return result

    result = run(scenario())
    assert result.success is False
    assert "Permission denied" in result.message


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def test_export_user_data_contains_nodes_and_edges(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        from core.graph.model import Edge

        n1 = await storage.upsert_node(Node(user_id="u1", type="NOTE", text="memory 1"))
        n2 = await storage.upsert_node(Node(user_id="u1", type="THOUGHT", text="thought"))
        edge = Edge(
            user_id="u1",
            source_node_id=n1.id,
            target_node_id=n2.id,
            relation="RELATES_TO",
        )
        await storage.add_edge(edge)
        await prov.save(
            ProvenanceRecord(
                node_id=n1.id, user_id="u1", source=MemorySource.USER, author="u1"
            )
        )

        export = await service.export_user_data("u1")
        json_str = export.to_json()
        await storage.close()
        await prov.close()
        return export, json_str

    export, json_str = run(scenario())
    assert export.node_count == 2
    assert export.edge_count == 1
    assert export.user_id == "u1"
    assert len(export.nodes) == 2
    assert len(export.edges) == 1
    assert len(export.provenance) == 1
    # Verify JSON is valid and contains expected keys
    import json
    parsed = json.loads(json_str)
    assert parsed["node_count"] == 2
    assert parsed["edge_count"] == 1


def test_export_empty_user(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        export = await service.export_user_data("empty-user")
        await storage.close()
        await prov.close()
        return export

    export = run(scenario())
    assert export.node_count == 0
    assert export.edge_count == 0
    assert export.provenance == []


# ---------------------------------------------------------------------------
# Inspect provenance
# ---------------------------------------------------------------------------


def test_get_memory_provenance(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="BELIEF", text="I am capable")
        node = await storage.upsert_node(node)
        record = ProvenanceRecord(
            node_id=node.id,
            user_id="u1",
            source=MemorySource.ONBOARDING,
            author="onboarding-bot",
            pipeline_stage="observe",
        )
        await prov.save(record)
        result = await service.get_memory_provenance("u1", node.id)
        await storage.close()
        await prov.close()
        return result

    result = run(scenario())
    assert result is not None
    assert result.source == MemorySource.ONBOARDING
    assert result.author == "onboarding-bot"
    assert result.pipeline_stage == "observe"


def test_get_memory_provenance_wrong_user_returns_none(tmp_path):
    db = str(tmp_path / "trust.db")

    async def scenario():
        service, storage, prov = await _make_service(db)
        node = Node(user_id="u1", type="NOTE", text="private")
        node = await storage.upsert_node(node)
        await prov.save(
            ProvenanceRecord(
                node_id=node.id, user_id="u1", source=MemorySource.USER, author="u1"
            )
        )
        result = await service.get_memory_provenance("u2", node.id)
        await storage.close()
        await prov.close()
        return result

    assert run(scenario()) is None
