"""
Unit tests for app/rca/rules.py.

Includes the deliberately-ambiguous case from the spec: metrics/logs that
don't clearly match any single rule, where the engine must honestly report
'undetermined' rather than force a guess.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

from app.rca.rules import analyze  # noqa: E402


def test_downstream_db_timeout_detected():
    metrics = {"latency_p95": 2.1, "error_rate": 0.05, "availability": 1.0}
    logs = ["ERROR: downstream DB connection timeout after 2000ms"]
    result = analyze(metrics, logs)
    assert result.root_cause_category == "downstream_dependency_failure"
    assert result.confidence == "high"
    assert len(result.evidence) >= 1


def test_service_down_detected():
    metrics = {"latency_p95": 0.0, "error_rate": 0.0, "availability": 0.1}
    result = analyze(metrics, [])
    assert result.root_cause_category == "service_outage"
    assert result.confidence == "high"


def test_connection_pool_exhaustion_detected_from_logs():
    metrics = {"latency_p95": 0.3, "error_rate": 0.02, "availability": 1.0}
    logs = ["WARN: connection pool exhausted, rejecting new requests"]
    result = analyze(metrics, logs)
    assert result.root_cause_category == "db_connection_exhaustion"


def test_elevated_error_rate_detected():
    metrics = {"latency_p95": 0.2, "error_rate": 0.35, "availability": 1.0}
    result = analyze(metrics, [])
    assert result.root_cause_category == "elevated_error_rate"
    assert result.confidence == "medium"


def test_latency_only_low_confidence():
    metrics = {"latency_p95": 1.4, "error_rate": 0.01, "availability": 1.0}
    result = analyze(metrics, [])
    assert result.root_cause_category == "performance_degradation"
    assert result.confidence == "low"


def test_ambiguous_case_returns_undetermined_not_a_guess():
    """The judgment-call scenario: metrics are all within normal bounds and
    logs are unrelated noise. The engine must NOT force a category."""
    metrics = {"latency_p95": 0.4, "error_rate": 0.03, "availability": 0.995}
    logs = ["INFO: scheduled cache refresh completed", "INFO: healthcheck ok"]
    result = analyze(metrics, logs)
    assert result.root_cause_category == "undetermined"
    assert result.confidence == "low"
    assert result.evidence == []
    assert "insufficient" not in result.summary or "human investigation" in result.summary


def test_higher_specificity_rule_wins_over_generic_one():
    """When both a DB-timeout pattern AND a plain latency spike are present,
    the more specific/higher-weight rule should be reported as the primary
    category, not the generic one."""
    metrics = {"latency_p95": 1.8, "error_rate": 0.02, "availability": 1.0}
    logs = ["ERROR: db timeout while fetching order"]
    result = analyze(metrics, logs)
    assert result.root_cause_category == "downstream_dependency_failure"
