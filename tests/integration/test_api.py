"""
Integration tests for the central-platform FastAPI app.

Uses FastAPI's TestClient (in-process, no network hop) against the REAL
Postgres database configured via DATABASE_URL, exercising the full
router -> DB -> response cycle. Run with:

    DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/itopscc_test \
    JWT_SECRET_KEY=ci-test-secret \
    pytest tests/integration/ -v

A dedicated itopscc_test database is used so this never touches dev
data, and tables are dropped/recreated per test session for a clean,
repeatable run - this is what makes it a real integration test rather
than a mocked one.

UPDATED for Priority 4 (auth) and Priority 6 (pagination): every state-
changing endpoint now requires a bearer token with a specific role, and
GET /incidents returns {items, page, limit, total} instead of a bare
list. A `login_as(role)` helper below logs in as one of the 4 demo users
seeded by main.py::seed_reference_data and returns the Authorization
header to attach to subsequent calls.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@localhost:5432/itopscc_test",
)
os.environ.setdefault("LLM_ENABLED", "false")  # no live API calls in CI
os.environ.setdefault("JWT_SECRET_KEY", "integration-test-secret-not-for-prod")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import SessionLocal, engine, init_db  # noqa: E402
from app.models.db import Base, Service  # noqa: E402

DEMO_PASSWORDS = {
    "admin": "changeme-admin",
    "it_support": "changeme-itsupport",
    "incident_manager": "changeme-incidentmgr",
    "rca_reviewer": "changeme-rcareviewer",
}


@pytest.fixture(scope="module")
def client():
    Base.metadata.drop_all(bind=engine)
    init_db()

    db = SessionLocal()
    db.add(Service(name="auth-service", base_url="http://localhost:8001"))
    db.add(Service(name="orders-service", base_url="http://localhost:8002"))
    db.commit()
    db.close()

    # Import app AFTER env vars / DB are set up so config picks them up.
    from app.main import app

    with TestClient(app) as c:
        yield c

    Base.metadata.drop_all(bind=engine)


def login_as(client, role: str) -> dict:
    """Logs in as one of the 4 seeded demo users and returns a ready-to-use
    Authorization header dict."""
    resp = client.post("/auth/login", json={"username": role, "password": DEMO_PASSWORDS[role]})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Health + auth basics
# ---------------------------------------------------------------------------
def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_login_succeeds_with_seeded_demo_user(client):
    headers = login_as(client, "admin")
    assert "Authorization" in headers


def test_login_fails_with_wrong_password(client):
    resp = client.post("/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert resp.status_code == 401


def test_login_fails_with_unknown_username(client):
    resp = client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert resp.status_code == 401


def test_me_endpoint_returns_the_authenticated_identity(client):
    headers = login_as(client, "rca_reviewer")
    resp = client.get("/auth/me", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "rca_reviewer"
    assert body["role"] == "rca_reviewer"


# ---------------------------------------------------------------------------
# Authorization: protected endpoints without/with the right role
# ---------------------------------------------------------------------------
def test_list_incidents_without_token_is_rejected(client):
    resp = client.get("/incidents")
    assert resp.status_code == 401


def test_list_incidents_with_token_succeeds(client):
    headers = login_as(client, "it_support")
    resp = client.get("/incidents", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"items", "page", "limit", "total"}


def test_resolve_denied_for_wrong_role(client):
    """IT_SUPPORT can acknowledge but not resolve - resolving is
    INCIDENT_MANAGER/ADMIN only (see routers/incidents.py)."""
    headers = login_as(client, "it_support")
    resp = client.post("/incidents/999999/resolve", json={}, headers=headers)
    assert resp.status_code == 403


def test_sla_rule_update_denied_for_non_admin(client):
    headers = login_as(client, "incident_manager")
    resp = client.put("/sla/rules/critical", json={"response_minutes": 5, "resolution_minutes": 30}, headers=headers)
    assert resp.status_code == 403


def test_sla_rule_update_accepted_for_admin_and_changes_the_value(client):
    headers = login_as(client, "admin")
    resp = client.put("/sla/rules/low", json={"response_minutes": 7, "resolution_minutes": 77}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["response_minutes"] == 7
    assert body["resolution_minutes"] == 77

    # GET reflects the same live value - proves the table (not a cached
    # default) is what a subsequent read/incident-creation would use.
    resp2 = client.get("/sla/rules", headers=headers)
    low_rule = next(r for r in resp2.json() if r["severity"] == "low")
    assert low_rule["response_minutes"] == 7


def test_list_services(client):
    headers = login_as(client, "it_support")
    resp = client.get("/services", headers=headers)
    assert resp.status_code == 200
    names = [s["name"] for s in resp.json()]
    assert "auth-service" in names
    assert "orders-service" in names


def test_sla_rules_seeded_on_startup(client):
    headers = login_as(client, "it_support")
    resp = client.get("/sla/rules", headers=headers)
    assert resp.status_code == 200
    severities = {r["severity"] for r in resp.json()}
    assert severities == {"critical", "high", "medium", "low"}


def test_sla_summary_with_no_incidents(client):
    headers = login_as(client, "it_support")
    resp = client.get("/sla/summary", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["sla_compliance_pct"] == 100.0


# ---------------------------------------------------------------------------
# Incident lifecycle (ack -> resolve), with real authenticated users
# ---------------------------------------------------------------------------
def test_incident_lifecycle_ack_and_resolve(client):
    from datetime import datetime, timedelta

    from app.models.db import Incident, IncidentStatus, Severity

    db = SessionLocal()
    service = db.query(Service).filter(Service.name == "auth-service").first()
    opened = datetime.utcnow()
    incident = Incident(
        service_id=service.id,
        title="Test incident",
        severity=Severity.HIGH,
        status=IncidentStatus.OPEN,
        opened_at=opened,
        response_due_at=opened + timedelta(minutes=30),
        resolution_due_at=opened + timedelta(minutes=480),
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    incident_id = incident.id
    db.close()

    ack_headers = login_as(client, "it_support")
    resp = client.post(f"/incidents/{incident_id}/acknowledge", json={}, headers=ack_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"

    # Double-ack should fail
    resp = client.post(f"/incidents/{incident_id}/acknowledge", json={}, headers=ack_headers)
    assert resp.status_code == 400

    resolve_headers = login_as(client, "incident_manager")
    resp = client.post(f"/incidents/{incident_id}/resolve", json={"note": "fixed it"}, headers=resolve_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


def test_incident_not_found_returns_404(client):
    headers = login_as(client, "it_support")
    resp = client.get("/incidents/999999", headers=headers)
    assert resp.status_code == 404


def test_rca_endpoint_returns_undetermined_with_no_evidence(client):
    from datetime import datetime, timedelta

    from app.models.db import Incident, IncidentStatus, Severity

    db = SessionLocal()
    service = db.query(Service).filter(Service.name == "orders-service").first()
    opened = datetime.utcnow()
    incident = Incident(
        service_id=service.id,
        title="RCA test incident",
        severity=Severity.MEDIUM,
        status=IncidentStatus.OPEN,
        opened_at=opened,
        response_due_at=opened + timedelta(minutes=120),
        resolution_due_at=opened + timedelta(minutes=1440),
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    incident_id = incident.id
    db.close()

    headers = login_as(client, "rca_reviewer")
    resp = client.post(f"/incidents/{incident_id}/rca", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    # No metric/log rows exist for this incident window, so the rule engine
    # must honestly report undetermined rather than guessing.
    assert body["root_cause_category"] == "undetermined"
    assert body["used_llm"] is False  # LLM_ENABLED=false in this test run
    # Priority 5: no AI ran, so validation must say UNAVAILABLE, never PASSED.
    assert body["ai_validation_status"] == "unavailable"


def test_verify_recommendation_requires_rca_reviewer_role(client):
    from datetime import datetime, timedelta

    from app.models.db import Incident, IncidentStatus, Severity

    db = SessionLocal()
    service = db.query(Service).filter(Service.name == "orders-service").first()
    opened = datetime.utcnow()
    incident = Incident(
        service_id=service.id, title="Verify-test incident", severity=Severity.LOW, status=IncidentStatus.OPEN,
        opened_at=opened, response_due_at=opened + timedelta(minutes=480), resolution_due_at=opened + timedelta(minutes=4320),
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    incident_id = incident.id
    db.close()

    rca_headers = login_as(client, "it_support")
    rca_resp = client.post(f"/incidents/{incident_id}/rca", headers=rca_headers)
    rec_id = rca_resp.json()["recommendation_id"]

    wrong_role_headers = login_as(client, "it_support")
    resp = client.post(
        f"/incidents/recommendations/{rec_id}/verify",
        json={"verdict": "accepted"},
        headers=wrong_role_headers,
    )
    assert resp.status_code == 403

    reviewer_headers = login_as(client, "rca_reviewer")
    resp = client.post(
        f"/incidents/recommendations/{rec_id}/verify",
        json={"verdict": "accepted", "note": "looks right"},
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["verified_by_human"] is True


def test_csv_conversion_requires_admin_role(client):
    csv_content = (
        "service,title,severity,opened_at\n"
        "auth-service,Legacy login bug,high,2026-01-01T09:00:00\n"
        "unknown-service,Ghost incident,low,2026-01-01T10:00:00\n"
    )
    non_admin_headers = login_as(client, "it_support")
    resp = client.post(
        "/conversion/legacy-incidents",
        files={"file": ("legacy.csv", csv_content, "text/csv")},
        headers=non_admin_headers,
    )
    assert resp.status_code == 403

    admin_headers = login_as(client, "admin")
    resp = client.post(
        "/conversion/legacy-incidents",
        files={"file": ("legacy.csv", csv_content, "text/csv")},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 2
    assert body["inserted_into_db"] == 1  # unknown-service has no matching Service row
    assert body["skipped_unknown_service"] == 1


# ---------------------------------------------------------------------------
# GET /audit-logs - now expects REAL user ids (not NULL) for authenticated
# human actions, since Priority 4 removed the caller-suppliable user_id.
# ---------------------------------------------------------------------------
def test_audit_logs_returns_real_rows_with_real_user_ids(client):
    headers = login_as(client, "it_support")
    resp = client.get("/audit-logs", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)

    actions = {row["action"] for row in body}
    assert "acknowledge" in actions
    assert "resolve" in actions

    resolve_rows = [r for r in body if r["action"] == "resolve"]
    assert resolve_rows
    # This is the Priority 4 proof: user_id must be a REAL id now, not None.
    assert resolve_rows[0]["user_id"] is not None
    assert resolve_rows[0]["details"] == "fixed it"

    timestamps = [row["timestamp"] for row in body]
    assert timestamps == sorted(timestamps, reverse=True)  # descending order


def test_audit_logs_limit_parameter_is_respected(client):
    headers = login_as(client, "it_support")
    resp = client.get("/audit-logs", params={"limit": 1}, headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_audit_logs_limit_over_max_is_rejected(client):
    headers = login_as(client, "it_support")
    resp = client.get("/audit-logs", params={"limit": 201}, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Pagination (Priority 6)
# ---------------------------------------------------------------------------
def test_incidents_pagination_pages_and_total_are_consistent(client):
    headers = login_as(client, "it_support")

    from datetime import datetime, timedelta

    from app.models.db import Incident, IncidentStatus, Severity

    db = SessionLocal()
    service = db.query(Service).filter(Service.name == "auth-service").first()
    opened = datetime.utcnow()
    for i in range(5):
        db.add(Incident(
            service_id=service.id, title=f"Pagination test incident {i}", severity=Severity.LOW,
            status=IncidentStatus.OPEN, opened_at=opened,
            response_due_at=opened + timedelta(minutes=480), resolution_due_at=opened + timedelta(minutes=4320),
        ))
    db.commit()
    db.close()

    page1 = client.get("/incidents", params={"page": 1, "limit": 2}, headers=headers).json()
    page2 = client.get("/incidents", params={"page": 2, "limit": 2}, headers=headers).json()

    assert page1["page"] == 1
    assert page1["limit"] == 2
    assert len(page1["items"]) == 2
    assert page1["total"] >= 5
    assert page2["total"] == page1["total"]  # same total regardless of page

    page1_ids = {i["id"] for i in page1["items"]}
    page2_ids = {i["id"] for i in page2["items"]}
    assert page1_ids.isdisjoint(page2_ids)  # no overlap between pages


def test_incidents_pagination_limit_over_max_is_rejected(client):
    headers = login_as(client, "it_support")
    resp = client.get("/incidents", params={"limit": 500}, headers=headers)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Recovery detection (Priority 3), through the real HTTP surface
# ---------------------------------------------------------------------------
def test_incident_recovery_via_poll_now(client):
    """Full path: an incident is opened for a service whose in-DB base_url
    points nowhere reachable (so poll-now sees it as unavailable), then
    the collector's own metrics dict is used directly to simulate the
    service coming back healthy, proving check_recovery marks RECOVERED
    (not RESOLVED) via the same code path the scheduled poller uses.

    Uses a dedicated, freshly-created Service (rather than reusing
    auth-service/orders-service) so this test's incident-state
    assertions can't be affected by OPEN incidents left behind by earlier
    tests in this module-scoped fixture (e.g. the pagination test's 5
    still-open LOW incidents) - the duplicate-open guard would otherwise
    correctly, but confusingly, block a second incident on the same
    service."""
    from datetime import datetime

    from app.ingestion.collector import check_recovery, detect_and_open_incident
    from app.models.db import IncidentStatus, Service as ServiceModel

    db = SessionLocal()
    recovery_service = ServiceModel(name="recovery-test-service", base_url="http://localhost:9999")
    db.add(recovery_service)
    db.commit()
    db.refresh(recovery_service)

    down_metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 0.0}
    incident = detect_and_open_incident(db, recovery_service, down_metrics)
    assert incident is not None
    incident_id = incident.id
    service_id = recovery_service.id
    db.close()

    db2 = SessionLocal()
    service2 = db2.get(ServiceModel, service_id)
    healthy_metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": 1.0}
    recovered = check_recovery(db2, service2, healthy_metrics)
    assert recovered is not None
    assert recovered.id == incident_id
    db2.close()

    headers = login_as(client, "it_support")
    resp = client.get(f"/incidents/{incident_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "recovered"
    assert body["recovered_at"] is not None
    assert body["resolved_at"] is None  # recovered != resolved, even via the API
