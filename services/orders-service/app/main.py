"""
Orders Service
--------------
Real FastAPI service simulating an order-management microservice.
Same fault-injection + Prometheus-metrics pattern as auth-service so all
services are comparable to the central platform's monitoring layer.

Run standalone:
    uvicorn app.main:app --reload --port 8002
"""
import hmac
import itertools
import os
import random
import time
from typing import Dict, List

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from app.applog import get_logs_since, log_event

SERVICE_NAME = "orders-service"

# SECURITY (audit finding, fixed): /admin/* endpoints require a shared
# secret so an unauthenticated caller cannot force this monitored
# service into a fault state. See auth-service/app/main.py for the
# full rationale; identical pattern applied to every service.
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
ORDERS_CREATED = Counter("orders_created_total", "Total orders created", ["service"])

app = FastAPI(title="Orders Service")

_id_counter = itertools.count(1)
ORDERS: Dict[int, dict] = {}


class FaultState:
    def __init__(self):
        self.extra_latency_ms: int = 0
        self.force_error_rate: float = 0.0
        self.simulate_down: bool = False


fault_state = FaultState()


class OrderCreate(BaseModel):
    customer: str
    item: str
    quantity: int


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
    return {"service": SERVICE_NAME, "status": "healthy"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/logs")
def get_logs(since_seq: int = 0, limit: int = 200):
    limit = min(max(limit, 1), 200)
    return get_logs_since(since_seq, limit)


@app.post("/orders")
def create_order(payload: OrderCreate):
    log_event("INFO", "Order creation request received", customer=payload.customer, item=payload.item)
    if payload.quantity <= 0:
        log_event("WARNING", "Order rejected - invalid quantity", customer=payload.customer, quantity=payload.quantity)
        raise HTTPException(status_code=422, detail="quantity must be positive")
    order_id = next(_id_counter)
    ORDERS[order_id] = payload.model_dump()
    ORDERS_CREATED.labels(SERVICE_NAME).inc()
    log_event("INFO", "Order created successfully", order_id=order_id)
    return {"order_id": order_id, **ORDERS[order_id]}


@app.get("/orders")
def list_orders() -> List[dict]:
    return [{"order_id": oid, **data} for oid, data in ORDERS.items()]


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="order not found")
    return {"order_id": order_id, **order}


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


@app.post("/admin/reset", dependencies=[Depends(require_admin_token)])
def reset_faults():
    fault_state.extra_latency_ms = 0
    fault_state.force_error_rate = 0.0
    fault_state.simulate_down = False
    log_event("INFO", "All faults reset")
    return {"status": "reset"}
