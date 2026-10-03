"""W2-07 items 1-3: sampling controls, the sampling record and adequacy."""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.csv as pacsv  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.profile import sampling as S

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _w2_07_data import (  # noqa: E402
    orders_table,
    shop_tables,
    write_orders_csv,
    write_orders_parquet,
)

SRC = Path(shape.__file__).parent


def ids(prof: Any, table: str | None = None) -> dict[str, Any]:
    """The order_id column of a profile (a unique 1..N key in the fixtures)."""
    tables = prof.tables
    t = tables[table] if table else next(iter(tables.values()))
    return t["columns"]["order_id"]


def sampled_ids_mean(positions: Any) -> float:
    """Mean order_id of the rows at ``positions`` (order_id = position + 1)."""
    return float(np.mean(np.asarray(positions) + 1))


# --- the spec ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [("500", (500, None)), ("1", (1, None)), ("10%", (None, 0.1)), ("100%", (None, 1.0)),
     ("12.5%", (None, 0.125)), (" 7 ", (7, None))],
)  # fmt: skip
def test_parse_sample(text: str, want: tuple[Any, Any]) -> None:
    assert S.parse_sample(text) == want


@pytest.mark.parametrize(
    "text",
    ["0", "-5", "0%", "101%", "100.1%", "-1%", "abc", "", "%", "1e3", "10 %%", "nan%", "inf%"],
)
def test_parse_sample_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="--sample"):
        S.parse_sample(text)


@pytest.mark.parametrize(
    "bad",
    [0, -1, 0.0, -0.5, 1.5, 2.0, float("nan"), float("inf"), True, "0%", "x", [1], {}],
)
def test_make_spec_rejects_invalid_sample(bad: Any) -> None:
    with pytest.raises(ValueError):
        S.make_spec(bad)


def test_make_spec_accepts_rows_fractions_and_text() -> None:
    assert S.make_spec(100) == S.SampleSpec(rows=100)
    assert S.make_spec(np.int64(100)) == S.SampleSpec(rows=100)
    assert S.make_spec(0.25) == S.SampleSpec(fraction=0.25)
    assert S.make_spec(1.0) == S.SampleSpec(fraction=1.0)
    assert S.make_spec("10%", "head", 9) == S.SampleSpec(fraction=0.1, method="head", seed=9)
    assert S.make_spec(None) is None


def test_make_spec_rejects_method_and_seed_without_sample() -> None:
    with pytest.raises(ValueError, match="need sample"):
        S.make_spec(None, "head")
    with pytest.raises(ValueError, match="need sample"):
        S.make_spec(None, "random", 7)


@pytest.mark.parametrize("method", ["stratified", "", "Random", None, 3])
def test_unknown_method_is_refused(method: Any) -> None:
    with pytest.raises(ValueError, match="method"):
        S.make_spec(10, method)


@pytest.mark.parametrize("seed", [-1, 2**32, 1.5, "7", True])
def test_bad_seed_is_refused(seed: Any) -> None:
    with pytest.raises(ValueError, match="seed"):
        S.make_spec(10, "random", seed)


def test_seed_boundaries_are_accepted() -> None:
    assert S.make_spec(10, "random", 0).seed == 0
    assert S.make_spec(10, "random", 2**32 - 1).seed == 2**32 - 1


def test_default_seed_is_42() -> None:
    assert S.make_spec(10).seed == 42 == S.DEFAULT_SEED


# --- the selection ----------------------------------------------------------------------------


def test_random_is_uniform_without_replacement_sorted_and_seeded() -> None:
    spec = S.SampleSpec(rows=100, seed=3)
    a, b = S.select(spec, 1000), S.select(spec, 1000)
    assert a is not None and b is not None
    assert len(a) == 100 and len(set(a.tolist())) == 100
    assert a.min() >= 0 and a.max() < 1000
    assert np.all(np.diff(a) > 0)  # sorted: row order is kept
    assert np.array_equal(a, b)
    other = S.select(S.SampleSpec(rows=100, seed=4), 1000)
    assert other is not None and not np.array_equal(a, other)


def test_random_is_frozen_to_numpy_randomstate() -> None:
    """The selection must not move with numpy or the kernel: it is the legacy RandomState."""
    want = np.sort(np.random.RandomState(42).choice(50, size=5, replace=False))
    got = S.select(S.SampleSpec(rows=5), 50)
    assert got is not None and got.tolist() == want.tolist()


