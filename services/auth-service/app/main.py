"""
Auth Service
------------
A small, real, independently-runnable FastAPI service that simulates the
authentication layer of an IT ops estate.

It exposes:
  - Real business endpoints (/login, /users)
  - A real /health endpoint (used by uptime checks)
  - A real /metrics endpoint in Prometheus exposition format (via
    prometheus_client), so Prometheus can actually scrape it.
  - Admin fault-injection endpoints used ONLY during deliberate failure
    drills (see docs/failure_injection.md). These are not fake data
    generators - they change real internal state that changes real
    request behaviour, latency and error rates, which Prometheus then
    observes for real.

Run standalone:
    uvicorn app.main:app --reload --port 8001
"""
import hmac
import os
import random
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel

from app.applog import get_logs_since, log_event

SERVICE_NAME = "auth-service"

# ---------------------------------------------------------------------------
# SECURITY (audit finding, fixed): the /admin/* fault-injection endpoints
# below can force a service into a down/error state. Before this fix they
# had NO authentication at all - anyone who could reach the port could take
# a "monitored production service" offline on demand. FAULT_INJECTION_TOKEN
# is a shared secret (set per-deployment via env var, see .env.example);
# every /admin/* call must present it in the X-Admin-Token header.
# ---------------------------------------------------------------------------
FAULT_INJECTION_TOKEN = os.getenv("FAULT_INJECTION_TOKEN", "dev-admin-token-change-me")


def require_admin_token(x_admin_token: str | None = Header(default=None)):
    if not x_admin_token or not hmac.compare_digest(x_admin_token, FAULT_INJECTION_TOKEN):
        raise HTTPException(status_code=401, detail="missing or invalid X-Admin-Token")

# ---------------------------------------------------------------------------
# Prometheus metrics (real client library, real counters/histograms).
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "endpoint", "method", "status"],
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["service", "endpoint"],
)
LOGIN_FAILURES = Counter(
    "auth_login_failures_total",
    "Total failed login attempts",
    ["service"],
)

# ---------------------------------------------------------------------------
# In-memory "fault state" - flipped by the admin endpoints below. This is the
# mechanism the project's failure-injection drills use instead of faking
# metrics: we change real behaviour, and the real metrics reflect it.
# ---------------------------------------------------------------------------
class FaultState:
    def __init__(self):
        self.extra_latency_ms: int = 0
        self.force_error_rate: float = 0.0  # 0.0 - 1.0
        self.simulate_down: bool = False


fault_state = FaultState()

USERS_DB = {
    "admin": {"password": "admin123", "role": "admin"},
    "operator": {"password": "operator123", "role": "operator"},
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Auth Service", lifespan=lifespan)


class LoginRequest(BaseModel):
    username: str
    password: str


def _observe(endpoint: str, method: str, status: int, duration: float):
    REQUEST_COUNT.labels(SERVICE_NAME, endpoint, method, str(status)).inc()
    REQUEST_LATENCY.labels(SERVICE_NAME, endpoint).observe(duration)


@app.middleware("http")
async def fault_injection_and_metrics_middleware(request, call_next):
    """
    Applies injected latency / forced errors BEFORE the real handler runs,
    then records real Prometheus metrics for every request, including the
    ones affected by fault injection. This is what lets the RCA engine
    later correlate 'latency spiked at T' with 'incident opened at T'.
    """
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
    return {"service": SERVICE_NAME, "status": "healthy"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/logs")
def get_logs(since_seq: int = 0, limit: int = 200):
    """Real structured application log events (Priority 2), pulled by the
    central platform's collector on every poll cycle. Not admin-token-
    gated, same as /health and /metrics - the content is non-sensitive
    demo telemetry, not a business secret."""
    limit = min(max(limit, 1), 200)
    return get_logs_since(since_seq, limit)


@app.post("/login")
def login(payload: LoginRequest):
    log_event("INFO", "Login request received", username=payload.username)
    user = USERS_DB.get(payload.username)
    if not user or user["password"] != payload.password:
        LOGIN_FAILURES.labels(SERVICE_NAME).inc()
        log_event("WARNING", "Login failed - invalid credentials", username=payload.username)
        raise HTTPException(status_code=401, detail="invalid credentials")
    log_event("INFO", "Login successful", username=payload.username, role=user["role"])
    return {"token": f"fake-jwt-for-{payload.username}", "role": user["role"]}


@app.get("/users")
def list_users():
    return {"users": list(USERS_DB.keys())}


# ---------------------------------------------------------------------------
# Admin / fault-injection endpoints. Used deliberately during failure drills.
# ---------------------------------------------------------------------------
@app.post("/admin/inject-latency", dependencies=[Depends(require_admin_token)])
def inject_latency(ms: int):
    fault_state.extra_latency_ms = max(0, ms)
    if fault_state.extra_latency_ms > 0:
        log_event("WARNING", f"High latency injected: +{fault_state.extra_latency_ms}ms")
    else:
        log_event("INFO", "Latency injection cleared")
    return {"extra_latency_ms": fault_state.extra_latency_ms}


@app.post("/admin/inject-errors", dependencies=[Depends(require_admin_token)])
def inject_errors(rate: float):
    fault_state.force_error_rate = min(max(rate, 0.0), 1.0)
    if fault_state.force_error_rate > 0:
        log_event("WARNING", f"Error injection enabled: rate={fault_state.force_error_rate:.2f}")
    else:
        log_event("INFO", "Error injection cleared")
    return {"force_error_rate": fault_state.force_error_rate}


@app.post("/admin/simulate-down", dependencies=[Depends(require_admin_token)])
def simulate_down(down: bool = True):
    fault_state.simulate_down = down
    log_event("ERROR" if down else "INFO", f"Service simulated {'DOWN' if down else 'UP (fault cleared)'} (fault injection)")
    return {"simulate_down": fault_state.simulate_down}


@app.post("/admin/reset", dependencies=[Depends(require_admin_token)])
def reset_faults():
    fault_state.extra_latency_ms = 0
    fault_state.force_error_rate = 0.0
    fault_state.simulate_down = False
    log_event("INFO", "All faults reset")
    return {"status": "reset"}
