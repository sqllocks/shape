"""W5-09 items 1 to 3: ``shape fabric known-answer`` (alias ``shape known-answer``): a dataset whose
DAX measure results are known exactly, the planted totals, and the DAX queries.

Every expected number here comes from code that does not share anything with the generator of the
answers: pyarrow compute over the Parquet files the command wrote, and DuckDB where it is installed.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import pytest
from shape_fabric import known_answer

from shape.cli.main import main

pytestmark = pytest.mark.contract


SHOP = json.loads((Path(__file__).parent / "data" / "shop_schema.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shop_file(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("schema") / "shop.json"
    path.write_text(json.dumps(SHOP), encoding="utf-8")
    return str(path)


@pytest.fixture
def run(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def go(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    return go


@pytest.fixture(scope="module")
def built(tmp_path_factory, shop_file) -> Path:
    """The default build, made once: the tests that only read it share it."""
    out = tmp_path_factory.mktemp("built")
    code = main(["fabric", "known-answer", shop_file, "-o", str(out), "--seed", "5"])
    assert code == 0
    return out


def answers_of(directory: Path) -> dict:
    return json.loads((directory / "answers.json").read_text(encoding="utf-8"))


def tables_of(directory: Path) -> dict[str, pa.Table]:
    return {p.stem: pq.read_table(p) for p in sorted((directory / "data").glob("*.parquet"))}


def query(doc: dict, slice_: str | None) -> dict:
    return next(q for q in doc["queries"] if q["slice"] == slice_)


def values_of(q: dict, measure: str) -> dict[tuple, str]:
    return {tuple(r["key"]): r["values"][measure] for r in q["rows"] if measure in r["values"]}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- 1. the dataset, the model and the answers -------------------------------------------------


def test_the_command_writes_data_model_answers_and_queries(built):
    assert sorted(p.name for p in (built / "data").iterdir()) == [
        "category.parquet",
        "item.parquet",
    ]
    for name in ("model.bim", "answers.json", "queries.dax"):
        assert (built / name).is_file()
    doc = answers_of(built)
    assert doc["format"] == "shape-dax-answers"
    assert doc["version"] == 1 and isinstance(doc["version"], int)
    assert doc["source"] == {"domain": "shop", "scale": "small", "seed": 5}
    assert doc["tables"] == {"category": 6, "item": 300}
    model = json.loads((built / "model.bim").read_text(encoding="utf-8"))
    assert model["compatibilityLevel"] == 1604
    assert {t["name"] for t in model["model"]["tables"]} == {"category", "item"}


def test_the_parquet_holds_decimal_columns_with_the_declared_scale(built):
    item = tables_of(built)["item"]
    assert item.schema.field("price").type == pa.decimal128(10, 2)
    assert item.schema.field("discount").type == pa.decimal128(6, 2)
    assert item.schema.field("weight").type == pa.float64()
    assert item["discount"].null_count > 0


def test_the_default_measures_are_the_exporters_and_the_default_slices_are_the_first_text_columns(
    built,
):
    doc = answers_of(built)
    model = json.loads((built / "model.bim").read_text(encoding="utf-8"))
    exported = {
        (t["name"], m["name"]): m["expression"]
        for t in model["model"]["tables"]
        for m in t.get("measures", [])
    }
    assert {(m["table"], m["name"]): m["expression"] for m in doc["measures"]} == exported
    assert exported[("item", "Total Price")] == "SUM('item'[price])"
    # category is the only dimension (the parent of a relationship); its first text column is label
    assert [q["slice"] for q in doc["queries"]] == [None, "category.label"]
    assert [q["id"] for q in doc["queries"]] == ["q01", "q02"]


def test_a_measure_the_slice_cannot_filter_is_listed_as_skipped(run, tmp_path, shop_file):
    doc = json.loads(json.dumps(MEASURES))
    doc["slice_by"] = ["item.qty"]
    out = tmp_path / "o"
    code, _, err = run(
        "known-answer", shop_file, "--measures", write_measures(tmp_path, doc), "-o", str(out)
    )
    assert code == 0
    assert err.strip() == (
        "shape: 1 (measure, slice) pair(s) skipped; see `skipped` in answers.json"
    )
    answers = answers_of(out)
    q = query(answers, "item.qty")
    assert "item.Items" in q["measures"] and "category.Categories" not in q["measures"]
    (skip,) = answers["skipped"]
    assert skip["slice"] == "item.qty" and skip["measure"] == "category.Categories"
    assert "one-direction" in skip["reason"] or "relationship" in skip["reason"]


def test_the_grand_total_answers_match_pyarrow_compute_over_the_parquet(built):
    doc = answers_of(built)
    item = tables_of(built)["item"]
    (row,) = query(doc, None)["rows"]
    assert row["key"] == []
    values = row["values"]
    assert values["item.Item Count"] == str(item.num_rows)
    assert Decimal(values["item.Total Price"]) == pc.sum(item["price"]).as_py()
    assert Decimal(values["item.Total Discount"]) == pc.sum(item["discount"]).as_py()
    assert Decimal(values["item.Total Qty"]) == pc.sum(item["qty"]).as_py()
    nonnull = item.num_rows - item["discount"].null_count
    exact = Decimal(pc.sum(item["discount"]).as_py()) / nonnull
    assert Decimal(values["item.Avg Discount"]) == exact.quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_EVEN
    )
    assert row["fractions"]["item.Avg Discount"].count("/") == 1


def test_the_sliced_answers_match_pyarrow_compute_over_the_parquet(built):
    doc = answers_of(built)
    tables = tables_of(built)
    item, category = tables["item"], tables["category"]
    index = pc.index_in(item["category_id"], value_set=category["category_id"])
    label = pc.take(category["label"], index)
    joined = item.append_column("label", label)
    grouped = joined.group_by("label").aggregate(
        [("price", "sum"), ("item_id", "count"), ("qty", "sum"), ("price", "mean")]
    )
    q = query(doc, "category.label")
    count = values_of(q, "item.Item Count")
    total = values_of(q, "item.Total Price")
    assert len(count) == grouped.num_rows
    for r in grouped.to_pylist():
        key = (r["label"],)
        assert count[key] == str(r["item_id_count"])
        assert Decimal(total[key]) == r["price_sum"]
        assert Decimal(values_of(q, "item.Total Qty")[key]) == r["qty_sum"]
    # the dimension's own measure: one row per label value, counted from the dimension table
    own = values_of(q, "category.Category Count")
    by_label = {}
    for value in category["label"].to_pylist():
        by_label[value] = by_label.get(value, 0) + 1
    assert {k[0]: int(v) for k, v in own.items()} == by_label


def test_the_answers_match_duckdb_where_it_is_installed(built):
    duckdb = pytest.importorskip("duckdb")
    doc = answers_of(built)
    con = duckdb.connect()
    for name in ("category", "item"):
        path = built / "data" / f"{name}.parquet"
        con.execute(f"create view {name} as select * from read_parquet('{path}')")
    q = query(doc, "category.label")
    rows = con.execute(
        "select c.label, count(*), sum(i.price), sum(i.qty), sum(i.discount) "
        "from item i left join category c on i.category_id = c.category_id group by c.label"
    ).fetchall()
    count = values_of(q, "item.Item Count")
    assert {(r[0],) for r in rows} == set(count)
    for label, n, price, qty, discount in rows:
        key = (label,)
        assert count[key] == str(n)
        assert Decimal(values_of(q, "item.Total Price")[key]) == Decimal(str(price))
        assert Decimal(values_of(q, "item.Total Qty")[key]) == Decimal(str(qty))
        assert Decimal(values_of(q, "item.Total Discount")[key]) == Decimal(str(discount))


def test_the_same_inputs_give_byte_identical_files(run, tmp_path, shop_file):
    a, b = tmp_path / "a", tmp_path / "b"
    for out in (a, b):
        code, _, err = run("known-answer", shop_file, "--seed", "5", "-o", str(out))
        assert (code, err) == (0, "")
    for name in ("answers.json", "queries.dax", "model.bim"):
        assert sha(a / name) == sha(b / name), name


def test_a_different_seed_gives_different_answers(run, tmp_path, shop_file):
    a, b = tmp_path / "a", tmp_path / "b"
    assert run("known-answer", shop_file, "--seed", "5", "-o", str(a))[0] == 0
    assert run("known-answer", shop_file, "--seed", "6", "-o", str(b))[0] == 0
    assert sha(a / "answers.json") != sha(b / "answers.json")


def test_the_scale_option_picks_the_preset(run, tmp_path, shop_file):
    doc = json.loads(json.dumps(SHOP))
    doc["generation"]["scales"]["tiny"] = {"category": 3, "item": 40}
    path = tmp_path / "tiny.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, _, err = run("known-answer", str(path), "--scale", "tiny", "-o", str(tmp_path / "o"))
    assert (code, err) == (0, "")
    assert answers_of(tmp_path / "o")["tables"] == {"category": 3, "item": 40}
    code, _, err = run("known-answer", str(path), "--scale", "nonesuch", "-o", str(tmp_path / "p"))
    assert code == 2 and "nonesuch" in err


def test_a_domain_works_and_an_unknown_one_exits_2(run, tmp_path):
    code, _, err = run(
        "known-answer", "retail", "--scale", "fabric_demo", "-o", str(tmp_path / "r")
    )
    doc = answers_of(tmp_path / "r")
    # the stderr line carries the number of pairs left out of the answers
    assert code == 0 and err == (
        f"shape: {len(doc['skipped'])} (measure, slice) pair(s) skipped; "
        "see `skipped` in answers.json\n"
    )
    assert doc["source"]["domain"] == "retail"
    assert doc["tables"]["customer"] == 200
    # two tables hold a measure of one name; the ids (and the queries) keep them apart
    ids = {m["id"] for m in doc["measures"]}
    assert {"product.Total Unit Price", "order_line.Total Unit Price"} <= ids
    code, _, err = run("known-answer", "nonesuch", "-o", str(tmp_path / "x"))
    assert code == 2 and "nonesuch" in err


def test_the_fabric_group_and_the_top_level_command_are_the_same(run, tmp_path, shop_file):
    a, b = tmp_path / "a", tmp_path / "b"
    assert run("fabric", "known-answer", shop_file, "--seed", "5", "-o", str(a))[0] == 0
    assert run("known-answer", shop_file, "--seed", "5", "-o", str(b))[0] == 0
    assert sha(a / "answers.json") == sha(b / "answers.json")


def test_the_output_folder_is_required(run, shop_file):
    code, _, err = run("known-answer", shop_file)
    assert code == 2


# ---- the measures file -------------------------------------------------------------------------

MEASURES = {
    "format": "shape-dax-measures",
    "version": 1,
    "measures": [
        {"name": "Items", "table": "item", "aggregation": "count"},
        {"name": "Revenue", "table": "item", "aggregation": "sum", "column": "price"},
        {"name": "Cheapest", "table": "item", "aggregation": "min", "column": "price"},
        {"name": "Priciest", "table": "item", "aggregation": "max", "column": "price"},
        {"name": "Mean Price", "table": "item", "aggregation": "avg", "column": "price"},
        {
            "name": "Revenue per Item",
            "table": "item",
            "aggregation": "ratio",
            "numerator": "Revenue",
            "denominator": "Items",
        },
        {"name": "Categories", "table": "category", "aggregation": "count"},
    ],
    "slice_by": ["category.label", "category.region"],
}


def write_measures(tmp_path, doc=None, name="measures.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(MEASURES if doc is None else doc), encoding="utf-8")
    return str(path)


def test_a_measures_file_replaces_the_defaults_and_adds_its_slices(run, tmp_path, shop_file):
    code, _, err = run(
        "known-answer",
        shop_file,
        "--measures",
        write_measures(tmp_path),
        "--seed",
        "5",
        "-o",
        str(tmp_path / "o"),
    )
    assert (code, err) == (0, "")
    out = tmp_path / "o"
    doc = answers_of(out)
    assert [q["slice"] for q in doc["queries"]] == [None, "category.label", "category.region"]
    assert [m["id"] for m in doc["measures"]] == [
        "item.Items",
        "item.Revenue",
        "item.Cheapest",
        "item.Priciest",
        "item.Mean Price",
        "item.Revenue per Item",
        "category.Categories",
    ]
    model = json.loads((out / "model.bim").read_text(encoding="utf-8"))
    item = next(t for t in model["model"]["tables"] if t["name"] == "item")
    expressions = {m["name"]: m["expression"] for m in item["measures"]}
    assert expressions["Revenue"] == "SUM('item'[price])"
    assert expressions["Cheapest"] == "MIN('item'[price])"
    assert expressions["Revenue per Item"] == "DIVIDE('item'[Revenue], 'item'[Items])"
    tables = tables_of(out)["item"]
    (row,) = query(doc, None)["rows"]
    assert Decimal(row["values"]["item.Cheapest"]) == pc.min(tables["price"]).as_py()
    assert Decimal(row["values"]["item.Priciest"]) == pc.max(tables["price"]).as_py()
    ratio = Decimal(pc.sum(tables["price"]).as_py()) / tables.num_rows
    assert Decimal(row["values"]["item.Revenue per Item"]) == ratio.quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_EVEN
    )


def test_a_null_attribute_is_a_blank_group_in_the_answers(run, tmp_path, shop_file):
    out = tmp_path / "o"
    code, _, _ = run(
        "known-answer",
        shop_file,
        "--measures",
        write_measures(tmp_path),
        "--seed",
        "5",
        "-o",
        str(out),
    )
    assert code == 0
    doc = answers_of(out)
    q = query(doc, "category.region")
    category = tables_of(out)["category"]
    keys = {r["key"][0] for r in q["rows"]}
    has_null = category["region"].null_count > 0
    assert (None in keys) == has_null
    assert q["slice_type"] == "string"


def test_a_measures_file_is_checked(run, tmp_path, shop_file):
    def bad(mutate, expect):
        doc = json.loads(json.dumps(MEASURES))
        mutate(doc)
        path = write_measures(tmp_path, doc, "bad.json")
        code, _, err = run("known-answer", shop_file, "--measures", path, "-o", str(tmp_path / "o"))
        assert code == 2, err
        assert expect in err, err

    bad(lambda d: d.update(format="other"), "shape-dax-measures")
    bad(lambda d: d.update(version=2), "version 2")
    bad(lambda d: d.update(version="1"), "version")
    bad(lambda d: d["measures"][0].update(table="nonesuch"), "nonesuch")
    bad(lambda d: d["measures"][1].update(column="nonesuch"), "nonesuch")
    bad(lambda d: d["measures"][1].pop("column"), "column")
    bad(lambda d: d["measures"][0].update(aggregation="median"), "median")
    bad(lambda d: d["measures"][0].update(extra=1), "extra")
    bad(lambda d: d["measures"][1].update(name="Items"), "Items")
    bad(lambda d: d["measures"][5].update(numerator="Nothing"), "Nothing")
    bad(lambda d: d["measures"][5].update(denominator="Revenue per Item"), "Revenue per Item")
    bad(lambda d: d["slice_by"].append("category.nonesuch"), "category.nonesuch")
    bad(lambda d: d["slice_by"].append("nonesuch.label"), "nonesuch.label")
    bad(lambda d: d["slice_by"].append("category.label"), "category.label")
    bad(lambda d: d["slice_by"].append("label"), "label")
    bad(lambda d: d.update(measures=[]), "measures")


def test_a_measures_file_that_is_not_json_exits_2(run, tmp_path, shop_file):
    path = tmp_path / "m.json"
    path.write_text("{not json", encoding="utf-8")
    code, _, err = run(
        "known-answer", shop_file, "--measures", str(path), "-o", str(tmp_path / "o")
    )
    assert code == 2 and "m.json" in err
    code, _, err = run(
        "known-answer", shop_file, "--measures", str(tmp_path / "missing.json"), "-o", "o"
    )
    assert code == 2


def test_a_numeric_aggregation_of_a_text_column_is_refused(run, tmp_path, shop_file):
    doc = json.loads(json.dumps(MEASURES))
    doc["measures"].append(
        {"name": "Label Sum", "table": "category", "aggregation": "sum", "column": "label"}
    )
    path = write_measures(tmp_path, doc)
    code, _, err = run("known-answer", shop_file, "--measures", path, "-o", str(tmp_path / "o"))
    assert code == 2 and "numeric" in err and "label" in err


def scaled_schema(tmp_path, name, **sizes):
    doc = json.loads(json.dumps(SHOP))
    doc["generation"]["scales"]["custom"] = sizes
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path, doc


def test_exact_and_inexact_measures_are_flagged(built):
    flags = {m["id"]: m["exact"] for m in answers_of(built)["measures"]}
    assert flags["item.Item Count"] and flags["item.Total Price"] and flags["item.Total Qty"]
    assert not flags["item.Avg Price"]
    assert not flags["item.Total Weight"]  # a float column: its sum is rounded to the places
    assert answers_of(built)["places"] == 6


def test_a_group_with_no_rows_is_not_in_the_answers_and_a_null_is_the_blank_group(run, tmp_path):
    path, doc = scaled_schema(tmp_path, "many.json", category=40, item=12)
    measures = {
        "format": "shape-dax-measures",
        "version": 1,
        "measures": [{"name": "Items", "table": "item", "aggregation": "count"}],
        "slice_by": ["category.category_id", "category.region"],
    }
    mpath = write_measures(tmp_path, measures)
    out = tmp_path / "o"
    code, _, err = run(
        "known-answer", str(path), "--scale", "custom", "--measures", mpath, "-o", str(out)
    )
    assert (code, err) == (0, "")
    answers = answers_of(out)
    tables = tables_of(out)
    used = set(tables["item"]["category_id"].to_pylist())
    by_id = query(answers, "category.category_id")
    assert by_id["slice_type"] == "integer"
    assert {r["key"][0] for r in by_id["rows"]} == {str(i) for i in used}
    assert len(by_id["rows"]) < 40
    # the blank group of a nullable attribute holds the items of the categories with no region
    region = dict(
        zip(
            tables["category"]["category_id"].to_pylist(),
            tables["category"]["region"].to_pylist(),
            strict=True,
        )
    )
    expected: dict = {}
    for cid in tables["item"]["category_id"].to_pylist():
        expected[region[cid]] = expected.get(region[cid], 0) + 1
    got = {
        r["key"][0]: int(r["values"]["item.Items"])
        for r in query(answers, "category.region")["rows"]
    }
    assert got == expected
    assert None in got  # with 40 categories and a null rate of 0.2, some have no region


def test_a_measure_with_two_relationship_paths_to_the_slice_is_skipped(run, tmp_path):
    path, doc = scaled_schema(tmp_path, "twice.json", category=6, item=50)
    doc["relationships"].append(dict(doc["relationships"][0], name="item_category_again"))
    path.write_text(json.dumps(doc), encoding="utf-8")
    measures = json.loads(json.dumps(MEASURES))
    measures["slice_by"] = ["category.label"]
    out = tmp_path / "o"
    code, _, err = run(
        "known-answer",
        str(path),
        "--scale",
        "custom",
        "--measures",
        write_measures(tmp_path, measures),
        "-o",
        str(out),
    )
    answers = answers_of(out)
    assert code == 0 and err.startswith(f"shape: {len(answers['skipped'])} (measure, slice)")
    skipped = {s["measure"]: s["reason"] for s in answers["skipped"]}
    assert "item.Items" in skipped and "2 relationship paths" in skipped["item.Items"]
    assert "ratio" not in "".join(skipped) or "item.Revenue per Item" in skipped
    assert query(answers, "category.label")["measures"] == ["category.Categories"]


def test_a_slice_through_two_relationships_follows_the_chain(run, tmp_path):
    # order_line -> order -> customer: a measure of the grandchild sliced by the grandparent
    measures = {
        "format": "shape-dax-measures",
        "version": 1,
        "measures": [{"name": "Lines", "table": "order_line", "aggregation": "count"}],
        "slice_by": ["store.store_type"],
    }
    out = tmp_path / "o"
    code, _, err = run(
        "known-answer",
        "retail",
        "--scale",
        "fabric_demo",
        "--measures",
        write_measures(tmp_path, measures),
        "-o",
        str(out),
    )
    assert (code, err) == (0, "")
    answers = answers_of(out)
    tables = tables_of(out)
    q = query(answers, "store.store_type")
    lines = tables["order_line"]
    order_customer = dict(
        zip(
            tables["order"]["order_id"].to_pylist(),
            tables["order"]["store_id"].to_pylist(),
            strict=True,
        )
    )
    tier = dict(
        zip(
            tables["store"]["store_id"].to_pylist(),
            tables["store"]["store_type"].to_pylist(),
            strict=True,
        )
    )
    expected: dict = {}
    for oid in lines["order_id"].to_pylist():
        key = tier.get(order_customer.get(oid))
        expected[key] = expected.get(key, 0) + 1
    assert {r["key"][0]: int(r["values"]["order_line.Lines"]) for r in q["rows"]} == expected


# ---- 2. planted totals -------------------------------------------------------------------------


def nonnull_count(directory: Path, table: str, column: str) -> int:
    t = tables_of(directory)[table]
    return t.num_rows - t[column].null_count


@pytest.fixture
def shop(shop_file, run):
    def go(tmp_path, *plants, name="p", extra=()):
        out = tmp_path / name
        argv = ["known-answer", shop_file, "--seed", "5", *extra]
        for p in plants:
            argv += ["--plant", p]
        code, _, err = run(*argv, "-o", str(out))
        return code, err, out

    return go


def test_a_plant_sets_the_grand_total_exactly(shop, tmp_path):
    code, err, out = shop(tmp_path, "item.price=1234.56")
    assert (code, err) == (0, "")
    price = tables_of(out)["item"]["price"]
    assert pc.sum(price).as_py() == Decimal("1234.56")
    doc = answers_of(out)
    assert doc["plants"] == [{"table": "item", "column": "price", "total": "1234.56"}]
    (row,) = query(doc, None)["rows"]
    assert row["values"]["item.Total Price"] == "1234.56"
    # the planted values stay inside the generator's bounds (uniform 1 to 10)
    assert pc.min(price).as_py() >= Decimal("1.00") and pc.max(price).as_py() <= Decimal("10.00")


def test_the_planted_total_reaches_the_sliced_answers_too(shop, tmp_path):
    code, err, out = shop(tmp_path, "item.price=2000")
    assert code == 0, err
    doc = answers_of(out)
    q = query(doc, "category.label")
    assert sum(Decimal(v) for v in values_of(q, "item.Total Price").values()) == Decimal("2000.00")


def test_a_plant_is_deterministic(shop, tmp_path):
    _, _, a = shop(tmp_path, "item.price=1500", name="a")
    _, _, b = shop(tmp_path, "item.price=1500", name="b")
    assert tables_of(a)["item"].equals(tables_of(b)["item"])
    assert sha(a / "answers.json") == sha(b / "answers.json")


def test_a_plant_moves_values_without_reordering_or_nulling_them(shop, tmp_path):
    _, _, plain = shop(tmp_path, name="plain")
    _, _, planted = shop(tmp_path, "item.discount=100", name="planted")
    before = tables_of(plain)["item"]["discount"]
    after = tables_of(planted)["item"]["discount"]
    assert [v is None for v in before.to_pylist()] == [v is None for v in after.to_pylist()]
    assert pc.sum(after).as_py() == Decimal("100.00")
    assert pc.min(after).as_py() >= Decimal("0.00") and pc.max(after).as_py() <= Decimal("0.50")


def test_the_bounds_of_the_reachable_range_succeed(shop, tmp_path):
    _, _, plain = shop(tmp_path, name="plain")
    n = nonnull_count(plain, "item", "price")  # 300, no nulls
    low, high = Decimal(n) * Decimal("1.00"), Decimal(n) * Decimal("10.00")
    code, err, out = shop(tmp_path, f"item.price={low}", name="low")
    assert (code, err) == (0, "")
    prices = tables_of(out)["item"]["price"].to_pylist()
    assert set(prices) == {Decimal("1.00")}
    code, err, out = shop(tmp_path, f"item.price={high}", name="high")
    assert (code, err) == (0, "")
    assert set(tables_of(out)["item"]["price"].to_pylist()) == {Decimal("10.00")}


def test_just_past_the_bounds_exits_1_and_names_the_range(shop, tmp_path):
    _, _, plain = shop(tmp_path, name="plain")
    n = nonnull_count(plain, "item", "price")
    for requested, name in (
        (Decimal(n) * 10 + Decimal("0.01"), "above"),
        (Decimal(n) - Decimal("0.01"), "below"),
    ):
        code, err, out = shop(tmp_path, f"item.price={requested}", name=name)
        assert code == 1, err
        assert "item.price" in err
        assert str(requested) in err
        assert f"{Decimal(n):.2f}" in err and f"{Decimal(n) * 10:.2f}" in err
        assert not (out / "answers.json").exists()


def test_the_reachable_range_counts_only_the_non_null_values(shop, tmp_path):
    _, _, plain = shop(tmp_path, name="plain")
    n = nonnull_count(plain, "item", "discount")
    assert n < 300  # discount is nullable
    top = Decimal(n) * Decimal("0.50")
    code, err, _ = shop(tmp_path, f"item.discount={top}", name="top")
    assert (code, err) == (0, "")
    code, err, _ = shop(tmp_path, f"item.discount={top + Decimal('0.01')}", name="past")
    assert code == 1 and f"{top:.2f}" in err


def test_more_than_one_plant_and_an_unplanted_column_stays_as_generated(shop, tmp_path):
    _, _, plain = shop(tmp_path, name="plain")
    code, err, out = shop(tmp_path, "item.price=1800", "item.discount=20", name="two")
    assert (code, err) == (0, "")
    item = tables_of(out)["item"]
    assert pc.sum(item["price"]).as_py() == Decimal("1800.00")
    assert pc.sum(item["discount"]).as_py() == Decimal("20.00")
    assert item["qty"].equals(tables_of(plain)["item"]["qty"])
    assert [p["column"] for p in answers_of(out)["plants"]] == ["price", "discount"]


def test_a_plant_that_is_already_the_total_changes_nothing(shop, tmp_path):
    _, _, plain = shop(tmp_path, name="plain")
    total = pc.sum(tables_of(plain)["item"]["price"]).as_py()
    code, err, out = shop(tmp_path, f"item.price={total}", name="same")
    assert (code, err) == (0, "")
    assert tables_of(out)["item"]["price"].equals(tables_of(plain)["item"]["price"])


@pytest.mark.parametrize(
    ("spec", "needle"),
    [
        ("item.nonesuch=5", "nonesuch"),
        ("nonesuch.price=5", "nonesuch"),
        ("item.weight=5", "decimal"),  # a float column
        ("item.qty=5", "decimal"),  # an integer column
        ("price=5", "TABLE.COLUMN"),
        ("item.price", "TABLE.COLUMN=VALUE"),
        ("item.price=abc", "abc"),
        ("item.price=NaN", "NaN"),
        ("item.price=Infinity", "Infinity"),
    ],
)
def test_a_malformed_plant_exits_2(shop, tmp_path, spec, needle):
    code, err, out = shop(tmp_path, spec)
    assert code == 2, err
    assert needle in err


def test_the_same_column_planted_twice_exits_2(shop, tmp_path):
    code, err, _ = shop(tmp_path, "item.price=1000", "item.price=1100")
    assert code == 2 and "item.price" in err


def test_a_total_with_more_places_than_the_column_has_exits_1(shop, tmp_path):
    code, err, _ = shop(tmp_path, "item.price=1000.005")
    assert code == 1 and "1000.005" in err and "2 decimal places" in err


def test_the_unit_helper_spreads_a_difference_within_the_headroom():
    values = [100, 250, 999, 1000]
    out = known_answer.spread(values, 3349, 100, 1000)
    assert sum(out) == 3349 and all(100 <= v <= 1000 for v in out)
    assert known_answer.spread(values, 4000, 100, 1000) == [1000] * 4
    assert known_answer.spread(values, 400, 100, 1000) == [100] * 4
    assert known_answer.spread([], 0, 0, 10) == []
    with pytest.raises(known_answer.PlantError):
        known_answer.spread(values, 4001, 100, 1000)


@pytest.mark.parametrize("target", [3333, 3334, 4999, 1, 7001, 9000])
def test_the_spread_is_exact_for_any_reachable_total(target):
    values = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5] * 7
    low, high = 0, 1000
    out = known_answer.spread(values, target, low, high)
    assert sum(out) == target
    assert all(low <= v <= high for v in out)


# ---- 3. DAX queries ---------------------------------------------------------------------------


def test_queries_dax_has_one_evaluate_per_answers_query(built):
    text = (built / "queries.dax").read_text(encoding="utf-8")
    doc = answers_of(built)
    assert text.count("EVALUATE") == len(doc["queries"])
    assert "// q01" in text and "// q02 category.label" in text
    assert "SUMMARIZECOLUMNS(\n    'category'[label]," in text
    assert "\"item.Total Price\", 'item'[Total Price]" in text
    assert "ORDER BY 'category'[label]" in text
    # the grand total has no grouping column
    first = text.split("// q02")[0]
    assert 'SUMMARIZECOLUMNS(\n    "' in first


def test_a_name_that_reaches_dax_is_quoted(run, tmp_path):
    assert known_answer.dax_string('a"b') == '"a""b"'
    assert known_answer.dax_string("plain") == '"plain"'
