"""Tests for core/memory/provenance.py."""

import asyncio

from core.memory.provenance import (
    MemorySource,
    ProvenanceRecord,
    ProvenanceStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# ProvenanceStore — basic persistence
# ---------------------------------------------------------------------------


def test_save_and_get(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        record = ProvenanceRecord(
            node_id="node-1",
            user_id="user-a",
            source=MemorySource.USER,
            author="user-a",
        )
        await store.save(record)
        retrieved = await store.get("node-1")
        await store.close()
        return retrieved

    result = run(scenario())
    assert result is not None
    assert result.node_id == "node-1"
    assert result.user_id == "user-a"
    assert result.source == MemorySource.USER
    assert result.author == "user-a"
    assert result.edit_history == []


def test_get_nonexistent_returns_none(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        result = await store.get("no-such-node")
        await store.close()
        return result

    assert run(scenario()) is None


def test_save_overwrites_existing(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        r1 = ProvenanceRecord(
            node_id="n1", user_id="u1", source=MemorySource.USER, author="u1"
        )
        await store.save(r1)
        r2 = ProvenanceRecord(
            node_id="n1", user_id="u1", source=MemorySource.LLM, author="llm-agent"
        )
        await store.save(r2)
        result = await store.get("n1")
        await store.close()
        return result

    result = run(scenario())
    assert result is not None
    assert result.source == MemorySource.LLM
    assert result.author == "llm-agent"


# ---------------------------------------------------------------------------
# ProvenanceStore — edit history
# ---------------------------------------------------------------------------


def test_append_edit_creates_history(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        record = ProvenanceRecord(
            node_id="n2", user_id="u1", source=MemorySource.USER, author="u1"
        )
        await store.save(record)
        await store.append_edit(
            node_id="n2",
            user_id="u1",
            author="u1",
            field="text",
            previous_value="old text",
        )
        result = await store.get("n2")
        await store.close()
        return result

    result = run(scenario())
    assert result is not None
    assert len(result.edit_history) == 1
    edit = result.edit_history[0]
    assert edit.field == "text"
    assert edit.previous_value == "old text"
    assert edit.author == "u1"


def test_append_edit_accumulates(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        record = ProvenanceRecord(
            node_id="n3", user_id="u1", source=MemorySource.USER, author="u1"
        )
        await store.save(record)
        for i in range(3):
            await store.append_edit(
                node_id="n3",
                user_id="u1",
                author="u1",
                field="text",
                previous_value=f"version-{i}",
            )
        result = await store.get("n3")
        await store.close()
        return result

    result = run(scenario())
    assert result is not None
    assert len(result.edit_history) == 3
    assert result.edit_history[0].previous_value == "version-0"
    assert result.edit_history[2].previous_value == "version-2"


def test_append_edit_creates_record_if_missing(tmp_path):
    """append_edit should auto-create a provenance record if none exists."""
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        await store.append_edit(
            node_id="orphan-node",
            user_id="u1",
            author="system",
            field="text",
            previous_value=None,
        )
        result = await store.get("orphan-node")
        await store.close()
        return result

    result = run(scenario())
    assert result is not None
    assert result.source == MemorySource.SYSTEM
    assert len(result.edit_history) == 1


# ---------------------------------------------------------------------------
# ProvenanceStore — list and aggregate
# ---------------------------------------------------------------------------


def test_list_for_user(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        for i, src in enumerate(
            [MemorySource.USER, MemorySource.LLM, MemorySource.USER]
        ):
            await store.save(
                ProvenanceRecord(
                    node_id=f"node-{i}",
                    user_id="u1",
                    source=src,
                    author="u1",
                )
            )
        # Different user's record
        await store.save(
            ProvenanceRecord(
                node_id="node-other",
                user_id="u2",
                source=MemorySource.USER,
                author="u2",
            )
        )
        all_u1 = await store.list_for_user("u1")
        user_only = await store.list_for_user("u1", source=MemorySource.USER)
        await store.close()
        return all_u1, user_only

    all_u1, user_only = run(scenario())
    assert len(all_u1) == 3
    assert all(r.user_id == "u1" for r in all_u1)
    assert len(user_only) == 2
    assert all(r.source == MemorySource.USER for r in user_only)


def test_count_by_source(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        sources = [
            MemorySource.USER,
            MemorySource.LLM,
            MemorySource.LLM,
            MemorySource.AGENT,
        ]
        for i, src in enumerate(sources):
            await store.save(
                ProvenanceRecord(
                    node_id=f"n{i}", user_id="u1", source=src, author="u1"
                )
            )
        counts = await store.count_by_source("u1")
        await store.close()
        return counts

    counts = run(scenario())
    assert counts.get("user") == 1
    assert counts.get("llm") == 2
    assert counts.get("agent") == 1


# ---------------------------------------------------------------------------
# ProvenanceStore — delete
# ---------------------------------------------------------------------------


def test_delete_removes_record(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        await store.save(
            ProvenanceRecord(
                node_id="del-node", user_id="u1", source=MemorySource.USER, author="u1"
            )
        )
        await store.delete("del-node")
        result = await store.get("del-node")
        await store.close()
        return result

    assert run(scenario()) is None


# ---------------------------------------------------------------------------
# ProvenanceRecord metadata round-trip
# ---------------------------------------------------------------------------


def test_session_and_pipeline_stage_persisted(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        record = ProvenanceRecord(
            node_id="n-meta",
            user_id="u1",
            source=MemorySource.LLM,
            author="pipeline",
            session_id="sess-abc",
            pipeline_stage="orient",
        )
        await store.save(record)
        result = await store.get("n-meta")
        await store.close()
        return result

    result = run(scenario())
    assert result is not None
    assert result.session_id == "sess-abc"
    assert result.pipeline_stage == "orient"


def test_all_memory_sources_accepted(tmp_path):
    db = str(tmp_path / "prov.db")

    async def scenario():
        store = ProvenanceStore(db_path=db)
        results = []
        for i, src in enumerate(MemorySource):
            record = ProvenanceRecord(
                node_id=f"n-src-{i}",
                user_id="u1",
                source=src,
                author="test",
            )
            await store.save(record)
            results.append(await store.get(f"n-src-{i}"))
        await store.close()
        return results

    results = run(scenario())
    assert all(r is not None for r in results)
    sources_found = {r.source for r in results}
    assert sources_found == set(MemorySource)