def test_random_selection_is_roughly_uniform() -> None:
    spec = S.SampleSpec(rows=2000, seed=1)
    idx = S.select(spec, 10_000)
    assert idx is not None
    halves = np.histogram(idx, bins=4, range=(0, 10_000))[0]
    assert all(380 < h < 620 for h in halves)


def test_head_takes_exactly_the_first_rows_whatever_the_seed() -> None:
    for seed in (1, 99):
        got = S.select(S.SampleSpec(rows=7, method="head", seed=seed), 50)
        assert got is not None and got.tolist() == list(range(7))


def test_systematic_is_evenly_spread_from_a_seeded_start() -> None:
    spec = S.SampleSpec(rows=100, method="systematic", seed=5)
    idx = S.select(spec, 1000)
    assert idx is not None and len(idx) == 100
    assert set(np.diff(idx).tolist()) == {10}  # every 10th row
    assert 0 <= idx[0] < 10 and idx[-1] < 1000
    assert np.array_equal(idx, S.select(spec, 1000))
    other = S.select(S.SampleSpec(rows=100, method="systematic", seed=6), 1000)
    assert other is not None and not np.array_equal(idx, other)


def test_systematic_with_a_fractional_step_stays_distinct_and_in_range() -> None:
    idx = S.select(S.SampleSpec(rows=7, method="systematic", seed=2), 20)
    assert idx is not None and len(idx) == 7 and len(set(idx.tolist())) == 7
    assert idx.max() < 20 and set(np.diff(idx).tolist()) <= {2, 3}


@pytest.mark.parametrize("method", ["random", "systematic", "head"])
def test_a_sample_of_every_row_is_not_a_sample(method: str) -> None:
    assert S.select(S.SampleSpec(rows=50, method=method), 50) is None
    assert S.select(S.SampleSpec(rows=500, method=method), 50) is None
    assert S.select(S.SampleSpec(fraction=1.0, method=method), 50) is None


@pytest.mark.parametrize("method", ["random", "systematic", "head"])
def test_a_sample_of_one_row(method: str) -> None:
    idx = S.select(S.SampleSpec(rows=1, method=method), 50)
    assert idx is not None and len(idx) == 1


def test_fraction_rounds_and_keeps_at_least_one_row() -> None:
    assert S.SampleSpec(fraction=0.1).target(1000) == 100
    assert S.SampleSpec(fraction=0.001).target(10) == 1
    assert S.SampleSpec(fraction=0.5).target(0) == 0


# --- adequacy formulas ------------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 30, 298, 299, 1000, 100_000])
def test_min_detectable_share_is_the_closed_form(n: int) -> None:
    assert S.min_detectable_share(n) == pytest.approx(1 - 0.05 ** (1 / n), rel=1e-12)
    share = S.min_detectable_share(n)
    assert share is not None
    # a value with that share is missed with probability 5% in n draws
    assert (1 - share) ** n == pytest.approx(0.05, rel=1e-9)


def test_min_detectable_share_of_nothing() -> None:
    assert S.min_detectable_share(0) is None


def test_null_rate_se_closed_form_with_and_without_the_population() -> None:
    p, n, big_n = 0.2, 1000, 50_000
    plain = math.sqrt(p * (1 - p) / n)
    assert S.null_rate_se(p, n, None) == pytest.approx(plain)
    assert S.null_rate_se(p, n, big_n) == pytest.approx(
        plain * math.sqrt((big_n - n) / (big_n - 1))
    )
    assert S.null_rate_se(p, n, big_n) < plain


def test_null_rate_se_is_zero_when_every_row_was_read() -> None:
    assert S.null_rate_se(0.3, 100, 100) == 0.0
    assert S.null_rate_se(0.3, 100, 50) == 0.0  # population smaller than the rows: whole table
    assert S.null_rate_se(0.0, 100, None) == 0.0


def test_null_rate_se_unknown_without_rows() -> None:
    assert S.null_rate_se(None, 0, 10) is None
    assert S.null_rate_se(0.1, 0, 10) is None


@pytest.mark.parametrize(
    ("rows", "level"),
    [(0, "insufficient"), (10, "insufficient"), (29, "insufficient"), (30, "limited"),
     (298, "limited"), (299, "adequate"), (300, "adequate"), (100_000, "adequate")],
)  # fmt: skip
def test_adequacy_rule_boundaries(rows: int, level: str) -> None:
    got, reason = S.adequacy_level(rows)
    assert got == level
    assert isinstance(reason, str) and reason
    if level != "insufficient":
        assert f"{S.min_detectable_share(rows) * 100:.2f}%" in reason  # type: ignore[operator]


