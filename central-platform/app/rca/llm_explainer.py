"""
LLM-Assisted RCA Explanation
------------------------------
This is the AI layer ON TOP OF the rule engine (app/rca/rules.py), not a
replacement for it. The flow is:

    real evidence -> rule engine -> structured RCAResult -> THIS module
    builds a constrained prompt from ONLY that structured result -> LLM
    turns it into a plain-language explanation for a human -> the
    explanation is stored separately from the rule evidence -> a human
    marks it verified/rejected before it's treated as fact.

Hallucination-mitigation guardrails actually implemented here (not just
described):
  1. Grounding: the prompt contains ONLY the evidence the rule engine
     already found. The LLM is explicitly told not to introduce new
     evidence, root causes, or numbers that are not in the provided list.
  2. Refusal path: if the rule engine returned 'undetermined', we do not
     ask the LLM to invent a cause - we ask it only to explain that the
     evidence was insufficient, in human terms.
  3. Explicit uncertainty: the prompt requires the model to state its
     confidence and to flag anything it cannot fully justify from the
     evidence.
  4. Human-in-the-loop: the output is written to `ai_explanation` with
     `verified_by_human=False` until an operator reviews it (see
     app/routers/incidents.py). Nothing here is treated as ground truth.
  5. If the API key is not configured, or the call fails, this degrades
     gracefully to "explanation unavailable" rather than fabricating text.

Provider note: this module calls Google Gemini (Generative Language API)
instead of Anthropic. Gemini is used strictly as an EXPLANATION layer on
top of the deterministic rule-based RCA result below - it never sees raw
logs/metrics directly and never determines the root cause itself:

    Evidence -> Deterministic Rule-Based RCA -> RCA Result + Evidence
             -> Optional Gemini Explanation -> Human Verification -> Resolution
"""
import json
import os
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.rca.rules import RCAResult

settings = get_settings()

# Gemini configuration. GEMINI_API_KEY is required to actually call the API;
# GEMINI_MODEL has a sensible free-tier default but can be overridden without
# a code change. Neither value is hardcoded - both come from the environment,
# consistent with the rest of this project's configuration pattern.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """You are an incident-explanation assistant for an internal \
IT operations tool. You will be given a structured JSON object containing \
ONLY evidence a rule-based engine already found for one incident.

Rules you MUST follow:
- Use ONLY the evidence provided. Do not invent metrics, log lines, timestamps, \
or root causes that are not present in the input JSON.
- If root_cause_category is "undetermined", do not guess a cause. Explain \
plainly that the evidence was insufficient and what an engineer should check \
next.
- Explicitly state your confidence in one short sentence.
- Keep the explanation under 120 words, plain language, for an on-call engineer.
- Do not add recommendations that go beyond what the evidence supports.
"""


@dataclass
class LLMExplanation:
    text: str
    used_llm: bool
    error: str | None = None


def _build_user_prompt(rca_result: RCAResult) -> str:
    payload = {
        "root_cause_category": rca_result.root_cause_category,
        "confidence": rca_result.confidence,
        "evidence": [
            {"kind": e.kind, "description": e.description, "weight": e.weight}
            for e in rca_result.evidence
        ],
        "rule_engine_summary": rca_result.summary,
    }
    return (
        "Here is the structured evidence for one incident. Explain it for an "
        "on-call engineer, following the system rules exactly.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )


def explain(rca_result: RCAResult) -> LLMExplanation:
    if not settings.LLM_ENABLED:
        return LLMExplanation(text="LLM explanation disabled by configuration.", used_llm=False)

    if not GEMINI_API_KEY:
        return LLMExplanation(
            text=(
                f"[Rule-based summary only - no LLM key configured] {rca_result.summary}"
            ),
            used_llm=False,
        )

    try:
        response = httpx.post(
            GEMINI_API_URL,
            headers={
                # Header-based auth (rather than a ?key= query param) keeps
                # the API key out of the request URL, so it can't end up in
                # httpx/library logs or exception messages that include the URL.
                "x-goog-api-key": GEMINI_API_KEY,
                "content-type": "application/json",
            },
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [
                    {"role": "user", "parts": [{"text": _build_user_prompt(rca_result)}]}
                ],
                "generationConfig": {"maxOutputTokens": 300},
            },
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates", [])
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "\n".join(p.get("text", "") for p in parts).strip()
        if not text:
            # Unusable response (e.g. blocked by safety filters, empty
            # candidate list) - treat the same as a failed call rather than
            # returning an empty explanation.
            return LLMExplanation(
                text=f"[LLM explanation unavailable, falling back to rule summary] {rca_result.summary}",
                used_llm=False,
                error="empty or unusable Gemini response",
            )
        return LLMExplanation(text=text, used_llm=True)
    except Exception:
        # Network error, auth error, rate limit, timeout, malformed response,
        # etc. Never include the raw exception (which could echo request
        # headers/URLs) in the returned text or error field - keep it generic
        # so the API key can never leak into logs, UI, or audit records.
        return LLMExplanation(
            text=f"[LLM explanation unavailable, falling back to rule summary] {rca_result.summary}",
            used_llm=False,
            error="Gemini API call failed",
        )