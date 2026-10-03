"""Regressions found by the second privacy bug hunt (HUNT2-privacy)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shape
from shape.privacy.cli import main as privacy_main
from shape.privacy.safe_profile import ColumnConfig, SafeConfig, to_safe_profile


@pytest.fixture
def rare_profile(tmp_path: Path) -> Path:
    csv = tmp_path / "d.csv"
    rows = ["city"] + ["Austin"] * 60 + ["Boston"] * 30 + ["Zed", "Yak"]
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = tmp_path / "d.shape"
    shape.save(shape.profile(str(csv), name="d"), str(out))
    return out


# --- #596: a minimum cohort below 2 is not a cohort -------------------------------------------


@pytest.mark.parametrize("k", [1, 0, -3, True])
def test_a_minimum_cohort_below_two_is_refused(k):
    with pytest.raises(ValueError, match="at least 2"):
        SafeConfig(k=k)
    with pytest.raises(ValueError, match="at least 2"):
        ColumnConfig(k=k)


def test_the_smallest_cohort_is_two_and_default_still_works():
    assert SafeConfig(k=2).column_k(None) == 2
    assert ColumnConfig(k=2).k == 2
    assert SafeConfig().column_k(None) == 5
    assert SafeConfig(unsafe_full_fidelity=True).column_k("x") == 1  # the one explicit opt-out


@pytest.mark.parametrize(
    "flag", [["--k", "1"], ["--k", "0"], ["--k", "-3"], ["--column-k", "city=0"]]
)
def test_the_safe_command_refuses_a_cohort_below_two(rare_profile, tmp_path, flag, capsys):
    out = tmp_path / "s.json"
    assert privacy_main(["safe", str(rare_profile), "-o", str(out), *flag]) == 2
    assert "at least 2" in capsys.readouterr().err
    assert not out.exists()


def test_a_cohort_of_two_still_folds_one_row_categories(rare_profile, tmp_path):
    out = tmp_path / "s.json"
    assert privacy_main(["safe", str(rare_profile), "-o", str(out), "--k", "2"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Zed" not in text and "Yak" not in text
    doc = json.loads(text)
    assert doc["unsafe"] is False
    assert to_safe_profile(str(rare_profile), SafeConfig(k=2)).unsafe is False


# --- #600, #601: masking edge cases -----------------------------------------------------------

import datetime as dt  # noqa: E402

import pyarrow as pa  # noqa: E402

from shape.masking import Masker, MaskingError  # noqa: E402

_KEYS = [bytes([i]) * 32 for i in range(1, 9)]


@pytest.mark.parametrize(
    "value", [dt.date(1, 1, 2), dt.date(9999, 12, 31), dt.datetime(9999, 12, 31, 23)]
)
def test_a_date_at_the_edge_of_the_calendar_is_a_masking_error_or_a_valid_date(value):
    # whatever the key, the result is a date or a MaskingError: never an OverflowError
    for key in _KEYS:
        try:
            out = Masker(key).mask("date", value, max_days=400)
        except MaskingError as e:
            assert "range" in str(e)
        else:
            assert type(out) is type(value)


def test_the_error_reaches_mask_column_as_a_masking_error():
    errors = 0
    for key in _KEYS:
        try:
            Masker(key).mask_column("date", pa.array([dt.date(1, 1, 2)], pa.date32()), max_days=400)
        except MaskingError:
            errors += 1
    assert errors, "the sign of the shift depends on the key, so some key must reach the edge"


def test_a_date_in_the_middle_of_the_calendar_is_unchanged_by_the_fix():
    out = Masker(b"k" * 32).mask("date", "2024-01-15")
    assert out == "2024-03-08"


def test_emails_that_differ_by_case_or_space_are_one_address():
    m = Masker(b"k" * 32)
    table = pa.table({"e": ["Bob@x.com", "bob@x.com", " BOB@X.COM ", "al@x.com"]})
    out = m.mask_tables({"t": table}, {"t": {"e": "email"}})["t"].column("e").to_pylist()
    assert out[0] == out[1] == out[2]
    assert out[3] != out[0]


def test_two_different_emails_that_collide_are_still_an_error(monkeypatch):
    m = Masker(b"k" * 32)
    monkeypatch.setattr(Masker, "_mask_email", lambda self, value: "same@example.com")
    table = pa.table({"e": ["a@x.com", "b@x.com"]})
    with pytest.raises(MaskingError, match="same mask"):
        m.mask_tables({"t": table}, {"t": {"e": "email"}})