def test_the_insufficient_threshold_is_the_drift_engines_min_rows() -> None:
    from shape.drift.engine import DEFAULT_THRESHOLDS as DRIFT_DEFAULTS

    assert S.MIN_ROWS == DRIFT_DEFAULTS["min_rows"]


# --- the record in a profile ------------------------------------------------------------------


def test_an_unsampled_profile_records_that_the_table_was_read_whole() -> None:
    t = shape.profile(orders_table(600)).tables["table"]
    rec = t["sampling"]
    assert rec["method"] == "none" and rec["seed"] is None and rec["requested"] is None
    assert rec["population_rows"] == 600 and rec["sampled_rows"] == 600 == t["row_count"]
    assert rec["adequacy"] == "adequate"
    assert isinstance(rec["adequacy_reason"], str)
    assert list(rec) == [
        "method", "seed", "requested", "population_rows", "sampled_rows", "internal",
        "adequacy", "adequacy_reason",
    ]  # fmt: skip


def test_a_random_sample_is_recorded() -> None:
    t = shape.profile(orders_table(600), sample=100).tables["table"]
    rec = t["sampling"]
    assert t["row_count"] == 100
    assert rec["method"] == "random" and rec["seed"] == 42
    assert rec["requested"] == {"rows": 100}
    assert rec["population_rows"] == 600 and rec["sampled_rows"] == 100
    assert rec["adequacy"] == "limited"


def test_a_fraction_is_recorded_as_a_fraction() -> None:
    t = shape.profile(orders_table(600), sample=0.25, sample_method="systematic", sample_seed=8)
    rec = t.tables["table"]["sampling"]
    assert rec["requested"] == {"fraction": 0.25}
    assert rec["method"] == "systematic" and rec["seed"] == 8
    assert rec["sampled_rows"] == 150 == t.tables["table"]["row_count"]


def test_head_records_the_seed_it_was_given_though_it_does_not_use_it() -> None:
    rec = shape.profile(orders_table(600), sample=50, sample_method="head", sample_seed=9).tables[
        "table"
    ]["sampling"]
    assert rec["method"] == "head" and rec["seed"] == 9
    other = shape.profile(orders_table(600), sample=50, sample_method="head", sample_seed=10)
    again = shape.profile(orders_table(600), sample=50, sample_method="head", sample_seed=9)
    t = other.tables["table"]["columns"]["order_id"]
    assert t == again.tables["table"]["columns"]["order_id"]  # the rows do not depend on it


def test_asking_for_more_rows_than_the_table_has_reads_it_whole() -> None:
    t = shape.profile(orders_table(600), sample=5000).tables["table"]
    rec = t["sampling"]
    assert t["row_count"] == 600
    assert rec["method"] == "none" and rec["requested"] == {"rows": 5000}
    assert rec["population_rows"] == 600 and rec["sampled_rows"] == 600


def test_a_whole_table_sample_equals_the_unsampled_profile_apart_from_the_request() -> None:
    plain = shape.profile(orders_table(600)).to_dict()
    asked = shape.profile(orders_table(600), sample=1.0).to_dict()
    assert asked["sampling"].pop("requested") == {"fraction": 1.0}
    plain["sampling"].pop("requested")
    assert plain == asked


@pytest.mark.parametrize("bad", [0, -3, 0.0, 1.5, "0%", "x"])
def test_invalid_sample_raises(bad: Any) -> None:
    with pytest.raises(ValueError):
        shape.profile(orders_table(60), sample=bad)


def test_invalid_method_raises() -> None:
    with pytest.raises(ValueError, match="method"):
        shape.profile(orders_table(60), sample=10, sample_method="stratified")


def test_method_without_sample_raises() -> None:
    with pytest.raises(ValueError, match="need sample"):
        shape.profile(orders_table(60), sample_method="head")


def test_the_same_seed_gives_the_same_profile_and_another_seed_another_sample() -> None:
    a = shape.profile(orders_table(600), sample=100, sample_seed=7).to_dict()
    b = shape.profile(orders_table(600), sample=100, sample_seed=7).to_dict()
    c = shape.profile(orders_table(600), sample=100, sample_seed=8).to_dict()
    assert a == b
    assert a != c


