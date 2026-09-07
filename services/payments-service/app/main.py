"""
Payments Service
-----------------
Real FastAPI service simulating a payment-processing microservice.
This service is the one most often targeted in the failure-injection
drills because payment failures map naturally to Critical severity
incidents (see central-platform/app/sla).

Run standalone:
    uvicorn app.main:app --reload --port 8003
"""
import hmac
import itertools
import os
import random
import time
from typing import Dict

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from app.applog import get_logs_since, log_event

SERVICE_NAME = "payments-service"

# SECURITY (audit finding, fixed): /admin/* endpoints require a shared
# secret - see auth-service/app/main.py for rationale.
FAULT_INJECTION_TOKEN = os.getenv("FAULT_INJECTION_TOKEN", "dev-admin-token-change-me")


def require_admin_token(x_admin_token: str | None = Header(default=None)):
    if not x_admin_token or not hmac.compare_digest(x_admin_token, FAULT_INJECTION_TOKEN):
        raise HTTPException(status_code=401, detail="missing or invalid X-Admin-Token")

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["service", "endpoint", "method", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency in seconds", ["service", "endpoint"]
)
PAYMENTS_DECLINED = Counter("payments_declined_total", "Total declined payments", ["service"])

app = FastAPI(title="Payments Service")

_id_counter = itertools.count(1)
PAYMENTS: Dict[int, dict] = {}


class FaultState:
    def __init__(self):
        self.extra_latency_ms: int = 0
        self.force_error_rate: float = 0.0
        self.simulate_down: bool = False
        self.simulate_db_timeout: bool = False  # models a downstream DB dependency failing


fault_state = FaultState()


class PaymentCreate(BaseModel):
    order_id: int
    amount: float
    currency: str = "USD"


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
    if fault_state.simulate_db_timeout and request.url.path == "/payments":
        time.sleep(2.0)  # real, observable latency spike from a "DB dependency"
        duration = time.perf_counter() - start
        _observe(request.url.path, request.method, 504, duration)
        log_event(
            "ERROR",
            "Payment database request exceeded timeout",
            path=request.url.path,
            duration_seconds=round(duration, 3),
        )
        return Response(content="downstream db timeout (injected)", status_code=504)
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
    limit = min(max(limit, 1), 200)
    return get_logs_since(since_seq, limit)


@app.post("/payments")
def create_payment(payload: PaymentCreate):
    log_event("INFO", "Payment request received", order_id=payload.order_id, amount=payload.amount)
    if payload.amount <= 0:
        log_event("WARNING", "Payment rejected - invalid amount", order_id=payload.order_id, amount=payload.amount)
        raise HTTPException(status_code=422, detail="amount must be positive")
    # simple fraud-style rule: declines a small % of large payments
    declined = payload.amount > 10000 and random.random() < 0.3
    payment_id = next(_id_counter)
    record = {**payload.model_dump(), "status": "declined" if declined else "approved"}
    PAYMENTS[payment_id] = record
    if declined:
        PAYMENTS_DECLINED.labels(SERVICE_NAME).inc()
        log_event("WARNING", "Payment declined", payment_id=payment_id, amount=payload.amount)
    else:
        log_event("INFO", "Payment processed successfully", payment_id=payment_id)
    return {"payment_id": payment_id, **record}


@app.get("/payments/{payment_id}")
def get_payment(payment_id: int):
    payment = PAYMENTS.get(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="payment not found")
    return {"payment_id": payment_id, **payment}


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


@app.post("/admin/simulate-db-timeout", dependencies=[Depends(require_admin_token)])
def simulate_db_timeout(on: bool = True):
    fault_state.simulate_db_timeout = on
    log_event(
        "WARNING" if on else "INFO",
        "Degraded dependency: payments database responding slowly (timeout injection enabled)"
        if on else "Downstream database timeout injection cleared",
    )
    return {"simulate_db_timeout": fault_state.simulate_db_timeout}


@app.post("/admin/reset", dependencies=[Depends(require_admin_token)])
def reset_faults():
    fault_state.extra_latency_ms = 0
    fault_state.force_error_rate = 0.0
    fault_state.simulate_down = False
    fault_state.simulate_db_timeout = False
    log_event("INFO", "All faults reset")
    return {"status": "reset"}
