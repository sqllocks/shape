"""W2-07 item 5: type inference confidence on every column."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _w2_07_data import orders_table, write_orders_csv  # noqa: E402

KEYS = ["type", "source", "confidence", "parse_shares", "identifier"]
SHARE_KEYS = ["integer", "float", "boolean", "date", "datetime"]


def csv_file(tmp_path: Path, text: str, name: str = "t.csv") -> str:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def col(prof: Any, name: str, table: str | None = None) -> dict[str, Any]:
    t = prof.tables[table] if table else next(iter(prof.tables.values()))
    return t["columns"][name]["type_inference"]  # type: ignore[no-any-return]


def rows_csv(header: str, values: list[str]) -> str:
    return header + "\n" + "\n".join(values) + "\n"


# --- the shape of the entry --------------------------------------------------------------------


def test_every_column_has_the_entry_with_the_five_shares(tmp_path: Path) -> None:
    prof = shape.profile(write_orders_csv(tmp_path / "o.csv"))
    for c in prof.tables["o"]["columns"].values():
        ti = c["type_inference"]
        assert list(ti)[:5] == KEYS
        assert list(ti["parse_shares"]) == SHARE_KEYS
        assert ti["type"] == c["dtype"]
        assert ti["source"] in ("inferred", "option", "identifier_rule", "declared")


def test_a_csv_column_is_inferred_with_full_confidence(tmp_path: Path) -> None:
    prof = shape.profile(write_orders_csv(tmp_path / "o.csv"))
    amount = col(prof, "amount")
    assert amount["source"] == "inferred" and amount["type"] == "float"
    assert amount["confidence"] == 1.0 and amount["parse_shares"]["float"] == 1.0
    assert "declared" not in amount and "inferred" not in amount
    oid = col(prof, "order_id")
    assert oid["type"] == "integer" and oid["parse_shares"]["integer"] == 1.0
    assert oid["parse_shares"]["float"] == 1.0 and oid["parse_shares"]["date"] == 0.0


def test_text_and_boolean_columns(tmp_path: Path) -> None:
    p = csv_file(tmp_path, rows_csv("flag,word", ["true,a", "false,b", "true,c", "false,d"]))
    prof = shape.profile(p)
    flag, word = col(prof, "flag"), col(prof, "word")
    assert flag["type"] == "boolean" and flag["confidence"] == 1.0
    assert flag["parse_shares"]["boolean"] == 1.0 and flag["parse_shares"]["integer"] == 0.0
    assert word["type"] == "string" and word["confidence"] == 1.0
    assert set(word["parse_shares"].values()) == {0.0}


def test_dates_in_a_csv_are_a_datetime_whose_shares_name_the_date_form(tmp_path: Path) -> None:
    p = csv_file(tmp_path, rows_csv("d,t,w", ["2026-01-05,2026-01-05 10:00:00,Jan 5 2026"] * 40))
    prof = shape.profile(p)
    d, t, w = col(prof, "d"), col(prof, "t"), col(prof, "w")
    assert d["type"] == "datetime" and d["confidence"] == 1.0
    assert d["parse_shares"]["date"] == 1.0 and d["parse_shares"]["datetime"] == 1.0
    assert t["parse_shares"]["date"] == 0.0 and t["parse_shares"]["datetime"] == 1.0
    assert w["type"] == "datetime" and w["confidence"] == 1.0  # the profiler's wider date rules
    assert w["parse_shares"]["datetime"] == 1.0


def test_an_invalid_calendar_date_does_not_count_as_a_date(tmp_path: Path) -> None:
    values = ["2026-01-05"] * 90 + ["2026-13-45"] * 10
    prof = shape.profile(csv_file(tmp_path, rows_csv("d", values)))
    d = col(prof, "d")
    assert d["parse_shares"]["date"] == 0.9 and d["parse_shares"]["datetime"] == 0.9


# --- confidence --------------------------------------------------------------------------------


def test_three_percent_non_numeric_in_an_integer_column_has_confidence_0_97(tmp_path: Path) -> None:
    values = [str(i) for i in range(97)] + ["n.a.", "tbd", "?"]
    prof = shape.profile(csv_file(tmp_path, rows_csv("qty", values)))
    qty = col(prof, "qty")
    assert qty["type"] == "string" and qty["source"] == "inferred"
    assert qty["confidence"] == 0.97
    assert qty["candidate"] == "integer"
    assert qty["parse_shares"]["integer"] == 0.97 and qty["parse_shares"]["float"] == 0.97


def test_confidence_counts_values_not_distinct_values(tmp_path: Path) -> None:
    values = ["1"] * 90 + ["x"] * 10
    qty = col(shape.profile(csv_file(tmp_path, rows_csv("qty", values))), "qty")
    assert qty["confidence"] == 0.9 and qty["candidate"] == "integer"


def test_nulls_are_not_values(tmp_path: Path) -> None:
    values = [str(i) for i in range(95)] + ["x"] * 5 + [""] * 100  # 200 rows, 100 non-null
    qty = col(shape.profile(csv_file(tmp_path, rows_csv("qty", values))), "qty")
    assert qty["confidence"] == 0.95


@pytest.mark.parametrize(
    ("good", "bad", "expect_candidate"),
    [(50, 50, True), (49, 51, False), (99, 1, True), (1, 99, False)],
)
def test_the_candidate_needs_half_the_values(
    tmp_path: Path, good: int, bad: int, expect_candidate: bool
) -> None:
    values = [str(i) for i in range(good)] + [f"w{i}" for i in range(bad)]
    qty = col(shape.profile(csv_file(tmp_path, rows_csv("qty", values))), "qty")
    assert ("candidate" in qty) is expect_candidate
    if expect_candidate:
        assert qty["confidence"] == good / 100
    else:
        assert qty["confidence"] == 1.0


def test_a_column_with_no_values_has_no_confidence(tmp_path: Path) -> None:
    prof = shape.profile(csv_file(tmp_path, rows_csv("a,b", ["1,", "2,", "3,"])))
    b = col(prof, "b")
    assert b["confidence"] is None and set(b["parse_shares"].values()) == {None}


def test_a_float_column_of_whole_numbers_is_an_integer_with_full_confidence(tmp_path: Path) -> None:
    ti = col(shape.profile(csv_file(tmp_path, rows_csv("n", ["1.0", "2.0", "3.0"]))), "n")
    assert ti["type"] == "integer" and ti["confidence"] == 1.0
    assert ti["parse_shares"]["integer"] == 1.0


# --- options and the identifier rule -----------------------------------------------------------


def test_types_string_columns_and_infer_off_are_options(tmp_path: Path) -> None:
    text = rows_csv("a,b,c", ["1.5,2,x"] * 5)
    p = csv_file(tmp_path, text)
    typed = shape.profile(p, types={"a": "float"})
    assert col(typed, "a")["source"] == "option" and col(typed, "a")["type"] == "float"
    assert col(typed, "a")["confidence"] == 1.0
    assert col(typed, "b")["source"] == "inferred"
    kept = shape.profile(p, string_columns=["b"])
    assert col(kept, "b")["source"] == "option" and col(kept, "b")["type"] == "string"
    assert col(kept, "b")["confidence"] == 1.0
    off = shape.profile(p, infer_types="off")
    assert {col(off, c)["source"] for c in "abc"} == {"option"}
    assert {col(off, c)["confidence"] for c in "abc"} == {1.0}


def test_a_zip_the_identifier_rule_kept_as_text(tmp_path: Path) -> None:
    p = csv_file(tmp_path, rows_csv("zip,amount", [f"{i % 9:05d},{i}" for i in range(60)]))
    prof = shape.profile(p)
    z = col(prof, "zip")
    assert z["type"] == "string" and z["source"] == "identifier_rule"
    assert z["identifier"] == "values with leading zeros" and z["confidence"] == 1.0
    assert col(prof, "amount")["source"] == "inferred" and col(prof, "amount")["identifier"] is None


def test_a_fixed_width_column_with_an_identifier_name_is_the_rule_too(tmp_path: Path) -> None:
    p = csv_file(tmp_path, rows_csv("member_id,n", [f"{10000 + i},{i}" for i in range(60)]))
    m = col(shape.profile(p), "member_id")
    assert m["source"] == "identifier_rule" and "fixed width of 5" in m["identifier"]


def test_a_suspect_stays_an_integer_and_carries_the_reason(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="look like identifiers"):
        prof = shape.profile(
            csv_file(tmp_path, rows_csv("reading,n", [f"{10000 + i},{i}" for i in range(60)]))
        )
    c = col(prof, "reading")
    assert c["type"] == "integer" and c["source"] == "inferred"
    assert c["identifier"] == "every value has 5 digits"
    assert col(prof, "n")["identifier"] is None


def test_the_option_beats_the_identifier_rule(tmp_path: Path) -> None:
    p = csv_file(tmp_path, rows_csv("zip", [f"{i % 9:05d}" for i in range(60)]))
    z = col(shape.profile(p, string_columns=["zip"]), "zip")
    assert z["source"] == "option"


def test_files_of_one_table_share_the_identifier_decision(tmp_path: Path) -> None:
    d = tmp_path / "parts"
    d.mkdir()
    (d / "a.csv").write_text(rows_csv("zip,n", [f"{i % 9:05d},{i}" for i in range(40)]))
    (d / "b.csv").write_text(rows_csv("zip,n", [f"{10000 + i},{i}" for i in range(40)]))
    z = col(shape.profile(str(d)), "zip")
    assert z["type"] == "string" and z["source"] == "identifier_rule"


# --- declared types ----------------------------------------------------------------------------


def test_a_clean_typed_parquet_file_agrees_with_itself(tmp_path: Path) -> None:
    p = tmp_path / "o.parquet"
    pq.write_table(orders_table(), p)
    prof = shape.profile(str(p))
    for name, c in prof.tables["o"]["columns"].items():
        ti = c["type_inference"]
        assert ti["source"] == "declared" and ti["confidence"] == 1.0, name
        assert ti["declared"] in {"integer", "float", "string", "date", "boolean"}
    amount = col(prof, "amount")
    assert amount["declared"] == "float" and amount["inferred"] == "float"
    assert col(prof, "ordered_on")["declared"] == "date" == col(prof, "ordered_on")["inferred"]


def test_a_string_column_of_integers(tmp_path: Path) -> None:
    p = tmp_path / "s.parquet"
    pq.write_table(pa.table({"n": pa.array([str(i) for i in range(50)])}), p)
    ti = col(shape.profile(str(p)), "n")
    assert ti["source"] == "declared" and ti["declared"] == "string"
    assert ti["inferred"] == "integer" and ti["type"] == "integer" and ti["confidence"] == 1.0


def test_a_float_column_of_whole_numbers(tmp_path: Path) -> None:
    p = tmp_path / "f.parquet"
    pq.write_table(pa.table({"n": pa.array([float(i) for i in range(50)])}), p)
    ti = col(shape.profile(str(p)), "n")
    assert ti["declared"] == "float" and ti["inferred"] == "integer"
    assert ti["parse_shares"]["integer"] == 1.0
    mixed = pa.table({"n": pa.array([float(i) for i in range(49)] + [0.5])})
    pq.write_table(mixed, p)
    ti = col(shape.profile(str(p)), "n")
    assert ti["inferred"] == "float" and ti["parse_shares"]["integer"] == 0.98


def test_a_string_column_of_iso_dates(tmp_path: Path) -> None:
    p = tmp_path / "d.parquet"
    days = [f"2026-01-{d:02d}" for d in range(1, 29)]
    pq.write_table(pa.table({"d": pa.array(days)}), p)
    ti = col(shape.profile(str(p)), "d")
    assert ti["declared"] == "string" and ti["inferred"] == "date"
    assert ti["parse_shares"]["date"] == 1.0 and ti["confidence"] == 1.0


def test_a_string_column_of_iso_datetimes(tmp_path: Path) -> None:
    p = tmp_path / "d.parquet"
    stamps = [f"2026-01-05T10:{m:02d}:00" for m in range(30)]
    pq.write_table(pa.table({"d": pa.array(stamps)}), p)
    ti = col(shape.profile(str(p)), "d")
    assert ti["inferred"] == "datetime" and ti["parse_shares"]["date"] == 0.0


def test_a_timestamp_column_of_midnights_is_declared_datetime_and_inferred_date(
    tmp_path: Path,
) -> None:
    p = tmp_path / "t.parquet"
    ts = pa.array(
        [1_767_571_200_000_000 + i * 86_400_000_000 for i in range(30)], pa.timestamp("us")
    )
    pq.write_table(pa.table({"t": ts}), p)
    ti = col(shape.profile(str(p)), "t")
    assert ti["declared"] == "datetime" and ti["inferred"] == "date" and ti["type"] == "date"


def test_a_declared_integer_the_identifier_rule_calls_a_suspect(tmp_path: Path) -> None:
    p = tmp_path / "z.parquet"
    pq.write_table(
        pa.table({"zip": pa.array([10000 + i for i in range(40)]), "n": pa.array(range(40))}), p
    )
    prof = shape.profile(str(p))
    z = col(prof, "zip")
    assert z["declared"] == "integer" and z["type"] == "integer"
    assert z["identifier"] is not None and "5" in z["identifier"]
    assert col(prof, "n")["identifier"] is None
    short = tmp_path / "s.parquet"
    pq.write_table(pa.table({"zip": pa.array([10 + i for i in range(40)])}), short)
    assert (
        col(shape.profile(str(short)), "zip")["identifier"] == "its name says it holds identifiers"
    )


def test_arrow_tables_and_data_frames_are_declared() -> None:
    pd = pytest.importorskip("pandas")
    table = orders_table(60)
    for src in (table, table.to_pandas()):
        assert {
            c["type_inference"]["source"]
            for c in shape.profile(src).tables["table"]["columns"].values()
        } == {"declared"}
    assert isinstance(table.to_pandas(), pd.DataFrame)


def test_json_lines_are_inferred_and_row_dicts_are_an_in_memory_table(tmp_path: Path) -> None:
    p = tmp_path / "r.jsonl"
    p.write_text('{"a": 1, "b": "x"}\n' * 5)
    prof = shape.profile(str(p))
    assert col(prof, "a")["source"] == "inferred" and "declared" not in col(prof, "a")
    # row dicts are profiled as the Arrow table they make: the same profile, records included
    rows = shape.profile([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    assert col(rows, "a")["source"] == "declared"
    assert rows == shape.profile(pa.table({"a": [1, 2], "b": ["x", "y"]}))


def test_a_delta_table_is_declared(tmp_path: Path) -> None:
    deltalake = pytest.importorskip("deltalake")
    path = tmp_path / "dt"
    deltalake.write_deltalake(str(path), orders_table(60))
    prof = shape.profile(str(path))
    assert {
        c["type_inference"]["source"] for c in next(iter(prof.tables.values()))["columns"].values()
    } == {"declared"}


# --- with sampling -----------------------------------------------------------------------------


def test_the_shares_are_those_of_the_rows_profiled(tmp_path: Path) -> None:
    values = [str(i) for i in range(60)] + ["bad"] * 40
    p = csv_file(tmp_path, rows_csv("qty", values))
    whole = col(shape.profile(p), "qty")
    head = col(shape.profile(p, sample=50, sample_method="head"), "qty")
    assert whole["confidence"] == 0.6 and whole["type"] == "string"
    assert head["type"] == "integer" and head["confidence"] == 1.0


def test_wide_numeric_columns_keep_their_shares_in_a_sample(tmp_path: Path) -> None:
    prof = shape.profile(orders_table(600), sample=100)
    assert col(prof, "amount")["parse_shares"]["float"] == 1.0
    assert col(prof, "gift")["parse_shares"]["boolean"] == 1.0