def test_random_sample_profiles_exactly_the_selected_rows() -> None:
    n, k = 600, 100
    idx = S.select(S.SampleSpec(rows=k, seed=11), n)
    assert idx is not None
    col = ids(shape.profile(orders_table(n), sample=k, sample_seed=11))
    assert col["mean"] == pytest.approx(sampled_ids_mean(idx))
    assert col["min_value"][1] == int(idx.min()) + 1 and col["max_value"][1] == int(idx.max()) + 1
    assert col["cardinality"] == k


def test_systematic_sample_profiles_exactly_the_selected_rows() -> None:
    n, k = 600, 100
    idx = S.select(S.SampleSpec(rows=k, method="systematic", seed=11), n)
    assert idx is not None
    col = ids(shape.profile(orders_table(n), sample=k, sample_method="systematic", sample_seed=11))
    assert col["mean"] == pytest.approx(sampled_ids_mean(idx))


def test_head_profiles_exactly_the_first_n_rows() -> None:
    col = ids(shape.profile(orders_table(600), sample=40, sample_method="head"))
    assert col["min_value"][1] == 1 and col["max_value"][1] == 40
    assert col["mean"] == pytest.approx(20.5) and col["cardinality"] == 40


def test_a_sample_keeps_the_text_flag_of_identifier_columns(tmp_path: Path) -> None:
    csv = tmp_path / "zips.csv"
    csv.write_text("zip,amount\n" + "".join(f"{i % 90:05d},{i}\n" for i in range(400)))
    t = shape.profile(str(csv), sample=100).tables["zips"]
    assert t["columns"]["zip"]["dtype"] == "string"
    assert t["columns"]["zip"]["min_value"][1].startswith("0")


# --- the same rows from every kind of source --------------------------------------------------


def _mean_ids(prof: Any) -> float:
    return float(ids(prof)["mean"])


def test_a_file_a_parquet_file_an_arrow_table_and_a_data_frame_pick_the_same_rows(
    tmp_path: Path,
) -> None:
    pd = pytest.importorskip("pandas")
    csv = write_orders_csv(tmp_path / "orders.csv")
    pqf = write_orders_parquet(tmp_path / "orders.parquet")
    table = orders_table()
    opts: dict[str, Any] = {"sample": 90, "sample_seed": 5}
    want = _mean_ids(shape.profile(table, **opts))
    for source in (str(csv), str(pqf), table, table.to_pandas()):
        assert _mean_ids(shape.profile(source, **opts)) == pytest.approx(want)
    assert isinstance(table.to_pandas(), pd.DataFrame)
    idx = S.select(S.SampleSpec(rows=90, seed=5), 600)
    assert idx is not None
    assert want == pytest.approx(sampled_ids_mean(idx))


def test_a_folder_and_a_glob_sample_the_files_as_one_table(tmp_path: Path) -> None:
    whole = orders_table(600)
    folder = tmp_path / "parts"
    folder.mkdir()
    for i in range(3):
        pacsv.write_csv(whole.slice(i * 200, 200), folder / f"part{i}.csv")
    opts: dict[str, Any] = {"sample": 90, "sample_seed": 5}
    want = _mean_ids(shape.profile(whole, **opts))
    by_folder = shape.profile(str(folder), **opts)
    by_glob = shape.profile(str(folder / "part*.csv"), **opts)
    assert _mean_ids(by_folder) == pytest.approx(want)
    assert _mean_ids(by_glob) == pytest.approx(want)
    assert next(iter(by_folder.tables.values()))["sampling"]["population_rows"] == 600


def test_a_delta_table_samples_the_version_asked_for(tmp_path: Path) -> None:
    deltalake = pytest.importorskip("deltalake")
    path = tmp_path / "delta"
    whole = orders_table(600)
    deltalake.write_deltalake(str(path), whole.slice(0, 300))
    deltalake.write_deltalake(str(path), whole.slice(300, 300), mode="append")
    opts: dict[str, Any] = {"sample": 60, "sample_seed": 5}
    v0 = shape.profile(str(path), version=0, **opts)
    latest = shape.profile(str(path), **opts)
    t0 = next(iter(v0.tables.values()))["sampling"]
    assert t0["population_rows"] == 300 and t0["sampled_rows"] == 60
    assert next(iter(latest.tables.values()))["sampling"]["population_rows"] == 600
    # the rows are chosen by position in the order the table reads back, version by version
    read_v0 = deltalake.DeltaTable(str(path), version=0).to_pyarrow_table()
    read_latest = deltalake.DeltaTable(str(path)).to_pyarrow_table()
    assert _mean_ids(v0) == pytest.approx(_mean_ids(shape.profile(read_v0, **opts)))
    assert _mean_ids(latest) == pytest.approx(_mean_ids(shape.profile(read_latest, **opts)))
    as_of = shape.profile(str(path), as_of="2999-01-01T00:00:00Z", **opts)
    assert _mean_ids(as_of) == pytest.approx(_mean_ids(latest))
    again = shape.profile(str(path), version=0, **opts)
    assert again.to_dict() == v0.to_dict()


