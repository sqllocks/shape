"""The safe-profile leak scanner (P7-01): structural, fail-closed, name-independent."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import shape
from shape.cli.main import main
from shape.privacy.safe_profile import to_safe_profile
from shape.privacy.safe_validator import SafeProfileValidator

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2] / "benchmarks" / "vs_refengine" / "safe_profile_1to1"))
from fixtures import FIXTURES  # noqa: E402

CLEAN = {"clean_minimal", "clean_length_minmax", "ok_two_strings", "leak_email_key_value"}


def rules(doc) -> set[str]:
    return {f.rule for f in SafeProfileValidator().validate_data(doc).findings}


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_fixture_verdicts(name):
    found = rules(FIXTURES[name])
    if name in CLEAN:
        assert not found
    else:
        assert found, f"{name} must be flagged"


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("leak_bounds_minmax", "extreme-pair"),
        ("leak_min_value_max_value", "extreme-pair"),
        ("leak_raw_list", "raw-string-list"),
        ("leak_nested_list", "raw-string-list"),
        ("leak_email", "pii-regex"),
        ("leak_ssn", "pii-regex"),
        ("unsafe_stamp", "unsafe-stamp"),
        ("row_count_missing", "row-count-missing"),
        ("row_count_zero", "row-count-missing"),
        ("no_markers", "not-safe-profile"),
    ],
)
def test_rule_names(name, rule):
    assert rule in rules(FIXTURES[name])


def test_length_aggregates_are_the_only_min_max_exemption():
    assert not rules(FIXTURES["clean_length_minmax"])
    doc = json.loads(json.dumps(FIXTURES["clean_length_minmax"]))
    col = doc["tables"]["t"]["columns"]["c"]
    col["renamed"] = col.pop("length_dist")
    assert "extreme-pair" in rules(doc)


def test_unreadable_and_malformed_files_fail_closed(tmp_path):
    v = SafeProfileValidator()
    assert [f.rule for f in v.validate_file(tmp_path / "missing.json").findings] == ["unreadable"]
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    assert [f.rule for f in v.validate_file(bad).findings] == ["malformed"]


@pytest.mark.parametrize("depth", [65, 500, 993, 5000])
@pytest.mark.parametrize("shape_", ["list", "dict"])
def test_over_deep_nesting_is_rejected_not_a_recursion_error(tmp_path, depth, shape_):
    """Fuzz finding (profile-json, seed 20261002): JSON nested just under the parser's own limit
    parsed fine, then overflowed the recursive scan. Deep nesting is a clean `malformed` finding."""
    leaf = '"a@b.com"'
    text = (
        "[" * depth + leaf + "]" * depth
        if shape_ == "list"
        else '{"a":' * depth + leaf + "}" * depth
    )
    deep = tmp_path / "deep.json"
    deep.write_text(text, encoding="utf-8")
    result = SafeProfileValidator().validate_file(deep)
    assert [f.rule for f in result.findings] == ["malformed"]


def test_nesting_at_the_limit_is_still_scanned():
    doc = {"schema_version": 1}
    node = doc
    for _ in range(60):
        node["a"] = {}
        node = node["a"]
    assert "malformed" not in rules(doc)


@pytest.fixture()
def orders(tmp_path):
    rows = ["id,status,email,amount"]
    rows += [f"{i},{'paid' if i % 2 else 'new'},u{i}@x.com,{i * 1.5}" for i in range(120)]
    csv = tmp_path / "orders.csv"
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = tmp_path / "orders.shape"
    shape.save(shape.profile(str(csv)), str(out), capture="full")  # the raw profile it scans
    return out


def test_cli_safe_then_validate_is_clean(orders, tmp_path, capsys):
    safe = tmp_path / "safe.json"
    assert main(["profile", "safe", str(orders), "-o", str(safe), "--k", "7"]) == 0
    doc = json.loads(safe.read_text())
    assert doc["redaction_manifest"]["k_default"] == 7 and doc["unsafe"] is False
    assert main(["profile", "validate", "--safe", str(safe)]) == 0
    assert "CLEAN" in capsys.readouterr().out


def test_cli_validate_flags_the_full_profile_and_unsafe_export(orders, tmp_path, capsys):
    assert main(["profile", "validate", "--safe", str(orders)]) == 1
    err = capsys.readouterr().err
    assert "raw-string-list" in err and "not-safe-profile" in err
    unsafe = tmp_path / "unsafe.json"
    assert main(["profile", "safe", str(orders), "-o", str(unsafe), "--unsafe-full-fidelity"]) == 0
    capsys.readouterr()
    assert main(["profile", "validate", "--safe", str(unsafe), "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["clean"] is False and out["exit_code"] == 1
    assert "unsafe-stamp" in {f["rule"] for f in out["findings"]}


def test_cli_input_errors_exit_2(orders, tmp_path, capsys):
    # P6-10: without --safe, `profile validate` is the structural check (valid profile: 0)
    assert main(["profile", "validate", str(orders)]) == 0
    assert main(["profile", "validate", str(tmp_path / "nope.json")]) == 1
    assert main(["profile", "safe", str(tmp_path / "nope.shape"), "-o", str(tmp_path / "o")]) == 2
    assert main(["profile", "safe", str(orders), "-o", str(tmp_path / "o"), "--column-k", "x"]) == 2
    assert main(["profile", "validate", "--safe", str(tmp_path / "nope.json")]) == 1


def test_validate_runs_without_numpy_or_pyarrow(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps(FIXTURES["clean_minimal"]))
    code = (
        "import sys\n"
        "from shape.cli.main import main\n"
        f"rc = main(['profile', 'validate', '--safe', {str(p)!r}])\n"
        "print(rc, [m for m in ('numpy', 'pyarrow') if m in sys.modules])\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert r.stdout.strip().endswith("0 []"), r.stdout + r.stderr


def test_exports_validate_clean_for_every_variant(orders):
    from shape.privacy.safe_profile import ColumnConfig, SafeConfig

    for cfg in (SafeConfig(), SafeConfig(k=11), SafeConfig(columns={"status": ColumnConfig(k=3)})):
        assert not rules(to_safe_profile(orders, cfg).to_dict())


@pytest.mark.parametrize("finding", sorted((HERE.parent / "fixtures" / "REL-091").glob("*.json")))
def test_rel091_fuzz_row_counts_do_not_overflow(finding):
    result = SafeProfileValidator().validate_file(finding)
    assert result.is_clean


@pytest.mark.parametrize("rows", [0, 1, 4, 5, 10**400])
@pytest.mark.parametrize("rate", [0.0, 0.5, 1.0])
def test_rel091_cohort_threshold_with_large_counts(rows, rate):
    from fractions import Fraction

    doc = {
        "schema_version": 1,
        "unsafe": False,
        "tables": {"t": {"row_count": rows, "columns": {"x": {"null_rate": rate, "mean": 3}}}},
    }
    found = rules(doc)
    assert ("row-count-missing" in found) == (rows == 0)
    assert ("small-cohort-statistic" in found) == (
        rows > 0 and rows - round(Fraction(rate) * rows) < 5
    )
