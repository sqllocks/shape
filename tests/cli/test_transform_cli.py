"""P6-06: ``shape transform star|cdm`` end to end, for retail (needs the shape-domains plugin)."""

from __future__ import annotations

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

pytest.importorskip("shape_domains")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture(scope="module")
def retail_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("retail")
    assert main(["generate", "retail", "--scale", "small", "-f", "parquet", "-o", str(root)]) == 0
    return root


def test_star_retail_writes_dimensions_date_dimension_and_facts(capsys, tmp_path):
    code, out, err = run(capsys, "transform", "star", "retail", "-o", tmp_path, "--json")
    assert code == 0
    info = json.loads(out)
    assert list(info["tables"]) == [
        "dim_customer",
        "dim_product",
        "dim_store",
        "dim_promotion",
        "dim_date",
        "fact_sale",
        "fact_return",
    ]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(info["files"])
    assert info["tables"]["fact_sale"]["rows"] == 12500
    assert "warning" not in err  # null promotion ids are not orphans


def test_star_dimension_keys_and_date_coverage(capsys, tmp_path):
    run(capsys, "transform", "star", "retail", "-o", tmp_path, "--format", "parquet")
    sale = pq.read_table(tmp_path / "fact_sale.parquet")
    dim_date = pq.read_table(tmp_path / "dim_date.parquet")
    assert {"nk_customer_id", "sk_customer", "sk_product", "sk_store", "sk_date"} <= set(
        sale.column_names
    )
    assert "customer_id" not in sale.column_names
    dates = set(dim_date["sk_date"].to_pylist())
    assert all(k in dates for k in sale["sk_date"].to_pylist() if k is not None)
    for dim, key in (("dim_customer", "sk_customer"), ("dim_product", "sk_product")):
        t = pq.read_table(tmp_path / f"{dim}.parquet")
        assert t[key].to_pylist() == list(range(1, t.num_rows + 1))
        assert (
            sale[key].drop_null().to_pylist()
            and max(sale[key].drop_null().to_pylist()) <= t.num_rows
        )


def test_star_from_a_directory_needs_a_map(capsys, retail_dir, tmp_path):
    code, _, err = run(capsys, "transform", "star", retail_dir, "-o", tmp_path / "x")
    assert code == 2
    assert "no star mapping" in err and "--map" in err
    star_map = {
        "dimensions": {
            "dim_store": {"source": "store", "key": "sk_store", "natural_key": "store_id"}
        },
        "facts": {
            "fact_order": {
                "source": "order",
                "dimension_keys": {"store_id": "dim_store"},
                "date_columns": ["order_date"],
            }
        },
    }
    (tmp_path / "map.json").write_text(json.dumps(star_map))
    code, out, _ = run(
        capsys,
        "transform",
        "star",
        retail_dir,
        "--map",
        tmp_path / "map.json",
        "-o",
        tmp_path / "y",
        "--format",
        "csv",
        "--json",
    )
    assert code == 0
    assert sorted(json.loads(out)["tables"]) == ["dim_date", "dim_store", "fact_order"]
    assert (tmp_path / "y/fact_order.csv").read_text().startswith('"order_id"')


def test_star_bad_map_is_a_clean_error(capsys, retail_dir, tmp_path):
    (tmp_path / "map.json").write_text(json.dumps({"facts": {"f": {"source": "nope"}}}))
    code, _, err = run(
        capsys,
        "transform",
        "star",
        retail_dir,
        "--map",
        tmp_path / "map.json",
        "-o",
        tmp_path / "o",
    )
    assert code == 2 and "no table named 'nope'" in err
    code, _, err = run(
        capsys,
        "transform",
        "star",
        retail_dir,
        "--map",
        tmp_path / "gone.json",
        "-o",
        tmp_path / "o",
    )
    assert code == 2 and "cannot read the map" in err


def test_cdm_retail_folder(capsys, tmp_path):
    code, out, _ = run(capsys, "transform", "cdm", "retail", "-o", tmp_path, "--json")
    assert code == 0
    info = json.loads(out)
    assert info["model"] == "ShapeRetail"
    assert "Contact" in info["entities"] and "SalesOrderProduct" in info["entities"]
    model = json.loads((tmp_path / "model.json").read_text())
    assert [e["name"] for e in model["entities"]] == info["entities"]
    assert (tmp_path / "Contact/Contact.csv").is_file()


def test_cdm_options(capsys, retail_dir, tmp_path):
    (tmp_path / "e.json").write_text(json.dumps({"entities": {"customer": "Person"}}))
    code, out, _ = run(
        capsys,
        "transform",
        "cdm",
        retail_dir,
        "--map",
        tmp_path / "e.json",
        "--model-name",
        "Mine",
        "-o",
        tmp_path / "o",
        "--format",
        "parquet",
        "--json",
    )
    assert code == 0
    info = json.loads(out)
    assert info["model"] == "Mine" and "Person" in info["entities"]
    assert "OrderLine" in info["entities"]  # unnamed tables are PascalCase
    assert pq.read_table(tmp_path / "o/Person/Person.parquet").num_rows == 1000


def test_unknown_source_and_missing_output(capsys, tmp_path):
    code, _, err = run(capsys, "transform", "star", "no_such_domain", "-o", tmp_path)
    assert code == 2 and "no_such_domain" in err
    with pytest.raises(SystemExit) as exc:
        main(["transform", "star", "retail"])
    assert exc.value.code == 2


def test_there_are_no_alternative_command_names(capsys):
    for name in ("to-star", "to-cdm"):
        with pytest.raises(SystemExit) as exc:
            main([name, "retail", "-o", "x"])
        assert exc.value.code == 2


def test_builtin_transforms_through_the_plugin_host():
    from shape.plugins.host import default_host

    host = default_host()
    assert {"star", "cdm"} <= set(host.names("shape.transforms"))
    cdm = host.get("shape.transforms", "cdm")
    tables = {"order_line": pa.table({"a": [1]}), "store": pa.table({"a": [1]})}
    assert list(cdm.apply(tables, entities={"store": "Shop"})) == ["OrderLine", "Shop"]
    star = host.get("shape.transforms", "star")
    with pytest.raises(ValueError, match="needs a star map"):
        star.apply(tables)
    out = star.apply(
        tables,
        map={
            "dimensions": {"dim_store": {"source": "store", "key": "sk_store", "natural_key": "a"}}
        },
    )
    assert list(out) == ["dim_store"]