def test_a_workbook_is_sampled_per_sheet_or_refused_clearly(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["a", "b"])
    for i in range(200):
        ws.append([i, i * 2])
    path = tmp_path / "book.xlsx"
    wb.save(path)
    prof = shape.profile(str(path), sample=50)
    for t in prof.tables.values():
        assert t["row_count"] == 50 and t["sampling"]["population_rows"] == 200


# --- datasets ---------------------------------------------------------------------------------


def test_a_dataset_samples_each_table_and_keeps_foreign_key_detection() -> None:
    tables = shop_tables(customers=300, orders=1500)
    prof = shape.profile(tables, sample=0.2)
    t = prof.tables
    assert t["customer"]["row_count"] == 60 and t["orders"]["row_count"] == 300
    assert t["customer"]["sampling"]["population_rows"] == 300
    assert t["orders"]["sampling"]["population_rows"] == 1500
    assert t["orders"]["detected_fks"] == {"customer_id": "customer"}
    rels = prof.to_dict()["relationships"]
    assert [(r["child"], r["parent"]) for r in rels] == [("orders", "customer")]


def test_a_dataset_sample_is_deterministic() -> None:
    tables = shop_tables()
    a = shape.profile(tables, sample=100, sample_seed=3).to_dict()
    b = shape.profile(tables, sample=100, sample_seed=3).to_dict()
    assert a == b


def test_a_dataset_keeps_a_foreign_key_that_the_sample_alone_would_lose() -> None:
    """Independent samples of parent and child share few keys; detection compares the sampled
    child values with the parent's whole key column, so sampling does not hide the relationship."""
    tables = shop_tables(customers=2000, orders=2000)
    prof = shape.profile(tables, sample=100, sample_seed=1)
    assert prof.tables["orders"]["detected_fks"] == {"customer_id": "customer"}


# --- the reference helpers ---------------------------------------------------------------------


def test_the_sample_rows_argument_of_the_reference_helpers_writes_the_record(
    tmp_path: Path,
) -> None:
    from shape.profile.reference.table import profile_csv, profile_parquet, profile_table

    table = orders_table(600)
    csv = write_orders_csv(tmp_path / "o.csv")
    pqf = write_orders_parquet(tmp_path / "o.parquet")
    for tp in (
        profile_table(table, "t", sample_rows=100),
        profile_csv(csv, sample_rows=100),
        profile_parquet(pqf, sample_rows=100),
    ):
        rec = tp.sampling
        assert rec is not None
        assert rec["method"] == "random" and rec["seed"] == 42
        assert rec["requested"] == {"rows": 100}
        assert rec["population_rows"] == 600 and rec["sampled_rows"] == 100 == tp.row_count


def test_the_reference_helpers_record_a_whole_read_too(tmp_path: Path) -> None:
    from shape.profile.reference.table import profile_table

    tp = profile_table(orders_table(100), "t")
    assert tp.sampling is not None and tp.sampling["method"] == "none"
    assert tp.sampling["population_rows"] == 100


def test_the_dataset_helper_records_every_table() -> None:
    from shape.profile.reference.table import profile_dataset

    dp = profile_dataset(shop_tables(50, 200))
    assert {n: t.sampling["method"] for n, t in dp.tables.items()} == {
        "customer": "none",
        "orders": "none",
    }


# --- adequacy in profiles ---------------------------------------------------------------------


def test_a_ten_row_sample_is_insufficient() -> None:
    t = shape.profile(orders_table(600), sample=10).tables["table"]
    assert t["sampling"]["adequacy"] == "insufficient"
    assert "10 rows" in t["sampling"]["adequacy_reason"]


def test_a_small_whole_table_is_insufficient_too() -> None:
    t = shape.profile(orders_table(12)).tables["table"]
    assert t["sampling"]["method"] == "none" and t["sampling"]["adequacy"] == "insufficient"


