"""
Unit tests for app/conversion/csv_converter.py - validation, dedup, and
row-level error reporting.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "central-platform"))

from app.conversion.csv_converter import convert  # noqa: E402

VALID_CSV = """service,title,severity,opened_at
auth-service,Login slow,high,2026-01-01T10:00:00
orders-service,Order stuck,medium,2026-01-02T11:00:00
"""

DUPLICATE_CSV = """service,title,severity,opened_at
auth-service,Login slow,high,2026-01-01T10:00:00
auth-service,Login slow,high,2026-01-01T10:00:00
"""

BAD_SEVERITY_CSV = """service,title,severity,opened_at
auth-service,Login slow,catastrophic,2026-01-01T10:00:00
"""

MISSING_FIELD_CSV = """service,title,severity,opened_at
auth-service,,high,2026-01-01T10:00:00
"""

BAD_DATE_CSV = """service,title,severity,opened_at
auth-service,Login slow,high,not-a-real-date
"""


def test_all_valid_rows_accepted():
    report = convert(VALID_CSV)
    assert report.total_rows == 2
    assert report.accepted == 2
    assert report.rejected == 0
    assert report.deduped == 0


def test_exact_duplicate_is_deduped_not_rejected():
    report = convert(DUPLICATE_CSV)
    assert report.total_rows == 2
    assert report.accepted == 1
    assert report.deduped == 1
    assert report.rejected == 0


def test_invalid_severity_rejected_with_reason():
    report = convert(BAD_SEVERITY_CSV)
    assert report.rejected == 1
    assert "invalid severity" in report.rows[0].reason


def test_missing_required_field_rejected_with_reason():
    report = convert(MISSING_FIELD_CSV)
    assert report.rejected == 1
    assert "missing required field" in report.rows[0].reason


def test_unparsable_date_rejected_with_reason():
    report = convert(BAD_DATE_CSV)
    assert report.rejected == 1
    assert "unparsable" in report.rows[0].reason


def test_accepted_records_are_normalized():
    report = convert(VALID_CSV)
    rec = report.accepted_records[0]
    assert rec["severity"] == "high"          # lowercased
    assert rec["service"] == "auth-service"   # stripped


def test_conversion_log_text_contains_summary_counts():
    report = convert(VALID_CSV)
    log_text = report.as_log_text()
    assert "Accepted: 2" in log_text
    assert "Rejected: 0" in log_text
