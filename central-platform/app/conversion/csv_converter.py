"""
Legacy Incident CSV Conversion
--------------------------------
Simulates the one real "data conversion" task the spec calls for: a legacy
ticketing export (CSV) needs to be validated, cleaned, deduplicated, and
loaded into the incidents table, with a real conversion log describing
what happened to every row (accepted / rejected / deduped) - this is the
kind of task described in JD bullet 2 ("data conversions using standard
tools and established processes").

This module is pure logic + stdlib (csv, hashlib) so it's independently
unit-testable and runnable without the DB or FastAPI (see
tests/unit/test_csv_converter.py and the __main__ block at the bottom for
a real, runnable demo against data/legacy_csv/sample_incidents.csv).
"""
import csv
import hashlib
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import List

VALID_SEVERITIES = {"critical", "high", "medium", "low"}
REQUIRED_COLUMNS = {"service", "title", "severity", "opened_at"}


@dataclass
class ConversionRowResult:
    row_number: int
    status: str  # "accepted" | "rejected" | "deduped"
    reason: str = ""
    normalized: dict | None = None


@dataclass
class ConversionReport:
    total_rows: int = 0
    accepted: int = 0
    rejected: int = 0
    deduped: int = 0
    rows: List[ConversionRowResult] = field(default_factory=list)
    accepted_records: List[dict] = field(default_factory=list)

    def as_log_text(self) -> str:
        lines = [
            f"CSV Conversion Report - {datetime.utcnow().isoformat()}Z",
            f"Total rows processed: {self.total_rows}",
            f"Accepted: {self.accepted} | Rejected: {self.rejected} | Deduped: {self.deduped}",
            "-" * 60,
        ]
        for r in self.rows:
            lines.append(f"row {r.row_number}: {r.status.upper()}"
                         + (f" - {r.reason}" if r.reason else ""))
        return "\n".join(lines)


def _row_hash(row: dict) -> str:
    """Dedup key: same service + title + opened_at is treated as the same
    legacy ticket, regardless of column ordering/whitespace differences."""
    key = f"{row['service'].strip().lower()}|{row['title'].strip().lower()}|{row['opened_at'].strip()}"
    return hashlib.sha256(key.encode()).hexdigest()


def _validate_row(row: dict) -> str | None:
    """Returns an error reason string, or None if the row is valid."""
    missing = REQUIRED_COLUMNS - set(k for k, v in row.items() if v and v.strip())
    if missing:
        return f"missing required field(s): {', '.join(sorted(missing))}"

    severity = row["severity"].strip().lower()
    if severity not in VALID_SEVERITIES:
        return f"invalid severity '{row['severity']}' (must be one of {sorted(VALID_SEVERITIES)})"

    try:
        datetime.fromisoformat(row["opened_at"].strip())
    except ValueError:
        return f"unparsable opened_at timestamp '{row['opened_at']}' (expected ISO 8601)"

    return None


def convert(csv_text: str) -> ConversionReport:
    report = ConversionReport()
    seen_hashes = set()

    reader = csv.DictReader(io.StringIO(csv_text))
    for i, raw_row in enumerate(reader, start=1):
        report.total_rows += 1

        error = _validate_row(raw_row)
        if error:
            report.rejected += 1
            report.rows.append(ConversionRowResult(i, "rejected", error))
            continue

        h = _row_hash(raw_row)
        if h in seen_hashes:
            report.deduped += 1
            report.rows.append(ConversionRowResult(i, "deduped", "duplicate of an earlier row"))
            continue
        seen_hashes.add(h)

        normalized = {
            "service": raw_row["service"].strip(),
            "title": raw_row["title"].strip(),
            "severity": raw_row["severity"].strip().lower(),
            "opened_at": datetime.fromisoformat(raw_row["opened_at"].strip()),
        }
        report.accepted += 1
        report.accepted_records.append(normalized)
        report.rows.append(ConversionRowResult(i, "accepted", normalized=normalized))

    return report


if __name__ == "__main__":
    # Real, runnable demo: python -m app.conversion.csv_converter
    from pathlib import Path

    sample_path = Path(__file__).resolve().parents[3] / "data" / "legacy_csv" / "sample_incidents.csv"
    text = sample_path.read_text()
    result = convert(text)
    print(result.as_log_text())

    log_path = Path(__file__).resolve().parents[3] / "data" / "conversion_logs" / "conversion_log.txt"
    log_path.write_text(result.as_log_text())
    print(f"\nLog written to {log_path}")
