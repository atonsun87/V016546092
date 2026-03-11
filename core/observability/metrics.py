"""Lightweight observability and metrics for SELF-OS.

This module provides a zero-dependency metrics collector that records
key system health indicators in-process.  It is intentionally simple:
no external push, no wire protocol — just an in-memory store with a
snapshot API.

For production observability (Prometheus, OpenTelemetry) this module is
intended as the *collection point* that a future exporter adapter can
read from.

Usage::

    collector = MetricsCollector.get_instance()
    collector.increment("messages_processed")
    collector.record_latency("retrieval_ms", 42.0)

    snapshot = collector.snapshot()
    print(snapshot.to_dict())
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of latency samples to keep per metric (ring buffer)
_MAX_LATENCY_SAMPLES = 500


# ---------------------------------------------------------------------------
# Snapshot types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LatencySummary:
    """Statistical summary for a named latency metric."""

    name: str
    count: int
    mean_ms: float
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float


@dataclass(slots=True)
class SystemSnapshot:
    """Point-in-time view of key system metrics.

    Attributes
    ----------
    captured_at:
        ISO-8601 UTC timestamp when the snapshot was taken.
    counters:
        Dict of named integer counters (e.g. ``messages_processed``).
    latencies:
        Dict of :class:`LatencySummary` objects per metric name.
    gauges:
        Dict of named float gauges (e.g. ``active_users``).
    """

    captured_at: str
    counters: dict[str, int]
    latencies: dict[str, LatencySummary]
    gauges: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "captured_at": self.captured_at,
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "latencies": {
                name: {
                    "count": s.count,
                    "mean_ms": round(s.mean_ms, 3),
                    "min_ms": round(s.min_ms, 3),
                    "max_ms": round(s.max_ms, 3),
                    "p50_ms": round(s.p50_ms, 3),
                    "p95_ms": round(s.p95_ms, 3),
                }
                for name, s in self.latencies.items()
            },
        }


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Thread-safe in-process metrics collector.

    Maintains three types of metrics:

    * **Counters** — monotonically increasing integers.
    * **Latencies** — ring-buffers of float values (milliseconds) with
      percentile summaries computed on demand.
    * **Gauges** — point-in-time float values that can go up or down.

    The collector follows a singleton pattern via :meth:`get_instance` so
    that all subsystems share the same state.  Tests can create isolated
    instances with ``MetricsCollector()``.
    """

    _instance: MetricsCollector | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=_MAX_LATENCY_SAMPLES)
        )
        self._gauges: dict[str, float] = {}
        self._started_at = datetime.now(UTC).isoformat()

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> MetricsCollector:
        """Return the process-global :class:`MetricsCollector` instance."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful in tests)."""
        with cls._instance_lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    def increment(self, name: str, amount: int = 1) -> None:
        """Increment counter *name* by *amount*."""
        with self._lock:
            self._counters[name] += amount

    def get_counter(self, name: str) -> int:
        """Return the current value of counter *name*."""
        with self._lock:
            return self._counters[name]

    # ------------------------------------------------------------------
    # Latencies
    # ------------------------------------------------------------------

    def record_latency(self, name: str, value_ms: float) -> None:
        """Record a latency sample in milliseconds for metric *name*."""
        with self._lock:
            self._latencies[name].append(value_ms)

    def latency_summary(self, name: str) -> LatencySummary | None:
        """Return a :class:`LatencySummary` for metric *name* or ``None``."""
        with self._lock:
            samples = list(self._latencies.get(name, []))
        if not samples:
            return None
        return _compute_summary(name, samples)

    # ------------------------------------------------------------------
    # Gauges
    # ------------------------------------------------------------------

    def set_gauge(self, name: str, value: float) -> None:
        """Set gauge *name* to *value*."""
        with self._lock:
            self._gauges[name] = value

    def get_gauge(self, name: str) -> float | None:
        """Return the current value of gauge *name*, or ``None``."""
        with self._lock:
            return self._gauges.get(name)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> SystemSnapshot:
        """Capture and return a point-in-time :class:`SystemSnapshot`."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            latency_data = {k: list(v) for k, v in self._latencies.items()}

        latencies = {
            name: _compute_summary(name, samples)
            for name, samples in latency_data.items()
            if samples
        }

        return SystemSnapshot(
            captured_at=datetime.now(UTC).isoformat(),
            counters=counters,
            latencies=latencies,
            gauges=gauges,
        )

    # ------------------------------------------------------------------
    # Convenience: timed context manager
    # ------------------------------------------------------------------

    def timed(self, name: str) -> _Timer:
        """Return a context manager that records elapsed time in ms.

        Usage::

            with collector.timed("pipeline_ms"):
                await pipeline.run(message)
        """
        return _Timer(self, name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Timer:
    """Context manager that records elapsed milliseconds to a collector."""

    def __init__(self, collector: MetricsCollector, name: str) -> None:
        self._collector = collector
        self._name = name
        self._start: float | None = None

    def __enter__(self) -> _Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        if self._start is not None:
            elapsed_ms = (time.perf_counter() - self._start) * 1000.0
            self._collector.record_latency(self._name, elapsed_ms)


def _compute_summary(name: str, samples: list[float]) -> LatencySummary:
    sorted_samples = sorted(samples)
    n = len(sorted_samples)

    def percentile(p: float) -> float:
        if n == 0:
            return 0.0
        idx = max(0, min(n - 1, int(p / 100.0 * n)))
        return sorted_samples[idx]

    return LatencySummary(
        name=name,
        count=n,
        mean_ms=sum(sorted_samples) / n if n else 0.0,
        min_ms=sorted_samples[0] if n else 0.0,
        max_ms=sorted_samples[-1] if n else 0.0,
        p50_ms=percentile(50),
        p95_ms=percentile(95),
    )
