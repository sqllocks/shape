"""HUNT2-profile: regression tests for rule proposals and their contract (#645, #646, #647)."""

from __future__ import annotations

import datetime as dt
import json
import math
import warnings
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.contracts.v1 import ContractError
from shape.proposals import DecisionFile, dump_contract, propose_rules

from .conftest import LATER, NOW


def _decided(profile) -> DecisionFile:
    f = DecisionFile.empty()
    f.update(propose_rules(profile), kinds=["rule"], now=NOW)
    for e in f.entries():
        f.decide(e.proposal.id, "accepted", actor="ana", now=LATER)
    return f


def _profile(table: pa.Table):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile({"t": table})


def _claims(profile) -> dict[str, dict]:
    return {p.id: p.claim["tables"]["t"].get("columns", {}) for p in propose_rules(profile)}


# ------------------------------------------------------------------ #645 --merge validation

_GOOD = _profile(pa.table({"v": [1, 2, 3, 4, 5] * 8}))


@pytest.mark.parametrize(
    ("existing", "match"),
    [
        ({"version": 99}, "version 99"),
        ({"format": "shape-contract", "version": 2}, "version 2"),
        ({"future_key": True}, "future_key"),
        ({"format": "something-else"}, "format"),
    ],
)
def test_645_a_newer_or_malformed_contract_is_not_merged(existing: dict, match: str) -> None:
    with pytest.raises(ContractError, match=match):
        _decided(_GOOD).to_contract(existing)


def test_645_a_valid_contract_still_merges() -> None:
    existing = {"format": "shape-contract", "version": 1, "x_note": "kept"}
    out = _decided(_GOOD).to_contract(existing)
    assert out["x_note"] == "kept" and out["version"] == 1 and "tables" in out


def test_645_cli_exits_2_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    decisions = tmp_path / "d.json"
    decisions.write_text(_decided(_GOOD).dumps(), encoding="utf-8")
    existing = tmp_path / "existing.json"
    existing.write_text(json.dumps({"version": 99, "future_key": True}), encoding="utf-8")
    out = tmp_path / "merged.json"
    code = main(
        ["proposals", "contract", "-d", str(decisions), "--merge", str(existing), "-o", str(out)]
    )
    assert code == 2
    assert "version 99" in capsys.readouterr().err
    assert not out.exists()


# ------------------------------------------------------------------- #646 CSV date ranges


def _csv_profile(tmp_path: Path, text: str):
    path = tmp_path / "t.csv"
    path.write_text(text, encoding="utf-8")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.profile({"t": str(path)})


def test_646_csv_dates_get_a_range(tmp_path: Path) -> None:
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i % 28) for i in range(200)]
    profile = _csv_profile(tmp_path, "d\n" + "".join(f"{d}\n" for d in days))
    claims = _claims(profile)
    assert "rule:t.d.range" in claims
    rng = claims["rule:t.d.range"]["d"]
    assert rng["min"] <= "2024-01-01" and rng["max"] >= "2024-01-28"
    assert len(rng["min"]) == len(rng["max"]) == 10  # dates stay dates


def test_646_csv_timestamps_get_a_range(tmp_path: Path) -> None:
    start = dt.datetime(2024, 1, 1, 8)
    stamps = [start + dt.timedelta(hours=7 * (i % 40)) for i in range(200)]
    text = "d\n" + "".join(f"{s:%Y-%m-%d %H:%M:%S}\n" for s in stamps)
    claims = _claims(_csv_profile(tmp_path, text))
    rng = claims["rule:t.d.range"]["d"]
    assert rng["min"] <= "2024-01-01 08:00:00" and len(rng["min"]) == 19


def test_646_csv_text_that_is_not_a_date_gets_no_range(tmp_path: Path) -> None:
    claims = _claims(_csv_profile(tmp_path, "d\n" + "".join(f"w{i % 9}\n" for i in range(200))))
    assert "rule:t.d.range" not in claims


def test_646_the_csv_date_range_passes_shape_check(tmp_path: Path) -> None:
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i % 28) for i in range(200)]
    profile = _csv_profile(tmp_path, "d\n" + "".join(f"{d}\n" for d in days))
    contract = _decided(profile).to_contract()
    assert shape.check(profile, contract).passed


# ----------------------------------------------------------------------- #647 negative zero


def test_647_a_range_clamped_at_zero_is_plus_zero() -> None:
    values = [2.08 + (98.14 - 2.08) * i / 9 for i in range(10)] * 4  # repeats: not a key
    rng = _claims(_profile(pa.table({"v": values})))["rule:t.v.range"]["v"]
    assert rng["min"] == 0.0 and math.copysign(1.0, rng["min"]) == 1.0
    assert '"min": -0.0' not in dump_contract(
        _decided(_profile(pa.table({"v": values}))).to_contract()
    )


def test_647_a_range_clamped_at_zero_from_above_is_plus_zero() -> None:
    values = [-98.14 + (98.14 - 2.08) * i / 9 for i in range(10)] * 4
    rng = _claims(_profile(pa.table({"v": values})))["rule:t.v.range"]["v"]
    assert rng["max"] == 0.0 and math.copysign(1.0, rng["max"]) == 1.0
