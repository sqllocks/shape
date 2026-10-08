"""F-12: the leak scan read an ISO timestamp (a datetime column's min or max) as a phone number.

`shape registry ROOT commit` refused 73 of 91 daily order profiles of the demo's 90-day load
because '2026-07-08 00:01:07' matched the phone pattern. A whole value that is a valid ISO 8601
date or timestamp is not a phone number; a phone number anywhere else is still refused.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.privacy.safe_validator import SafeProfileValidator


def _findings(value: str) -> list[str]:
    doc = {"columns": {"c": {"min_value": ["string", value]}}}
    found = SafeProfileValidator().validate_data(doc).findings
    return [f.detail for f in found if f.rule == "pii-regex"]


@pytest.mark.parametrize(
    "value",
    [
        "2026-07-08 00:01:07",
        "2026-07-08 23:58:46",
        "2026-07-08T23:58:46",
        "2026-07-08T23:58:46.123456",
        "2026-07-08T23:58:46.123456789",
        "2026-07-08T23:58:46Z",
        "2026-07-08T23:58:46+02:00",
        "2026-07-08 23:58:46-0500",
        "2026-07-08 23:58",
        "2026-07-08",
    ],
)
def test_an_iso_date_or_timestamp_is_not_a_phone_number(value: str) -> None:
    assert _findings(value) == []


@pytest.mark.parametrize(
    "value",
    [
        "+1 (555) 010-0123",
        "555-010-0123",
        "call 555 010 0123 today",
        "2026-07-08 00 555 010 0123",  # a timestamp prefix does not hide a phone number
        "2026-13-45 99:99:99",  # date-shaped but not a date
        "2026-07-08 24:00:00",
    ],
)
def test_a_phone_number_is_still_found(value: str) -> None:
    found = _findings(value)
    assert found and all("phone pattern" in d for d in found)


def _day_table() -> pa.Table:
    start = dt.datetime(2026, 7, 8, 0, 1, 7)
    stamps = [start + dt.timedelta(seconds=43 * i) for i in range(2000)]
    stamps.append(dt.datetime(2026, 7, 8, 23, 58, 46))
    return pa.table(
        {
            "order_id": pa.array(range(len(stamps)), pa.int64()),
            "order_date": pa.array(stamps, pa.timestamp("us")),
        }
    )


def test_a_daily_profile_with_a_timestamp_column_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    shape.save(shape.profile(_day_table(), name="order"), "day.shape")
    args = ["registry", "reg", "commit", "order", "day.shape", "--business-date", "2026-07-08"]
    assert main([*args, "--allow-raw"]) == 0, capsys.readouterr().err
    assert "phone" not in capsys.readouterr().err


def test_a_phone_number_in_a_string_column_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = "\n".join(f"{i},note {i},2026-07-08 {i % 24:02d}:00:00" for i in range(200))
    (tmp_path / "c.csv").write_text("id,note,seen_at\n" + rows + "\n")
    assert main(["profile", "c.csv", "-o", "c.shape"]) == 0
    assert main(["profile", "safe", "c.shape", "-o", "safe.json"]) == 0
    capsys.readouterr()
    doc = json.loads((tmp_path / "safe.json").read_text())
    (table,) = doc["tables"].values()
    table["columns"]["note"]["leaked"] = "+1 (555) 010-0123"  # a phone number in a string column
    (tmp_path / "leaky.json").write_text(json.dumps(doc))
    assert main(["registry", "reg", "commit", "contacts", "safe.json"]) == 0  # timestamps pass
    capsys.readouterr()
    assert main(["registry", "reg", "commit", "contacts", "leaky.json"]) == 1
    err = capsys.readouterr().err
    assert "phone pattern" in err and "010-0123" in err
