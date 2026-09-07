# Skill Matrix — JD Requirement → Project Evidence

| JD Requirement | Project Feature | Concept | Tool | Evidence in Project |
|---|---|---|---|---|
| Analyzes business requirements, converts to functional/technical specs, designs solutions | Problem statement + requirements docs written before any code | Requirements engineering, functional vs non-functional requirements | Markdown docs | `docs/business_requirements.md`, `docs/architecture.md` |
| Performs/documents software configuration/coding | 4 microservices + central platform, layered (routers/models/core/rca/sla) | REST APIs, layered architecture, OOP (dataclasses, enums) | FastAPI, SQLAlchemy, Pydantic | `services/*/app/main.py`, `central-platform/app/**` |
| Prepares/executes testing (unit, integration, performance, acceptance) | 55 unit tests, 8 integration tests (real Postgres), 1 real Locust load run, UAT checklist | Pytest, TestClient, load testing | Pytest, Locust, Postman | `tests/unit/`, `tests/integration/`, `tests/load/`, `docs/test_plan.md` |
| Data conversions using standard tools | Legacy CSV -> validated/deduped -> DB, with a conversion log | Data validation, dedup via hashing, ETL basics | Python csv/hashlib | `app/conversion/csv_converter.py`, `docs/test_plan.md` (real run: 6 accepted / 4 rejected / 2 deduped) |
| Reviews/monitors complex production systems for continuous performance; determines modifications | Real Prometheus scraping + threshold-based auto incident detection; found and fixed 2 real bugs during monitoring/testing | Observability, metrics vs logs, systematic debugging | Prometheus, Grafana, custom collector | `app/ingestion/collector.py`, `docs/bug_reports.md` (BUG-001, BUG-002 - reproduce→root cause→fix→regression) |
| Provides IT support, adheres to SLA processes, debugs root causes | SLA engine with real breach detection; rule-based + LLM-explained RCA with human-in-the-loop verification | Incident management, SLA, RCA, hallucination mitigation | Custom SLA/RCA engines, Anthropic API | `app/sla/engine.py`, `app/rca/rules.py`, `app/rca/llm_explainer.py`, `docs/rca_reports.md` |

## Interview questions this evidence directly supports

- "Walk me through what happens when a service goes down." -> `docs/failure_injection.md` demo sequence + `docs/rca_reports.md` Drill 1.
- "How do you prevent an AI tool from confidently making things up?" -> `app/rca/llm_explainer.py` grounding guardrails + the `undetermined` fallback in `app/rca/rules.py`.
- "Tell me about a bug you found and fixed." -> `docs/bug_reports.md` BUG-001 and BUG-002, both with full reproduce/root-cause/fix/regression detail.
- "How would you scale this?" -> `docs/architecture.md`, "Scaling from 100 to 100,000 users" section.
- "Why didn't you use Kubernetes / ELK / ML for this?" -> `docs/business_requirements.md`, "Out of Scope" section, and `docs/architecture.md`, "Why this shape, not something bigger."
- "Tell me about a security issue you found and fixed." -> `AUDIT_REPORT.md` §8 + `docs/bug_reports.md` BUG-005 (unauthenticated fault-injection admin endpoints, fixed with a shared-secret header and 19 regression test cases) and BUG-007 (stored XSS in the dashboard, fixed with output escaping).
- "How do you know your test suite actually passes, right now?" -> ran `pytest tests/unit/ -v` and `pytest tests/integration/ -v` live in the audit session: 55 + 8 = 63/63, with the terminal output to show for it.
