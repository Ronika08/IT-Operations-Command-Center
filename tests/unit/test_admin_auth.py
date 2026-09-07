"""
Regression tests for a real security fix found during the audit:
the /admin/* fault-injection endpoints on all 4 backend services had NO
authentication - any caller who could reach the port could force a
"monitored production service" into a down/error state. Fixed by
requiring an X-Admin-Token header matching FAULT_INJECTION_TOKEN.

Also regression-tests the API-design fix on db-proxy-service's
POST /records, which used to accept an unvalidated raw query parameter
and now takes a real Pydantic JSON body.

Each service is its own standalone app (own requirements.txt, own
`app.main` module path), so this file loads each one under a distinct
module name via importlib to avoid Python module-cache collisions
between the 4 identically-named `app.main` modules.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICES_ROOT = Path(__file__).resolve().parents[2] / "services"
TEST_TOKEN = "test-admin-token-for-pytest"

os.environ["FAULT_INJECTION_TOKEN"] = TEST_TOKEN


def _load_service_app(service_dir_name: str, unique_module_name: str):
    """Import services/<name>/app/main.py under a unique module name so
    the 4 services (all of which define modules named `app` / `app.main`)
    don't clobber each other in sys.modules.

    NOTE: all 4 services independently define Prometheus counters with
    the same names (http_requests_total, http_request_duration_seconds,
    ...) because in real deployments each runs in its own process with
    its own prometheus_client global registry. Loading more than one
    into this single test process would collide on those names, so we
    clear the shared registry before each load - a test-harness-only
    accommodation for the fact that this test process is emulating 4
    separate service processes.

    UPDATED (Priority 2 - structured log ingestion): main.py now does
    `from app.applog import ...`, a real sibling-module import within
    each service's own `app` package (previously every service's main.py
    only imported third-party libraries, so this trick didn't need to
    handle same-named sibling packages). To keep the 4 services' `app`
    packages from merging into one Python namespace package, or from
    sharing a single `applog` module's in-memory log buffer across
    services, this now: (1) drops any previously-cached `app`/`app.applog`
    modules, (2) temporarily puts ONLY this service's directory on
    sys.path for the duration of the import, (3) removes it again
    immediately after. `from app.applog import log_event` binds a direct
    reference to that specific module's functions at import time, so each
    service's loaded main module keeps its own isolated log buffer even
    after the next service's load reuses the generic `app.applog` name
    for itself.
    """
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


@pytest.fixture(scope="module")
def auth_client():
    module = _load_service_app("auth-service", "auth_service_main")
    return TestClient(module.app)


@pytest.fixture(scope="module")
def orders_client():
    module = _load_service_app("orders-service", "orders_service_main")
    return TestClient(module.app)


@pytest.fixture(scope="module")
def payments_client():
    module = _load_service_app("payments-service", "payments_service_main")
    return TestClient(module.app)


@pytest.fixture(scope="module")
def dbproxy_client():
    module = _load_service_app("db-proxy-service", "dbproxy_service_main")
    return TestClient(module.app)


ADMIN_ENDPOINTS = [
    ("auth_client", "/admin/inject-latency", {"ms": 0}),
    ("orders_client", "/admin/inject-latency", {"ms": 0}),
    ("payments_client", "/admin/inject-latency", {"ms": 0}),
    ("dbproxy_client", "/admin/inject-latency", {"ms": 0}),
]


@pytest.mark.parametrize("client_fixture,path,params", ADMIN_ENDPOINTS)
def test_admin_endpoint_rejects_missing_token(client_fixture, path, params, request):
    client = request.getfixturevalue(client_fixture)
    resp = client.post(path, params=params)
    assert resp.status_code == 401


@pytest.mark.parametrize("client_fixture,path,params", ADMIN_ENDPOINTS)
def test_admin_endpoint_rejects_wrong_token(client_fixture, path, params, request):
    client = request.getfixturevalue(client_fixture)
    resp = client.post(path, params=params, headers={"X-Admin-Token": "wrong-token"})
    assert resp.status_code == 401


@pytest.mark.parametrize("client_fixture,path,params", ADMIN_ENDPOINTS)
def test_admin_endpoint_accepts_correct_token(client_fixture, path, params, request):
    client = request.getfixturevalue(client_fixture)
    resp = client.post(path, params=params, headers={"X-Admin-Token": TEST_TOKEN})
    assert resp.status_code == 200


def test_payments_simulate_db_timeout_requires_token(payments_client):
    resp = payments_client.post("/admin/simulate-db-timeout", params={"on": "false"})
    assert resp.status_code == 401
    resp = payments_client.post(
        "/admin/simulate-db-timeout", params={"on": "false"},
        headers={"X-Admin-Token": TEST_TOKEN},
    )
    assert resp.status_code == 200


def test_dbproxy_simulate_connection_exhaustion_requires_token(dbproxy_client):
    resp = dbproxy_client.post("/admin/simulate-connection-exhaustion", params={"on": "false"})
    assert resp.status_code == 401
    resp = dbproxy_client.post(
        "/admin/simulate-connection-exhaustion", params={"on": "false"},
        headers={"X-Admin-Token": TEST_TOKEN},
    )
    assert resp.status_code == 200


def test_business_endpoints_are_not_gated_by_admin_token(auth_client):
    """Only /admin/* should require the token - regular business
    endpoints (login, health, metrics) must keep working unauthenticated
    exactly as before, since gating them was never part of this fix."""
    resp = auth_client.get("/health")
    assert resp.status_code == 200
    resp = auth_client.post("/login", json={"username": "admin", "password": "admin123"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# db-proxy /records API-design fix: real Pydantic body instead of a raw,
# unvalidated query parameter.
# ---------------------------------------------------------------------------
def test_records_accepts_valid_json_body(dbproxy_client):
    resp = dbproxy_client.post("/records", json={"payload": "audit-test-record"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["payload"] == "audit-test-record"
    assert isinstance(body["id"], int)


def test_records_rejects_empty_payload(dbproxy_client):
    resp = dbproxy_client.post("/records", json={"payload": ""})
    assert resp.status_code == 422


def test_records_rejects_missing_payload_field(dbproxy_client):
    resp = dbproxy_client.post("/records", json={})
    assert resp.status_code == 422


def test_records_rejects_oversized_payload(dbproxy_client):
    resp = dbproxy_client.post("/records", json={"payload": "x" * 5000})
    assert resp.status_code == 422
