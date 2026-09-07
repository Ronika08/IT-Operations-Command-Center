"""
Metrics Collector & Incident Detector
----------------------------------------
Polls each monitored service's REAL /health and /metrics endpoints
(Prometheus exposition format, parsed here without a heavy client library
since we only need a handful of series), stores samples, and opens a real
incident row when a real threshold is breached.

This is the piece that makes "monitors real services" true end-to-end:
  service's own counters -> this collector -> metrics table -> incident
  table -> RCA engine (app/rca) reads the same rows back out.

Kept deliberately simple (poll loop, not a message queue) because the
spec caps scope at 3-4 services - a queue-based pipeline would be over-
engineering for this scale and is explicitly called out as something to
discuss in system-design terms rather than build.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.db import (
    ACTIVE_INCIDENT_STATUSES,
    AuditLog,
    Incident,
    IncidentStatus,
    LogEntry,
    MetricSample,
    Service,
    Severity,
)
from app.sla.engine import calculate_deadlines
from app.sla.repository import get_sla_policy_for_severity

settings = get_settings()
logger = logging.getLogger("central-platform.collector")

LOG_INGEST_LIMIT = 200  # per service, per poll cycle - see ingest_logs()

METRIC_LINE_RE = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+0-9.eE]+)')


def parse_prometheus_text(text: str) -> list[dict]:
    """Minimal Prometheus exposition format parser: enough to extract
    counter/histogram sample lines with their label sets. Not a full
    implementation (no exemplars, no NaN handling) - deliberately scoped
    to what this project's own metrics actually emit."""
    samples = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = METRIC_LINE_RE.match(line)
        if not m:
            continue
        labels = {}
        if m.group("labels"):
            for pair in m.group("labels").split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    labels[k.strip()] = v.strip().strip('"')
        samples.append({"name": m.group("name"), "labels": labels, "value": float(m.group("value"))})
    return samples


def compute_service_metrics(samples: list[dict]) -> Dict[str, float]:
    """
    Reduces raw scraped counter/histogram samples into the three signals
    the incident-detection rules care about: error_rate, latency_p95
    (approximated from histogram buckets), and availability (1.0 unless
    health check failed - set by caller).
    """
    total_requests = 0.0
    error_requests = 0.0
    bucket_counts: Dict[str, float] = defaultdict(float)

    for s in samples:
        if s["name"] == "http_requests_total":
            total_requests += s["value"]
            status = s["labels"].get("status", "")
            if status.startswith("5") or status.startswith("4"):
                error_requests += s["value"]
        if s["name"] == "http_request_duration_seconds_bucket":
            le = s["labels"].get("le")
            if le:
                bucket_counts[le] += s["value"]

    error_rate = (error_requests / total_requests) if total_requests > 0 else 0.0

    # crude p95 estimate from cumulative histogram buckets
    latency_p95 = 0.0
    if bucket_counts:
        total = bucket_counts.get("+Inf", max(bucket_counts.values(), default=0))
        if total > 0:
            target = 0.95 * total
            for le_str in sorted(bucket_counts.keys(), key=lambda x: float("inf") if x == "+Inf" else float(x)):
                if bucket_counts[le_str] >= target:
                    latency_p95 = float("inf") if le_str == "+Inf" else float(le_str)
                    break

    return {"error_rate": error_rate, "latency_p95": latency_p95}


def poll_service(db: Session, service: Service) -> dict:
    """Scrapes one service's /health and /metrics for real, stores metric
    samples, and returns the reduced metrics dict used by detection."""
    availability = 1.0
    try:
        health_resp = httpx.get(f"{service.base_url}/health", timeout=3.0)
        if health_resp.status_code != 200:
            availability = 0.0
    except httpx.RequestError:
        availability = 0.0

    metrics = {"error_rate": 0.0, "latency_p95": 0.0, "availability": availability}

    if availability > 0:
        try:
            metrics_resp = httpx.get(f"{service.base_url}/metrics", timeout=3.0)
            samples = parse_prometheus_text(metrics_resp.text)
            reduced = compute_service_metrics(samples)
            metrics.update(reduced)
        except httpx.RequestError:
            metrics["availability"] = 0.0

    now = datetime.utcnow()
    for name, value in metrics.items():
        db.add(MetricSample(service_id=service.id, metric_name=name, value=value, timestamp=now))
    db.commit()

    return metrics


