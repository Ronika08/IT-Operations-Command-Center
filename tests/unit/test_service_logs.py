"""
Unit tests for Priority 2 (real application log ingestion).

Loads each service the same isolated way tests/unit/test_admin_auth.py
does, drives a real request through it, and asserts the /logs endpoint
returns real, meaningful structured events for that action - not
fabricated data, just what actually happened during the request. Also
proves the RCA rule engine can correlate the DB-timeout and
connection-pool-exhaustion scenarios using ONLY logs produced this way
(the exact gap identified in the audit - see docs/bug_reports.md, BUG-003).
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))
os.environ["FAULT_INJECTION_TOKEN"] = "test-admin-token-for-pytest"

SERVICES_ROOT = Path(__file__).resolve().parents[2] / "services"
TEST_TOKEN = "test-admin-token-for-pytest"


def _load_service_app(service_dir_name: str, unique_module_name: str):
    """Same isolation trick as tests/unit/test_admin_auth.py::_load_service_app -
    see that function's docstring for the full rationale."""
    from prometheus_client import REGISTRY

    for collector in list(REGISTRY._collector_to_names.keys()):
        REGISTRY.unregister(collector)

    service_path = SERVICES_ROOT / service_dir_name
    main_path = service_path / "app" / "main.py"

    sys.modules.pop("app.applog", None)
    sys.modules.pop("app", None)
    sys.path.insert(0, str(service_path))
    try:
        spec = importlib.util.spec_from_file_location(unique_module_name, main_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_module_name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(service_path))
        sys.modules.pop("app.applog", None)
        sys.modules.pop("app", None)
    return module


@pytest.fixture
def payments_client():
    module = _load_service_app("payments-service", "applog_test_payments_main")
    return TestClient(module.app)


@pytest.fixture
def dbproxy_client():
    module = _load_service_app("db-proxy-service", "applog_test_dbproxy_main")
    return TestClient(module.app)


@pytest.fixture
def orders_client():
    module = _load_service_app("orders-service", "applog_test_orders_main")
    return TestClient(module.app)


def test_successful_business_operation_produces_info_log(orders_client):
    resp = orders_client.post("/orders", json={"customer": "acme", "item": "widget", "quantity": 3})
    assert resp.status_code == 200

    logs = orders_client.get("/logs").json()
    messages = [entry["message"] for entry in logs]
    assert any("Order creation request received" in m for m in messages)
    assert any("Order created successfully" in m for m in messages)
    assert all(entry["level"] in ("INFO", "WARNING", "ERROR") for entry in logs)


def test_failed_business_operation_produces_warning_log(orders_client):
    resp = orders_client.post("/orders", json={"customer": "acme", "item": "widget", "quantity": 0})
    assert resp.status_code == 422

    logs = orders_client.get("/logs").json()
    warning_messages = [e["message"] for e in logs if e["level"] == "WARNING"]
    assert any("rejected" in m.lower() for m in warning_messages)


def test_logs_endpoint_since_seq_only_returns_new_entries(orders_client):
    orders_client.post("/orders", json={"customer": "a", "item": "x", "quantity": 1})
    first_batch = orders_client.get("/logs").json()
    last_seq = first_batch[-1]["seq"]

    orders_client.post("/orders", json={"customer": "b", "item": "y", "quantity": 1})
    second_batch = orders_client.get("/logs", params={"since_seq": last_seq}).json()

    assert all(entry["seq"] > last_seq for entry in second_batch)
    assert len(second_batch) >= 1


def test_db_timeout_fault_produces_the_expected_error_log(payments_client):
    payments_client.post("/admin/simulate-db-timeout", params={"on": "true"}, headers={"X-Admin-Token": TEST_TOKEN})
    resp = payments_client.post("/payments", json={"order_id": 1, "amount": 50})
    assert resp.status_code == 504

    logs = payments_client.get("/logs").json()
    error_messages = [e["message"] for e in logs if e["level"] == "ERROR"]
    assert any("timeout" in m.lower() for m in error_messages)


def test_connection_pool_exhaustion_produces_the_expected_warning_log(dbproxy_client):
    dbproxy_client.post(
        "/admin/simulate-connection-exhaustion", params={"on": "true"}, headers={"X-Admin-Token": TEST_TOKEN},
    )
    resp = dbproxy_client.post("/records", json={"payload": "x"})
    assert resp.status_code == 503

    logs = dbproxy_client.get("/logs").json()
    messages = [e["message"].lower() for e in logs]
    assert any("connection pool" in m for m in messages)


def test_rca_engine_correlates_real_payments_service_logs_for_db_timeout(payments_client):
    """End-to-end proof of the Priority 2 goal: real service-produced logs
    (not hand-written test fixtures) are sufficient, on their own, for the
    rule engine to reach downstream_dependency_failure - the exact
    correlation the audit found the old (near-empty) logs table could not
    support."""
    from app.rca.rules import analyze

    payments_client.post("/admin/simulate-db-timeout", params={"on": "true"}, headers={"X-Admin-Token": TEST_TOKEN})
    payments_client.post("/payments", json={"order_id": 1, "amount": 50})

    real_log_messages = [e["message"] for e in payments_client.get("/logs").json()]
    metrics = {"latency_p95": 2.0, "error_rate": 0.0, "availability": 1.0}  # what the collector would have measured

    result = analyze(metrics, real_log_messages)
    assert result.root_cause_category == "downstream_dependency_failure"
    assert result.confidence == "high"


def test_rca_engine_correlates_real_dbproxy_logs_for_connection_exhaustion(dbproxy_client):
    from app.rca.rules import analyze

    dbproxy_client.post(
        "/admin/simulate-connection-exhaustion", params={"on": "true"}, headers={"X-Admin-Token": TEST_TOKEN},
    )
    dbproxy_client.post("/records", json={"payload": "x"})

    real_log_messages = [e["message"] for e in dbproxy_client.get("/logs").json()]
    metrics = {"latency_p95": 0.2, "error_rate": 0.05, "availability": 1.0}

    result = analyze(metrics, real_log_messages)
    assert result.root_cause_category == "db_connection_exhaustion"
