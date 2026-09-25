"""Full traces for the last 200 requests."""

import threading
import time
import uuid
from collections import OrderedDict

from app.models import StageTiming, Trace

_WINDOW = 200

# trace_id -> trace dict, oldest evicted first. Debugging aid and the console's detail view.
_traces: OrderedDict[str, dict] = OrderedDict()
_lock = threading.Lock()


def new_trace() -> Trace:
    """A trace with a fresh id, ready for the pipeline to fill in as stages complete."""
    return Trace(trace_id=f"t_{uuid.uuid4().hex[:10]}")


def stage(trace: Trace, name: str, started_at: float, summary: str = "") -> StageTiming:
    """Record one stage's duration against a monotonic start time.

    `summary` is not stored: StageTiming has no field for it and models.py is frozen. The console
    gets each stage's summary from its SSE event instead.
    """
    timing = StageTiming(stage=name, ms=round((time.perf_counter() - started_at) * 1000, 1))
    trace.timings.append(timing)
    return timing


def put(trace: Trace, extra: dict | None = None) -> None:
    """Store a finished trace, evicting the oldest once the window is full."""
    payload = trace.model_dump()
    payload.update(extra or {})
    payload["at"] = time.time()
    with _lock:
        _traces[trace.trace_id] = payload
        while len(_traces) > _WINDOW:
            _traces.popitem(last=False)


def get(trace_id: str) -> dict | None:
    """One stored trace, or None once it has fallen out of the window."""
    with _lock:
        return _traces.get(trace_id)


def recent(limit: int = 20) -> list[dict]:
    """Newest first: trace id, total time and cache tier, for the console's list view."""
    with _lock:
        traces = list(_traces.values())[-limit:]
    return [
        {
            "trace_id": t["trace_id"],
            "at": t["at"],
            "cache_tier": t.get("cache_tier"),
            "ms": round(sum(s["ms"] for s in t.get("timings", [])), 1),
        }
        for t in reversed(traces)
    ]


def clear() -> None:
    with _lock:
        _traces.clear()
