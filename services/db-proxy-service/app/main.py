"""
DB-Proxy Service
-----------------
Real FastAPI service that fronts a tiny SQLite database (real DB access,
real connection pool exhaustion is simulate-able), used to demonstrate a
"DB connection issue" failure scenario distinct from HTTP-level faults.

Run standalone:
    uvicorn app.main:app --reload --port 8004
"""
import hmac
import os
import random
import sqlite3
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from app.applog import get_logs_since, log_event

SERVICE_NAME = "db-proxy-service"

# SECURITY (audit finding, fixed): /admin/* endpoints require a shared
# secret - see auth-service/app/main.py for rationale.
FAULT_INJECTION_TOKEN = os.getenv("FAULT_INJECTION_TOKEN", "dev-admin-token-change-me")


def require_admin_token(x_admin_token: str | None = Header(default=None)):
    if not x_admin_token or not hmac.compare_digest(x_admin_token, FAULT_INJECTION_TOKEN):
        raise HTTPException(status_code=401, detail="missing or invalid X-Admin-Token")
DB_PATH = Path(__file__).parent / "proxy.db"

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["service", "endpoint", "method", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["service", "endpoint"]
)
DB_CONNECTION_ERRORS = Counter(
    "db_connection_errors_total", "Total DB connection errors", ["service"]
)

app = FastAPI(title="DB Proxy Service")


class FaultState:
    def __init__(self):
        self.extra_latency_ms: int = 0
        self.force_error_rate: float = 0.0
        self.simulate_down: bool = False
        self.simulate_connection_exhaustion: bool = False


fault_state = FaultState()


class RecordCreate(BaseModel):
    # API-DESIGN FIX (audit finding): this was previously `payload: str`
    # taken as a raw, unvalidated query parameter - inconsistent with
    # every other service's use of a real Pydantic request body, and with
    # no length/content constraint. Now a proper JSON body like the rest
    # of the platform, with an explicit size bound.
    payload: str = Field(min_length=1, max_length=4096)


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    conn.close()


_init_db()


def _observe(endpoint, method, status, duration):
    REQUEST_COUNT.labels(SERVICE_NAME, endpoint, method, str(status)).inc()
    REQUEST_LATENCY.labels(SERVICE_NAME, endpoint).observe(duration)


@app.middleware("http")
async def fault_and_metrics(request, call_next):
    start = time.perf_counter()
    if fault_state.simulate_down:
        duration = time.perf_counter() - start
        _observe(request.url.path, request.method, 503, duration)
        return Response(content="service unavailable (injected)", status_code=503)
    if fault_state.extra_latency_ms > 0:
        time.sleep(fault_state.extra_latency_ms / 1000.0)
    if fault_state.force_error_rate > 0 and random.random() < fault_state.force_error_rate:
        duration = time.perf_counter() - start
        _observe(request.url.path, request.method, 500, duration)
        log_event("ERROR", "Request failed - simulated fault (injected error)", path=request.url.path)
        return Response(content="internal error (injected)", status_code=500)
    response = await call_next(request)
    duration = time.perf_counter() - start
    _observe(request.url.path, request.method, response.status_code, duration)
    return response


@app.get("/health")
def health():
    if fault_state.simulate_down:
        raise HTTPException(status_code=503, detail="unhealthy")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=1)
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error:
        log_event("ERROR", "Database connection failure - health check failed")
        raise HTTPException(status_code=503, detail="db unreachable")
    return {"service": SERVICE_NAME, "status": "healthy"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/logs")
def get_logs(since_seq: int = 0, limit: int = 200):
    limit = min(max(limit, 1), 200)
    return get_logs_since(since_seq, limit)


@app.post("/records")
def create_record(body: RecordCreate):
    if fault_state.simulate_connection_exhaustion:
        DB_CONNECTION_ERRORS.labels(SERVICE_NAME).inc()
        log_event("WARNING", "Connection pool exhausted - request rejected", path="/records")
        raise HTTPException(status_code=503, detail="db connection pool exhausted (injected)")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("INSERT INTO records (payload) VALUES (?)", (body.payload,))
    conn.commit()
    record_id = cur.lastrowid
    conn.close()
    log_event("INFO", "Record created", record_id=record_id)
    return {"id": record_id, "payload": body.payload}


@app.get("/records")
def list_records():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, payload, created_at FROM records ORDER BY id DESC LIMIT 50").fetchall()
    conn.close()
    return [{"id": r[0], "payload": r[1], "created_at": r[2]} for r in rows]


@app.post("/admin/inject-latency", dependencies=[Depends(require_admin_token)])
def inject_latency(ms: int):
    fault_state.extra_latency_ms = max(0, ms)
    log_event("WARNING" if ms > 0 else "INFO",
              f"High latency injected: +{fault_state.extra_latency_ms}ms" if ms > 0 else "Latency injection cleared")
    return {"extra_latency_ms": fault_state.extra_latency_ms}


@app.post("/admin/inject-errors", dependencies=[Depends(require_admin_token)])
def inject_errors(rate: float):
    fault_state.force_error_rate = min(max(rate, 0.0), 1.0)
    log_event("WARNING" if fault_state.force_error_rate > 0 else "INFO",
              f"Error injection enabled: rate={fault_state.force_error_rate:.2f}" if fault_state.force_error_rate > 0 else "Error injection cleared")
    return {"force_error_rate": fault_state.force_error_rate}


@app.post("/admin/simulate-down", dependencies=[Depends(require_admin_token)])
def simulate_down(down: bool = True):
    fault_state.simulate_down = down
    log_event("ERROR" if down else "INFO", f"Service simulated {'DOWN' if down else 'UP (fault cleared)'} (fault injection)")
    return {"simulate_down": fault_state.simulate_down}


@app.post("/admin/simulate-connection-exhaustion", dependencies=[Depends(require_admin_token)])
def simulate_connection_exhaustion(on: bool = True):
    fault_state.simulate_connection_exhaustion = on
    log_event(
        "WARNING" if on else "INFO",
        "Connection pool exhaustion injection enabled" if on else "Connection pool exhaustion injection cleared",
    )
    return {"simulate_connection_exhaustion": fault_state.simulate_connection_exhaustion}


@app.post("/admin/reset", dependencies=[Depends(require_admin_token)])
def reset_faults():
    fault_state.extra_latency_ms = 0
    fault_state.force_error_rate = 0.0
    fault_state.simulate_down = False
    fault_state.simulate_connection_exhaustion = False
    log_event("INFO", "All faults reset")
    return {"status": "reset"}
