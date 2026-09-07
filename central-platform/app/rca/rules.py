"""
Rule-Based RCA (Root Cause Analysis) Engine
--------------------------------------------
Deliberately NOT machine learning. This is a transparent, explainable
correlation engine: it looks at real evidence (recent metric samples, log
lines, and the incident's own trigger) collected around the same time
window as the incident, and applies a small set of hand-written rules to
propose a most-likely cause category.

Why rules first, before any LLM call:
  1. Explainability - every conclusion traces back to a specific evidence
     item, which is what "determines modifications as needed" requires in
     the JD, not a black-box score.
  2. It gives the LLM a grounded, structured context to explain in plain
     language, instead of asking the LLM to guess from raw logs (which is
     where hallucination risk is highest).
  3. It works even with zero API budget/availability - the LLM step is an
     enhancement layer, not a dependency.

This module is pure logic (no DB, no network) so it is fully unit
testable - see tests/unit/test_rca_rules.py.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class Evidence:
    kind: str          # "metric" | "log" | "trigger"
    description: str
    weight: float = 1.0


@dataclass
class RCAResult:
    root_cause_category: str
    confidence: str  # "high" | "medium" | "low"
    evidence: List[Evidence] = field(default_factory=list)
    summary: str = ""


# Ordered rules: first sufficiently-matching rule wins (highest-specificity
# rules are listed first on purpose).
def _rule_downstream_db_timeout(metrics: dict, logs: List[str]) -> Evidence | None:
    if metrics.get("latency_p95", 0) > 1.5 and any("timeout" in l.lower() or "db" in l.lower() for l in logs):
        return Evidence(
            kind="metric+log",
            description=(
                f"Latency p95 spiked to {metrics.get('latency_p95'):.2f}s and logs "
                f"contain DB/timeout errors in the same window."
            ),
            weight=3.0,
        )
    return None


def _rule_error_rate_spike(metrics: dict, logs: List[str]) -> Evidence | None:
    if metrics.get("error_rate", 0) > 0.10:
        return Evidence(
            kind="metric",
            description=f"Error rate is {metrics.get('error_rate') * 100:.1f}%, above the 10% threshold.",
            weight=2.0,
        )
    return None


def _rule_service_down(metrics: dict, logs: List[str]) -> Evidence | None:
    if metrics.get("availability", 1.0) < 0.5:
        return Evidence(
            kind="metric",
            description=f"Availability dropped to {metrics.get('availability') * 100:.0f}% - service is largely unreachable.",
            weight=3.0,
        )
    return None


def _rule_connection_pool(metrics: dict, logs: List[str]) -> Evidence | None:
    if any("connection pool" in l.lower() or "connection exhausted" in l.lower() for l in logs):
        return Evidence(
            kind="log",
            description="Log lines mention DB connection pool exhaustion.",
            weight=2.5,
        )
    return None


def _rule_latency_only(metrics: dict, logs: List[str]) -> Evidence | None:
    if metrics.get("latency_p95", 0) > 1.0:
        return Evidence(
            kind="metric",
            description=f"Latency p95 is {metrics.get('latency_p95'):.2f}s, above the 1.0s threshold, with no clear log correlation.",
            weight=1.0,
        )
    return None


RULES = [
    ("downstream_dependency_failure", _rule_downstream_db_timeout),
    ("db_connection_exhaustion", _rule_connection_pool),
    ("service_outage", _rule_service_down),
    ("elevated_error_rate", _rule_error_rate_spike),
    ("performance_degradation", _rule_latency_only),
]


def analyze(metrics: dict, logs: List[str]) -> RCAResult:
    """
    metrics: dict like {"error_rate": 0.32, "latency_p95": 1.8, "availability": 0.97}
    logs: list of recent raw log message strings for the service, in the
          incident's time window.

    Returns the best-matching rule's category with the evidence that
    justified it. If NO rule matches confidently, this is reported
    honestly as 'undetermined' rather than forcing a guess - this is the
    deliberately-ambiguous case the project's failure-injection drill is
    designed to exercise, and it is exactly where a human has to make the
    judgment call.
    """
    all_evidence: List[Evidence] = []
    matched_category = None

    for category, rule_fn in RULES:
        ev = rule_fn(metrics, logs)
        if ev:
            all_evidence.append(ev)
            if matched_category is None:
                matched_category = category

    if matched_category is None:
        return RCAResult(
            root_cause_category="undetermined",
            confidence="low",
            evidence=[],
            summary=(
                "No rule matched confidently. Metrics and logs do not show a clear "
                "correlated signal - this needs human investigation rather than an "
                "automated verdict."
            ),
        )

    top_weight = max(e.weight for e in all_evidence)
    confidence = "high" if top_weight >= 3.0 else "medium" if top_weight >= 2.0 else "low"

    summary = (
        f"Most likely cause: {matched_category.replace('_', ' ')}. "
        f"Based on {len(all_evidence)} correlated evidence item(s)."
    )

    return RCAResult(
        root_cause_category=matched_category,
        confidence=confidence,
        evidence=all_evidence,
        summary=summary,
    )