@pytest.mark.parametrize(
    ("rows", "level"),
    [(29, "insufficient"), (30, "limited"), (298, "limited"), (299, "adequate")],
)
def test_the_adequacy_boundaries_in_a_profile(rows: int, level: str) -> None:
    t = shape.profile(orders_table(400), sample=rows).tables["table"]
    assert t["sampling"]["adequacy"] == level


def test_every_column_carries_its_adequacy() -> None:
    t = shape.profile(orders_table(600), sample=200).tables["table"]
    note = t["columns"]["note"]
    a = note["adequacy"]
    assert list(a) == ["non_null", "null_rate_se", "min_detectable_share"]
    assert a["non_null"] == 200 - note["null_count"]
    p, n, big_n = note["null_rate"], 200, 600
    assert a["null_rate_se"] == pytest.approx(
        math.sqrt(p * (1 - p) / n) * math.sqrt((big_n - n) / (big_n - 1)), rel=1e-4
    )
    assert a["min_detectable_share"] == pytest.approx(1 - 0.05 ** (1 / a["non_null"]))


def test_an_unsampled_column_has_no_null_rate_uncertainty() -> None:
    a = shape.profile(orders_table(600)).tables["table"]["columns"]["note"]["adequacy"]
    assert a["null_rate_se"] == 0.0


def test_a_column_with_no_values_has_no_detectable_share() -> None:
    t = pa.table({"a": pa.array([None] * 40, pa.string()), "b": pa.array(range(40))})
    a = shape.profile(t).tables["table"]["columns"]["a"]["adequacy"]
    assert a["non_null"] == 0 and a["min_detectable_share"] is None


def test_a_hundred_thousand_of_a_million_rows_is_adequate_with_the_closed_form_error() -> None:
    n_total, n = 1_000_000, 100_000
    rng = np.random.RandomState(1)
    values = rng.randint(0, 1000, n_total)
    mask = rng.rand(n_total) < 0.1
    table = pa.table(
        {"v": pa.array(values, mask=mask), "k": pa.array(np.arange(n_total, dtype=np.int64))}
    )
    t = shape.profile(table, sample=n, joint=False).tables["table"]
    assert t["sampling"]["adequacy"] == "adequate"
    assert t["sampling"]["population_rows"] == n_total and t["row_count"] == n
    p = t["columns"]["v"]["null_rate"]
    want = math.sqrt(p * (1 - p) / n) * math.sqrt((n_total - n) / (n_total - 1))
    assert t["columns"]["v"]["adequacy"]["null_rate_se"] == pytest.approx(want, rel=1e-4)


# --- the internal samples ---------------------------------------------------------------------


def _internal(prof: Any) -> dict[str, dict[str, Any]]:
    t = next(iter(prof.tables.values()))
    return {e["analysis"]: e for e in t["sampling"]["internal"]}


def test_a_small_table_has_no_internal_samples() -> None:
    assert shape.profile(orders_table(600)).tables["table"]["sampling"]["internal"] == []


def test_pattern_detection_and_distribution_fit_samples_are_recorded() -> None:
    rng = np.random.RandomState(2)
    table = pa.table(
        {
            "x": pa.array(rng.normal(0, 1, 5000)),
            "s": pa.array([f"v{int(i)}" for i in rng.randint(0, 50, 5000)]),
            "short": pa.array([f"v{int(i)}" for i in rng.randint(0, 5, 5000)]),
        }
    )
    got = _internal(shape.profile(table))
    assert got["pattern_detection"]["rows"] == 1000
    assert got["pattern_detection"]["method"] == "random" and got["pattern_detection"]["seed"] == 42
    assert got["pattern_detection"]["columns"] == ["s", "short"]
    assert got["distribution_fit"]["rows"] == 2000 and got["distribution_fit"]["columns"] == ["x"]
    assert "joint" not in got  # 5000 rows are analysed whole


def test_no_internal_sample_is_recorded_at_the_threshold() -> None:
    rng = np.random.RandomState(2)
    table = pa.table(
        {
            "x": pa.array(rng.normal(0, 1, 2000)),
            "s": pa.array([f"v{int(i)}" for i in rng.randint(0, 50, 1000)] * 2),
        }
    )
    got = _internal(shape.profile(table))
    assert "distribution_fit" not in got  # exactly 2000 values are fitted whole
    assert got["pattern_detection"]["columns"] == ["s"]  # 2000 > 1000
    one_k = pa.table(
        {"s": pa.array([f"v{i % 50}" for i in range(1000)]), "t": pa.array(range(1000))}
    )
    assert "pattern_detection" not in _internal(shape.profile(one_k))  # exactly 1000: whole


