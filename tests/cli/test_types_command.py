"""W2-07 item 6: ``shape types`` and ``shape.types_report``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.cli.main import main

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _w2_07_data import orders_table, write_shop_dir  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "w2_07"


def run(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    try:
        rc = main(list(args))
    except SystemExit as exc:
        rc = int(exc.code or 0)
    out = capsys.readouterr()
    return rc, out.out, out.err


def make_profile(src: Any, path: Path, **kw: Any) -> Path:
    shape.save(shape.profile(src, **kw), path)
    return path


@pytest.fixture()
def bad_parquet(tmp_path: Path) -> Path:
    n = 60
    table = pa.table(
        {
            "qty": pa.array([str(i) for i in range(n)]),  # a string of integers
            "price": pa.array([float(i) for i in range(n)]),  # a float of whole numbers
            "day": pa.array([f"2026-01-{1 + i % 28:02d}" for i in range(n)]),  # ISO dates as text
            "zip": pa.array([10000 + i % 20 for i in range(n)]),  # an integer that looks like an id
            "name": pa.array([f"person {i}" for i in range(n)]),
            "n": pa.array(range(n)),
        }
    )
    p = tmp_path / "bad.parquet"
    pq.write_table(table, p)
    return make_profile(str(p), tmp_path / "bad.shape")


def by_column(findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        out.setdefault(f["column"], []).append(f)
    return out


# --- the report --------------------------------------------------------------------------------


def test_declared_types_that_differ_from_the_values_are_reported(bad_parquet: Path) -> None:
    found = by_column(shape.types_report(shape.load(str(bad_parquet))))
    assert set(found) == {"qty", "price", "day", "zip"}  # name and n agree with their types
    qty, price, day, zipc = (found[c][0] for c in ("qty", "price", "day", "zip"))
    assert (qty["declared"], qty["inferred"]) == ("string", "integer")
    assert (price["declared"], price["inferred"]) == ("float", "integer")
    assert (day["declared"], day["inferred"]) == ("string", "date")
    for f in (qty, price, day):
        assert f["table"] == "bad" and f["kind"] == "declared_differs" and f["confidence"] == 1.0
        assert f["option"] == "--types"
    assert zipc["kind"] == "identifier_suspect" and zipc["option"] == "--string-columns"
    assert zipc["identifier"] and zipc["declared"] == "integer" and zipc["inferred"] == "string"


def test_every_finding_has_the_same_keys_and_a_message(bad_parquet: Path) -> None:
    for f in shape.types_report(shape.load(str(bad_parquet))):
        assert list(f)[:7] == [
            "table",
            "column",
            "kind",
            "declared",
            "inferred",
            "confidence",
            "option",
        ]
        assert f["table"] in f["message"] and f["column"] in f["message"]
        assert f["option"] in f["message"]


def test_a_clean_typed_file_has_no_findings(tmp_path: Path) -> None:
    prof = shape.profile(orders_table(300))
    assert shape.types_report(prof) == []
    assert shape.types_report(prof, min_confidence=1.0) == []


def test_a_csv_column_with_stray_text_is_a_low_confidence_inferred_type(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("qty\n" + "\n".join([str(i) for i in range(97)] + ["n.a.", "tbd", "?"]) + "\n")
    prof = shape.profile(str(p))
    (f,) = shape.types_report(prof)
    assert f["kind"] == "low_confidence" and f["declared"] is None
    assert f["confidence"] == 0.97 and f["inferred"] == "integer" and f["profile_type"] == "string"
    assert f["option"] == "--types"


def test_the_confidence_threshold_is_strict_and_adjustable(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("qty\n" + "\n".join([str(i) for i in range(97)] + ["x", "y", "z"]) + "\n")
    prof = shape.profile(str(p))
    assert len(shape.types_report(prof)) == 1  # default 0.99
    assert len(shape.types_report(prof, min_confidence=0.98)) == 1
    assert shape.types_report(prof, min_confidence=0.97) == []  # equal to the threshold: not below
    assert shape.types_report(prof, min_confidence=0.0) == []
    assert len(shape.types_report(prof, min_confidence=1.0)) == 1


@pytest.mark.parametrize("bad", [-0.1, 1.01, float("nan"), True, "0.9"])
def test_the_report_refuses_a_bad_threshold(bad: Any) -> None:
    with pytest.raises(ValueError, match="min_confidence"):
        shape.types_report(shape.profile(orders_table(60)), min_confidence=bad)


def test_an_option_or_the_identifier_rule_is_not_a_finding(tmp_path: Path) -> None:
    p = tmp_path / "z.csv"
    p.write_text("zip,qty\n" + "".join(f"{i % 9:05d},{i}\n" for i in range(60)))
    assert shape.types_report(shape.profile(str(p))) == []  # the rule already kept it as text
    assert shape.types_report(shape.profile(str(p), types={"qty": "float"})) == []


def test_a_suspect_integer_in_a_csv_is_reported(tmp_path: Path) -> None:
    p = tmp_path / "s.csv"
    p.write_text("reading,n\n" + "".join(f"{10000 + i},{i}\n" for i in range(60)))
    with pytest.warns(UserWarning):
        prof = shape.profile(str(p))
    (f,) = shape.types_report(prof)
    assert f["kind"] == "identifier_suspect" and f["column"] == "reading"
    assert f["option"] == "--string-columns" and "--string-columns reading" in f["message"]


def test_a_dataset_names_the_table(tmp_path: Path) -> None:
    folder = write_shop_dir(tmp_path / "shop")
    bad = pa.table({"customer_id": pa.array([str(i) for i in range(1, 11)])})
    pq.write_table(bad, folder / "extra.parquet")
    prof = shape.profile(
        {"orders": str(folder / "orders.csv"), "extra": str(folder / "extra.parquet")}
    )
    (f,) = shape.types_report(prof)
    assert f["table"] == "extra" and f["column"] == "customer_id"


def test_a_sampled_profile_is_reported_on_its_rows(tmp_path: Path) -> None:
    prof = shape.profile(orders_table(600), sample=100)
    assert shape.types_report(prof) == []


# --- the contract ------------------------------------------------------------------------------


def test_a_contract_type_that_differs_from_the_profiles(tmp_path: Path) -> None:
    prof = shape.profile(orders_table(300))
    contract = {"columns": {"amount": {"dtype": "integer"}, "status": {"dtype": "string"}}}
    (f,) = shape.types_report(prof, contract=contract)
    assert f["kind"] == "contract_differs" and f["column"] == "amount"
    assert f["declared"] == "integer" and f["profile_type"] == "float"
    assert f["option"] == "--types"


def test_a_contract_can_be_a_file_and_names_tables_for_a_dataset(tmp_path: Path) -> None:
    prof = shape.profile({"a": orders_table(100), "b": orders_table(100)})
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"tables": {"b": {"columns": {"gift": {"dtype": "string"}}}}}))
    (f,) = shape.types_report(prof, contract=str(path))
    assert f["table"] == "b" and f["column"] == "gift"


def test_a_contract_with_unknown_columns_or_tables_is_not_a_finding() -> None:
    prof = shape.profile(orders_table(100))
    assert shape.types_report(prof, contract={"columns": {"nope": {"dtype": "integer"}}}) == []
    d = shape.profile({"a": orders_table(100), "b": orders_table(100)})
    assert shape.types_report(d, contract={"tables": {"zzz": {"columns": {}}}}) == []


def test_a_malformed_contract_raises() -> None:
    from shape.contracts.v1 import ContractError

    with pytest.raises(ContractError):
        shape.types_report(shape.profile(orders_table(60)), contract={"columns": 3})
    with pytest.raises(ContractError, match="tables"):
        shape.types_report(
            shape.profile({"a": orders_table(60), "b": orders_table(60)}), contract={"columns": {}}
        )


# --- a profile written before W2-07 ------------------------------------------------------------


def test_an_old_profile_is_reported_on_without_error() -> None:
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))
    assert shape.types_report(old) == []
    (f,) = shape.types_report(old, contract={"columns": {"amount": {"dtype": "integer"}}})
    assert f["kind"] == "contract_differs" and f["confidence"] is None


# --- the command -------------------------------------------------------------------------------


def test_the_command_exits_1_with_findings_and_prints_a_line_each(
    bad_parquet: Path, capsys: Any
) -> None:
    rc, out, _ = run("types", str(bad_parquet), capsys=capsys)
    assert rc == 1
    lines = [line for line in out.splitlines() if line.startswith("bad.")]
    assert len(lines) == 4
    qty = next(line for line in lines if line.startswith("bad.qty"))
    for part in ("declared string", "inferred integer", "confidence 1.00", "--types"):
        assert part in qty
    assert "--string-columns" in next(line for line in lines if line.startswith("bad.zip"))


def test_the_command_exits_0_for_a_clean_file(tmp_path: Path, capsys: Any) -> None:
    p = make_profile(orders_table(300), tmp_path / "ok.shape")
    rc, out, _ = run("types", str(p), capsys=capsys)
    assert rc == 0 and "no type findings" in out.lower()
    rc, out, _ = run("types", str(p), "--json", capsys=capsys)
    assert rc == 0 and json.loads(out) == []


def test_the_json_output_is_the_report(bad_parquet: Path, capsys: Any) -> None:
    rc, out, _ = run("types", str(bad_parquet), "--json", capsys=capsys)
    assert rc == 1
    assert json.loads(out) == json.loads(
        json.dumps(shape.types_report(shape.load(str(bad_parquet))))
    )


def test_the_command_reads_a_contract_and_a_threshold(tmp_path: Path, capsys: Any) -> None:
    p = make_profile(orders_table(300), tmp_path / "ok.shape")
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"columns": {"amount": {"dtype": "integer"}}}))
    rc, out, _ = run("types", str(p), "--contract", str(c), capsys=capsys)
    assert rc == 1 and "contract" in out and "bad" not in out
    csv = tmp_path / "t.csv"
    csv.write_text("qty\n" + "\n".join([str(i) for i in range(97)] + ["x", "y", "z"]) + "\n")
    q = make_profile(str(csv), tmp_path / "q.shape")
    assert run("types", str(q), capsys=capsys)[0] == 1
    assert run("types", str(q), "--min-confidence", "0.97", capsys=capsys)[0] == 0


@pytest.mark.parametrize("bad", ["-0.1", "1.5", "x", "nan"])
def test_a_bad_threshold_exits_2(tmp_path: Path, capsys: Any, bad: str) -> None:
    p = make_profile(orders_table(60), tmp_path / "p.shape")
    rc, _, err = run("types", str(p), "--min-confidence", bad, capsys=capsys)
    assert rc == 2 and err.strip()


def test_bad_input_exits_2(tmp_path: Path, capsys: Any) -> None:
    p = make_profile(orders_table(60), tmp_path / "p.shape")
    assert run("types", str(tmp_path / "missing.shape"), capsys=capsys)[0] == 2
    junk = tmp_path / "junk.shape"
    junk.write_text("not a profile")
    assert run("types", str(junk), capsys=capsys)[0] == 2
    assert run("types", str(p), "--contract", str(tmp_path / "none.json"), capsys=capsys)[0] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert run("types", str(p), "--contract", str(bad), capsys=capsys)[0] == 2
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"columns": 3}))
    assert run("types", str(p), "--contract", str(wrong), capsys=capsys)[0] == 2
    model = tmp_path / "m.shape"
    from shape.artifact import write_shape
    from shape.capture import capture_arrow

    write_shape(str(model), capture_arrow(orders_table(60)).to_dict(), name="m")
    assert run("types", str(model), capsys=capsys)[0] == 2


def test_an_old_profile_through_the_command(capsys: Any) -> None:
    rc, out, err = run("types", str(FIXTURES / "pre_w2_07_orders.shape"), capsys=capsys)
    assert rc == 0 and "not recorded" in err
    rc, out, _ = run("types", str(FIXTURES / "pre_w2_07_orders.shape"), "--json", capsys=capsys)
    assert rc == 0 and json.loads(out) == []