def _evaluate_breach(service: Service, metrics: dict):
    """
    Pure evaluation of the three real thresholds against one metrics
    reading. Returns (severity, title, trigger_metric, trigger_value,
    trigger_threshold) for the highest-priority breach, or None if
    nothing breaches.

    Factored out of detect_and_open_incident() so the exact same
    detection logic can also answer "has this incident's condition
    cleared?" for recovery detection (Priority 3) - recovery is defined
    as "this function now returns None for the service that an active
    incident is open against," not a separate, possibly-inconsistent
    check.
    """
    if metrics["availability"] < settings.AVAILABILITY_THRESHOLD:
        return (
            Severity.CRITICAL, f"{service.name} is unavailable",
            "availability", metrics["availability"], settings.AVAILABILITY_THRESHOLD,
        )
    if metrics["error_rate"] > settings.ERROR_RATE_THRESHOLD:
        return (
            Severity.HIGH, f"{service.name} error rate elevated",
            "error_rate", metrics["error_rate"], settings.ERROR_RATE_THRESHOLD,
        )
    if metrics["latency_p95"] > settings.LATENCY_P95_THRESHOLD_SECONDS:
        return (
            Severity.MEDIUM, f"{service.name} latency degraded",
            "latency_p95", metrics["latency_p95"], settings.LATENCY_P95_THRESHOLD_SECONDS,
        )
    return None


def ingest_logs(db: Session, service: Service) -> int:
    """
    Priority 2: pulls real, service-generated structured log events from
    the service's own /logs endpoint (see services/*/app/applog.py) and
    writes them into the central `logs` table.

    Uses a durable per-service cursor (Service.last_ingested_log_seq) so
    entries are never re-ingested after a central-platform restart, and
    never missed between polls (as long as the service's bounded ring
    buffer - 500 entries - doesn't wrap between two poll cycles, a known,
    documented limitation at demo scale; see docs/architecture.md).

    Resilient by the same principle as poll_service(): a service that
    doesn't expose /logs (or is down, or on an old version) must not
    break the poll loop for the others.
    """
    try:
        resp = httpx.get(
            f"{service.base_url}/logs",
            params={"since_seq": service.last_ingested_log_seq, "limit": LOG_INGEST_LIMIT},
            timeout=3.0,
        )
        if resp.status_code != 200:
            return 0
        entries = resp.json()
    except (httpx.RequestError, ValueError):
        return 0

    if not entries:
        return 0

    max_seq = service.last_ingested_log_seq
    ingested = 0
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            db.add(LogEntry(
                service_id=service.id,
                level=str(entry.get("level", "INFO"))[:16],
                message=str(entry.get("message", ""))[:2000],
                timestamp=ts,
            ))
            max_seq = max(max_seq, int(entry["seq"]))
            ingested += 1
        except (KeyError, ValueError, TypeError):
            continue  # malformed entry from an untrusted/older service build - skip, don't crash the poll

    service.last_ingested_log_seq = max_seq
    db.commit()
    return ingested


