"""Tests for core/observability/metrics.py — MetricsCollector."""

import time

import pytest

from core.observability.metrics import MetricsCollector, SystemSnapshot

# ---------------------------------------------------------------------------
# Isolation: use fresh instances, not the singleton
# ---------------------------------------------------------------------------


@pytest.fixture()
def collector():
    """Return a fresh, isolated MetricsCollector for each test."""
    return MetricsCollector()


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


def test_counter_starts_at_zero(collector):
    assert collector.get_counter("new_counter") == 0


def test_increment_default(collector):
    collector.increment("messages_processed")
    assert collector.get_counter("messages_processed") == 1


def test_increment_by_amount(collector):
    collector.increment("items", 5)
    collector.increment("items", 3)
    assert collector.get_counter("items") == 8


def test_multiple_counters_independent(collector):
    collector.increment("a")
    collector.increment("b")
    collector.increment("b")
    assert collector.get_counter("a") == 1
    assert collector.get_counter("b") == 2


# ---------------------------------------------------------------------------
# Latencies
# ---------------------------------------------------------------------------


def test_latency_summary_none_when_no_samples(collector):
    assert collector.latency_summary("pipeline_ms") is None


def test_record_latency_and_summary(collector):
    for ms in [10.0, 20.0, 30.0, 40.0, 50.0]:
        collector.record_latency("retrieval_ms", ms)

    summary = collector.latency_summary("retrieval_ms")
    assert summary is not None
    assert summary.count == 5
    assert summary.mean_ms == pytest.approx(30.0)
    assert summary.min_ms == pytest.approx(10.0)
    assert summary.max_ms == pytest.approx(50.0)
    assert 20.0 <= summary.p50_ms <= 40.0
    assert summary.p95_ms >= 40.0


def test_latency_ring_buffer_bounded(collector):
    """After 500+ samples the oldest should be dropped."""
    for i in range(600):
        collector.record_latency("heavy_op", float(i))
    summary = collector.latency_summary("heavy_op")
    assert summary is not None
    assert summary.count == 500  # ring buffer max


# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------


def test_gauge_returns_none_when_unset(collector):
    assert collector.get_gauge("active_users") is None


def test_set_and_get_gauge(collector):
    collector.set_gauge("active_users", 42.0)
    assert collector.get_gauge("active_users") == pytest.approx(42.0)


def test_gauge_can_decrease(collector):
    collector.set_gauge("queue_depth", 100.0)
    collector.set_gauge("queue_depth", 3.0)
    assert collector.get_gauge("queue_depth") == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot_contains_counters_and_gauges(collector):
    collector.increment("processed")
    collector.set_gauge("memory_mb", 256.0)
    snapshot = collector.snapshot()
    assert isinstance(snapshot, SystemSnapshot)
    assert snapshot.counters.get("processed") == 1
    assert snapshot.gauges.get("memory_mb") == pytest.approx(256.0)


def test_snapshot_to_dict(collector):
    collector.increment("sessions", 3)
    collector.record_latency("pipeline_ms", 55.0)
    collector.set_gauge("node_count", 1000.0)

    d = collector.snapshot().to_dict()
    assert d["counters"]["sessions"] == 3
    assert d["gauges"]["node_count"] == pytest.approx(1000.0)
    assert "pipeline_ms" in d["latencies"]
    assert d["latencies"]["pipeline_ms"]["count"] == 1
    assert "captured_at" in d


def test_snapshot_is_immutable_copy(collector):
    collector.increment("x")
    snap = collector.snapshot()
    collector.increment("x")
    # Snapshot should not reflect the new increment
    assert snap.counters["x"] == 1


# ---------------------------------------------------------------------------
# Timed context manager
# ---------------------------------------------------------------------------


def test_timed_records_elapsed(collector):
    with collector.timed("op_ms"):
        time.sleep(0.01)  # 10 ms

    summary = collector.latency_summary("op_ms")
    assert summary is not None
    assert summary.count == 1
    assert summary.mean_ms >= 5.0  # allow some timer slack


def test_timed_multiple_invocations(collector):
    for _ in range(3):
        with collector.timed("loop_ms"):
            time.sleep(0.005)

    summary = collector.latency_summary("loop_ms")
    assert summary is not None
    assert summary.count == 3


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_singleton_returns_same_instance():
    MetricsCollector.reset_instance()
    inst1 = MetricsCollector.get_instance()
    inst2 = MetricsCollector.get_instance()
    assert inst1 is inst2
    MetricsCollector.reset_instance()


def test_reset_instance_clears_singleton():
    MetricsCollector.reset_instance()
    inst1 = MetricsCollector.get_instance()
    MetricsCollector.reset_instance()
    inst2 = MetricsCollector.get_instance()
    assert inst1 is not inst2
    MetricsCollector.reset_instance()


# ---------------------------------------------------------------------------
# Thread safety (basic)
# ---------------------------------------------------------------------------


def test_concurrent_increments_are_safe(collector):
    import threading

    def worker():
        for _ in range(100):
            collector.increment("concurrent")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert collector.get_counter("concurrent") == 1000
