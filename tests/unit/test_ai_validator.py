"""
Unit tests for app/rca/ai_validator.py.

No live Gemini call anywhere in this file (validate_explanation is pure
logic against a given RCAResult + explanation string), so this runs in
normal CI with no API key - see CI workflow, LLM_ENABLED=false.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

from app.rca.ai_validator import validate_explanation  # noqa: E402
from app.rca.rules import Evidence, RCAResult  # noqa: E402


def _result(category="service_outage", confidence="high", evidence=None, summary="Most likely cause: service outage."):
    return RCAResult(root_cause_category=category, confidence=confidence, evidence=evidence or [], summary=summary)


def test_ai_unavailable_is_reported_as_unavailable_not_passed():
    result = _result()
    v = validate_explanation("some text", result, used_llm=False)
    assert v.status == "unavailable"


def test_empty_explanation_is_flagged():
    result = _result(evidence=[Evidence(kind="metric", description="Availability dropped to 10%.", weight=3.0)])
    v = validate_explanation("   ", result, used_llm=True)
    assert v.status == "flagged"


def test_grounded_explanation_passes():
    result = _result(
        evidence=[Evidence(kind="metric", description="Availability dropped to 10% - service is largely unreachable.", weight=3.0)],
        summary="Most likely cause: service outage. Based on 1 correlated evidence item(s).",
    )
    explanation = (
        "The service appears to be down: availability dropped sharply, consistent with a "
        "service outage. Confidence is high given the strength of this single signal."
    )
    v = validate_explanation(explanation, result, used_llm=True)
    assert v.status == "passed"


def test_explanation_naming_an_unsupported_metric_is_flagged():
    """Evidence only concerns availability - if the explanation invents a
    claim about error_rate that was never part of the evidence, that's
    exactly the failure mode this validator exists to catch."""
    result = _result(
        evidence=[Evidence(kind="metric", description="Availability dropped to 10%.", weight=3.0)],
    )
    explanation = "This looks like an outage, and the error_rate also spiked significantly."
    v = validate_explanation(explanation, result, used_llm=True)
    assert v.status == "flagged"
    assert "error_rate" in v.reason


def test_explanation_with_unsupported_number_is_flagged():
    result = _result(
        evidence=[Evidence(kind="metric", description="Availability dropped to 10%.", weight=3.0)],
    )
    explanation = "The service was down for approximately 47.5 minutes based on the outage pattern."
    v = validate_explanation(explanation, result, used_llm=True)
    assert v.status == "flagged"


def test_undetermined_case_flagged_if_explanation_is_overconfident():
    result = _result(category="undetermined", confidence="low", evidence=[], summary="No rule matched confidently.")
    explanation = "The root cause is definitely a memory leak in the service."
    v = validate_explanation(explanation, result, used_llm=True)
    assert v.status == "flagged"


def test_undetermined_case_passes_when_explanation_stays_honest():
    result = _result(category="undetermined", confidence="low", evidence=[], summary="No rule matched confidently.")
    explanation = (
        "The available metrics and logs do not show a clear correlated signal. "
        "An engineer should investigate manually; confidence is low."
    )
    v = validate_explanation(explanation, result, used_llm=True)
    assert v.status == "passed"
