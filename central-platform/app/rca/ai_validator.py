"""
AI Explanation Validator (Priority 5).

WHAT THIS IS: a small, deterministic, fully-explainable set of checks run
against the LLM's own explanation text, comparing it back to the
structured evidence the rule engine actually produced (app/rca/rules.py).
It is the second half of the hallucination-mitigation story - the prompt
in app/rca/llm_explainer.py asks Gemini nicely not to invent things; this
module is what actually looks at what came back.

WHAT THIS IS NOT: a semantic fact-checker, and not "hallucination-free."
Using another LLM call to grade the first LLM's output would just
inherit the same grounding problem one level up. This checks for the
specific, common, most-misleading failure modes of an RCA explainer:
  1. Naming a specific known metric that wasn't part of the evidence.
  2. Stating a specific number that doesn't appear anywhere in the
     evidence it was given.
  3. Asserting a confident cause when the rule engine reported
     "undetermined."
A validator that tried to catch every conceivable hallucination would be
either so strict it flags normal paraphrasing, or so complex it becomes
its own unverified black box - neither is appropriate here. This is
intentionally conservative and its limits are documented, not hidden.
"""
import re
from dataclasses import dataclass
from typing import Iterable

from app.rca.rules import RCAResult

KNOWN_METRIC_NAMES = ("error_rate", "latency_p95", "availability")

_NUMBER_RE = re.compile(r"\d+\.\d+|\d+%")


@dataclass
class ValidationResult:
    status: str   # "passed" | "flagged" | "unavailable"
    reason: str


def _mentions_metric(text: str, metric: str) -> bool:
    return metric in text or metric.replace("_", " ") in text.replace("_", " ")


def _numbers_in(*texts: Iterable[str]) -> set:
    joined = " ".join(t for t in texts if t)
    return set(_NUMBER_RE.findall(joined))


def validate_explanation(explanation: str, rca_result: RCAResult, used_llm: bool) -> ValidationResult:
    """
    Returns PASSED / FLAGGED / UNAVAILABLE. Called once per RCA run (see
    routers/incidents.py::run_rca), regardless of whether Gemini actually
    produced the explanation or the system fell back to a rule-summary
    string - the UNAVAILABLE case exists specifically so a human never
    confuses "AI explanation wasn't validated" with "AI explanation was
    checked and is fine."
    """
    if not used_llm:
        return ValidationResult("unavailable", "no AI explanation was generated (rule-based summary only) - nothing to validate")

    if not explanation or not explanation.strip():
        return ValidationResult("flagged", "AI explanation was empty")

    text = explanation.lower()
    evidence_text = " ".join(e.description.lower() for e in rca_result.evidence)
    category_text = rca_result.root_cause_category.lower().replace("_", " ")

    # Check 1: a specific known metric name mentioned in the explanation
    # that never appeared in the evidence the model was actually given.
    for metric in KNOWN_METRIC_NAMES:
        if _mentions_metric(text, metric) and not _mentions_metric(evidence_text + " " + category_text, metric):
            return ValidationResult(
                "flagged",
                f"explanation references metric '{metric}', which is not present in the rule engine's evidence",
            )

    # Check 2: a specific numeric value not echoed anywhere in the
    # evidence/summary it was given (a very common hallucination shape:
    # inventing a plausible-looking percentage or duration).
    numbers_in_text = _numbers_in(text)
    numbers_in_evidence = _numbers_in(evidence_text, rca_result.summary.lower())
    unsupported = numbers_in_text - numbers_in_evidence
    if unsupported:
        return ValidationResult(
            "flagged",
            f"explanation contains numeric value(s) {sorted(unsupported)} not found in the supplied evidence",
        )

    # Check 3: undetermined must stay uncertain, not be asserted as a firm cause.
    if rca_result.root_cause_category == "undetermined":
        overconfident_phrases = ("definitely", "certainly", "confirmed cause", "root cause is", "clearly caused by")
        if any(p in text for p in overconfident_phrases):
            return ValidationResult(
                "flagged",
                "root cause is undetermined, but the explanation asserts a confident cause",
            )

    return ValidationResult("passed", "explanation's factual references are consistent with the supplied evidence")
