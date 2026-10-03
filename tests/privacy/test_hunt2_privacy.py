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
