"""
Structured application log buffer (Priority 2).

Each service keeps a small in-memory, bounded ring buffer of real
structured log events - generated only at genuine runtime events (a
request received, a business outcome, a fault-injection state change),
never fabricated independently just to give the RCA engine something to
find. The central platform pulls new entries via GET /logs (see
app/main.py) on every poll cycle and writes them into the real `logs`
table in Postgres (see central-platform/app/ingestion/collector.py::
ingest_logs).

Deliberately NOT a message queue or external logging stack - this is the
smallest mechanism that fits "SERVICE -> log event -> central platform
ingestion -> Postgres -> RCA" without adding new infrastructure.

Bounded at 500 entries per service (oldest dropped first): sized for
this project's demo/interview scale, not for sustained high-volume
production logging - a known, documented limitation (see
docs/architecture.md).
"""
import itertools
from collections import deque
from datetime import datetime, timezone

_MAX_BUFFERED_LOGS = 500
_log_buffer: deque = deque(maxlen=_MAX_BUFFERED_LOGS)
_seq_counter = itertools.count(1)


def log_event(level: str, message: str, **context) -> dict:
    """Appends one structured log event to this process's buffer. Called
    only from real request handlers and fault-injection endpoints below -
    never on a timer, never independent of an actual thing that happened."""
    entry = {
        "seq": next(_seq_counter),
        "level": level.upper(),
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "context": context or None,
    }
    _log_buffer.append(entry)
    return entry


def get_logs_since(since_seq: int = 0, limit: int = 200) -> list[dict]:
    """Entries with seq > since_seq, oldest-first, capped at limit - used
    by both the /logs endpoint and directly ingestible by the central
    platform's cursor-based ingestion."""
    matching = [e for e in _log_buffer if e["seq"] > since_seq]
    return matching[:limit]