def test_the_pattern_rates_sample_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    from shape.profile.reference import column

    monkeypatch.setattr(column, "_RATE_MAX_DISTINCT", 100)
    table = pa.table({"s": pa.array([f"v{i}" for i in range(500)]), "n": pa.array(range(500))})
    got = _internal(shape.profile(table))
    assert (
        got["pattern_rates"]["columns"] == ["s"] and got["pattern_rates"]["method"] == "systematic"
    )
    assert got["pattern_rates"]["seed"] is None


def test_the_kendall_sample_is_recorded() -> None:
    rng = np.random.RandomState(8)
    x = rng.normal(0, 1, 800)
    table = pa.table({"x": pa.array(x), "y": pa.array(2 * x + rng.normal(0, 0.1, 800)),
                      "g": pa.array(rng.randint(0, 3, 800))})  # fmt: skip
    prof = shape.profile(table)
    assoc = prof.tables["table"]["joint"]["associations"]
    assert any(a.get("kendall") is not None and a["rows"] > 500 for a in assoc)
    got = _internal(prof)["kendall_tau"]
    assert got["rows"] == 500 and got["method"] == "systematic"


def test_no_kendall_sample_when_every_pair_is_short() -> None:
    rng = np.random.RandomState(8)
    x = rng.normal(0, 1, 400)
    table = pa.table({"x": pa.array(x), "y": pa.array(2 * x + rng.normal(0, 0.1, 400))})
    assert "kendall_tau" not in _internal(shape.profile(table))


def test_the_joint_sample_is_recorded() -> None:
    rng = np.random.RandomState(3)
    n = 25_000
    table = pa.table(
        {
            "a": pa.array(rng.randint(0, 6, n)),
            "b": pa.array(rng.randint(0, 6, n)),
            "c": pa.array(rng.randint(0, 9, n)),
        }
    )
    prof = shape.profile(table)
    joint = prof.tables["table"]["joint"]
    got = _internal(prof)["joint"]
    assert joint["sampled"] is True
    assert got["rows"] == joint["rows_analyzed"] == 5000
    assert got["method"] == "systematic" and got["seed"] == 7


def test_a_sampled_profile_records_the_internal_samples_of_the_sampled_rows() -> None:
    rng = np.random.RandomState(2)
    table = pa.table(
        {
            "x": pa.array(rng.normal(0, 1, 6000)),
            "s": pa.array([f"v{int(i)}" for i in rng.randint(0, 50, 6000)]),
        }
    )
    prof = shape.profile(table, sample=1500)
    got = _internal(prof)
    assert prof.tables["table"]["row_count"] == 1500
    assert "pattern_detection" in got and "distribution_fit" not in got


# --- every sampling code path writes the record ----------------------------------------------

# Where the profile code draws a sample, and what its record entry is called. A new draw fails
# ``test_every_sampling_path_is_registered`` until it is added here and to ``sampling.INTERNAL``
# (or it is the table sample itself).
_SAMPLERS = {
    "profile/reference/column.py::_pattern_sample_cached": "pattern_detection",
    "profile/reference/column.py::pattern_rates": "pattern_rates",
    "profile/reference/numerics.py::detect_distribution": "distribution_fit",
    "kernel/reference/fit.py::sample_for_fitting": "distribution_fit",
    "profile/joint/analyze.py::_sample_index": "joint",
    "profile/joint/measures.py::kendall_tau": "kendall_tau",
    "profile/joint/reference.py::measure_reference_pairs": "reference_pairs",
    "profile/sampling.py::select": "table",
}
# calls that look like a draw and are not one: ``linspace`` makes bin edges here, not row positions
_NOT_SAMPLES = {"profile/joint/measures.py::quantile_codes"}
_DRAWS = {"choice", "RandomState", "default_rng", "random_sample", "permutation", "shuffle",
          "linspace", "sample"}  # fmt: skip