def detect_and_open_incident(db: Session, service: Service, metrics: dict) -> Incident | None:
    """Applies the real thresholds from settings. Opens exactly one new
    incident per breach type if one isn't already (actively) open for
    this service, to avoid duplicate-incident spam on sustained
    failures. RESOLVED and RECOVERED incidents don't block a new one -
    see ACTIVE_INCIDENT_STATUSES."""
    existing_open = (
        db.query(Incident)
        .filter(Incident.service_id == service.id, Incident.status.in_(ACTIVE_INCIDENT_STATUSES))
        .first()
    )
    if existing_open:
        return None  # don't open a second incident while one is already active

    breach = _evaluate_breach(service, metrics)
    if breach is None:
        return None
    severity, title, trigger_metric, trigger_value, trigger_threshold = breach

    opened_at = datetime.utcnow()
    # PRIORITY 1 FIX: fetch the live SLA policy from Postgres for this
    # severity instead of using the hard-coded DEFAULT_SLA_MINUTES dict
    # directly - see app/sla/repository.py for the full rationale.
    sla_policy = get_sla_policy_for_severity(db, severity)
    deadlines = calculate_deadlines(opened_at, severity.value, sla_policy)

    incident = Incident(
        service_id=service.id,
        title=title,
        severity=severity,
        status=IncidentStatus.OPEN,
        opened_at=opened_at,
        response_due_at=deadlines.response_due_at,
        resolution_due_at=deadlines.resolution_due_at,
        trigger_metric=trigger_metric,
        trigger_value=trigger_value,
        trigger_threshold=trigger_threshold,
    )
    db.add(incident)
    db.add(LogEntry(
        service_id=service.id,
        level="ERROR",
        message=f"Incident auto-opened: {title} ({trigger_metric}={trigger_value:.3f}, threshold={trigger_threshold})",
        timestamp=opened_at,
    ))
    db.commit()
    db.refresh(incident)
    return incident


def check_recovery(db: Session, service: Service, metrics: dict) -> Incident | None:
    """
    Priority 3: if this service has an active (OPEN/ACKNOWLEDGED/BREACHED)
    incident and this poll's metrics no longer breach ANY of the three
    real thresholds (the same _evaluate_breach() used to detect the
    failure in the first place), mark that incident RECOVERED - a
    system-detected fact, timestamped, audited, and logged.

    Deliberately does NOT set status to RESOLVED: recovery is what the
    monitoring observed; resolution is a human decision (see
    IncidentStatus.RECOVERED's docstring). Once an incident is RECOVERED
    it is no longer in ACTIVE_INCIDENT_STATUSES, so this function will
    never re-mark the same incident recovered on a later poll - recovery
    fires exactly once per incident.
    """
    active = (
        db.query(Incident)
        .filter(Incident.service_id == service.id, Incident.status.in_(ACTIVE_INCIDENT_STATUSES))
        .first()
    )
    if active is None:
        return None

    if _evaluate_breach(service, metrics) is not None:
        return None  # still breaching (possibly a different condition now) - not recovered

    recovered_at = datetime.utcnow()
    active.recovered_at = recovered_at
    active.status = IncidentStatus.RECOVERED

    db.add(LogEntry(
        service_id=service.id,
        level="INFO",
        message=(
            f"{service.name} recovered - health/metric checks back within "
            f"thresholds (incident #{active.id})"
        ),
        timestamp=recovered_at,
    ))
    db.add(AuditLog(
        user_id=None,  # system-detected, not a human action - see docs/security.md
        action="auto_recovery_detected",
        target_type="incident",
        target_id=active.id,
        details=f"monitoring poll observed {service.name} back within all thresholds",
        timestamp=recovered_at,
    ))
    db.commit()
    db.refresh(active)
    return active


def poll_all_services(db: Session) -> list[Incident]:
    opened = []
    services = db.query(Service).filter(Service.is_active == True).all()  # noqa: E712
    for service in services:
        metrics = poll_service(db, service)
        ingest_logs(db, service)
        recovered = check_recovery(db, service, metrics)
        if recovered:
            logger.info("Incident #%s recovered for %s", recovered.id, service.name)
        incident = detect_and_open_incident(db, service, metrics)
        if incident:
            opened.append(incident)
    return opened
