"""
Unit tests for app/ingestion/collector.py's Prometheus text parser and
metric-reduction logic - run against realistic exposition-format text
(the same shape our own services actually emit).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

from app.ingestion.collector import compute_service_metrics, parse_prometheus_text  # noqa: E402

SAMPLE_METRICS_TEXT = """
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{service="auth-service",endpoint="/login",method="POST",status="200"} 8.0
http_requests_total{service="auth-service",endpoint="/login",method="POST",status="500"} 2.0
# HELP http_request_duration_seconds HTTP request latency in seconds
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{service="auth-service",endpoint="/login",le="0.1"} 5.0
http_request_duration_seconds_bucket{service="auth-service",endpoint="/login",le="0.5"} 8.0
http_request_duration_seconds_bucket{service="auth-service",endpoint="/login",le="1.0"} 9.0
http_request_duration_seconds_bucket{service="auth-service",endpoint="/login",le="+Inf"} 10.0
"""


def test_parse_prometheus_text_extracts_counter_samples():
    samples = parse_prometheus_text(SAMPLE_METRICS_TEXT)
    counter_samples = [s for s in samples if s["name"] == "http_requests_total"]
    assert len(counter_samples) == 2
    assert counter_samples[0]["labels"]["status"] == "200"
    assert counter_samples[0]["value"] == 8.0


def test_parse_prometheus_text_ignores_comments():
    samples = parse_prometheus_text(SAMPLE_METRICS_TEXT)
    assert all(not s["name"].startswith("#") for s in samples)


def test_compute_service_metrics_error_rate():
    samples = parse_prometheus_text(SAMPLE_METRICS_TEXT)
    metrics = compute_service_metrics(samples)
    # 2 error (500) out of 10 total = 0.2
    assert abs(metrics["error_rate"] - 0.2) < 1e-9


def test_compute_service_metrics_latency_p95_from_buckets():
    samples = parse_prometheus_text(SAMPLE_METRICS_TEXT)
    metrics = compute_service_metrics(samples)
    # 95% of 10 = 9.5 -> first bucket whose cumulative count >= 9.5 is +Inf (10.0)
    assert metrics["latency_p95"] == float("inf")


def test_compute_service_metrics_empty_input_is_safe():
    metrics = compute_service_metrics([])
    assert metrics["error_rate"] == 0.0
    assert metrics["latency_p95"] == 0.0