def _sampling_sites() -> set[str]:
    found: set[str] = set()
    for path in [*(SRC / "profile").rglob("*.py"), *(SRC / "kernel").rglob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(SRC).as_posix()

        class V(ast.NodeVisitor):
            def __init__(self, rel: str) -> None:
                self.rel = rel
                self.stack: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_Call(self, node: ast.Call) -> None:
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                owner = ast.unparse(f.value) if isinstance(f, ast.Attribute) else ""
                if name in _DRAWS and (
                    "random" in owner
                    or name in {"default_rng", "RandomState"}
                    or owner.endswith("rng")
                    or name == "linspace"
                ):
                    where = self.stack[0] if self.stack else "<module>"
                    found.add(f"{self.rel}::{where}")
                self.generic_visit(node)

        V(rel).visit(tree)
    return found


def test_every_sampling_path_is_registered() -> None:
    sites = _sampling_sites()
    unregistered = sorted(s for s in sites if s not in _SAMPLERS and s not in _NOT_SAMPLES)
    assert not unregistered, (
        f"{unregistered} draw a sample: record it in the table's `sampling.internal` "
        "(sampling.INTERNAL) and add it to _SAMPLERS in this test"
    )
    assert set(_SAMPLERS) - sites == set(), (
        "a registered sampler no longer exists: update _SAMPLERS"
    )
    assert _NOT_SAMPLES <= sites, "a _NOT_SAMPLES entry no longer exists: remove it"


def test_every_registered_internal_analysis_has_a_registry_entry() -> None:
    names = {v for v in _SAMPLERS.values() if v != "table"}
    assert names == set(S.INTERNAL)


def test_the_registry_matches_the_constants_the_code_paths_use() -> None:
    from shape.kernel.reference import fit as kernel_fit
    from shape.profile.joint import measures, reference
    from shape.profile.reference import column

    assert S.FIT_SAMPLE_ROWS == kernel_fit.SAMPLE_SIZE == S.INTERNAL["distribution_fit"]["rows"]
    assert S.KENDALL_SAMPLE_ROWS == measures.kendall_tau.__defaults__[0]  # type: ignore[index]
    assert S.RATES_SAMPLE_ROWS == column._RATE_MAX_DISTINCT
    assert S.REFERENCE_PAIRS_ROWS == reference.MAX_ROWS
    assert S.PATTERN_SAMPLE_ROWS == S.INTERNAL["pattern_detection"]["rows"]
    assert S.INTERNAL["joint"]["seed"] == S.JOINT_JITTER_SEED


def test_reference_pairs_sample_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    import shape.profile.joint.reference as ref

    monkeypatch.setattr(ref, "MAX_ROWS", 50)
    rng = np.random.RandomState(4)
    table = pa.table(
        {
            "city": pa.array(rng.choice(["a", "b"], 300).tolist()),
            "zip": pa.array(rng.choice(["1", "2"], 300).tolist()),
        }
    )
    refs = pa.table({"city": ["a", "b"], "zip": ["1", "2"]})
    prof = shape.profile(table, reference_pairs=[{"columns": ["city", "zip"], "reference": refs}])
    got = _internal(prof)["reference_pairs"]
    assert got["method"] == "systematic" and got["rows"] == 50


# --- the same sample in both kernel modes ------------------------------------------------------

_PROFILE_SCRIPT = (
    "import json,sys,shape;"
    "p=shape.profile(sys.argv[1], sample=90, sample_seed=4, sample_method=sys.argv[2]);"
    "print(json.dumps(p.to_dict(), sort_keys=True, allow_nan=True))"
)


@pytest.mark.parametrize("method", ["random", "systematic", "head"])
def test_both_kernel_modes_choose_the_same_rows(tmp_path: Path, method: str) -> None:
    import os

    csv = write_orders_csv(tmp_path / "orders.csv")
    outs = []
    for kernel in ("python", "rust"):
        env = {**os.environ, "SHAPE_KERNEL": kernel}
        done = subprocess.run(
            [sys.executable, "-c", _PROFILE_SCRIPT, str(csv), method],
            capture_output=True, text=True, env=env, check=False,
        )  # fmt: skip
        if kernel == "rust" and "SHAPE_KERNEL=rust" in done.stderr and done.returncode:
            pytest.skip("the native kernel is not built")
        assert done.returncode == 0, done.stderr
        outs.append(json.loads(done.stdout))
    assert outs[0] == outs[1]


def test_the_saved_profile_keeps_the_record(tmp_path: Path) -> None:
    prof = shape.profile(orders_table(600), sample=100)
    path = tmp_path / "p.shape"
    shape.save(prof, path)
    assert shape.load(path).tables["table"]["sampling"] == prof.tables["table"]["sampling"]


def test_parquet_data_is_unchanged_by_a_sample_of_all_rows(tmp_path: Path) -> None:
    pqf = tmp_path / "o.parquet"
    pq.write_table(orders_table(100), pqf)
    assert shape.profile(str(pqf), sample=100).tables["o"]["sampling"]["method"] == "none"
