"""W7-04 item 5: ``design`` and ``design_from_data``."""

from __future__ import annotations

import csv
import json

import pytest
from data_1_1 import DESIGNS, retail_design, write_design

from shape.cli.main import main
from shape.design.ddl import DIALECTS
from shape.design.engine import MODES


def cli(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- design: the same bytes as the CLI ---------------------------------------------------------


@pytest.mark.parametrize("name", ["retail", "tiny", "keyless"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("dialect", DIALECTS)
def test_ddl_and_tables_are_byte_identical_to_the_clis_files(
    tmp_path, api11, capsys, name, mode, dialect
):
    path = write_design(tmp_path, name)
    sql, tables = tmp_path / "out.sql", tmp_path / "out.json"
    code, _, _ = cli(
        capsys, "design", path, "--mode", mode, "--dialect", dialect, "-o", sql, "--json", tables
    )
    result = api11.ok("design", input=str(path), mode=mode, dialect=dialect)
    if code != 0:
        assert result["passed"] is False and result["ddl"] is None and result["tables"] is None
        return
    assert result["passed"] is True
    assert result["ddl"].encode("utf-8") == sql.read_bytes()
    assert (json.dumps(result["tables"], indent=2, sort_keys=True) + "\n").encode() == (
        tables.read_bytes()
    )


@pytest.mark.parametrize("name", sorted(DESIGNS))
@pytest.mark.parametrize("mode", MODES)
def test_the_lint_report_is_the_clis_lint_json(tmp_path, api11, capsys, name, mode):
    path = write_design(tmp_path, name)
    code, out, _ = cli(capsys, "design", path, "--mode", mode, "--lint")
    result = api11.ok("design", input=str(path), mode=mode)
    assert result["lint"] == json.loads(out)
    assert result["passed"] is (code == 0)
    assert result["passed"] is not any(f["severity"] == "error" for f in result["lint"])


def test_schema_name_and_drop_are_applied(tmp_path, api11, capsys):
    path = write_design(tmp_path, "retail")
    sql = tmp_path / "x.sql"
    cli(capsys, "design", path, "--mode", "star", "--schema-name", "dw", "--drop", "-o", sql)
    result = api11.ok("design", input=str(path), mode="star", schema_name="dw", drop=True)
    assert result["ddl"] == sql.read_text(encoding="utf-8")
    assert "[dw].[fact_sales]" in result["ddl"] and "DROP TABLE" in result["ddl"]
    plain = api11.ok("design", input=str(path), mode="star")
    assert "DROP TABLE" not in plain["ddl"] and "[dw]" not in plain["ddl"]


def test_defaults_are_3nf_and_tsql(tmp_path, api11):
    path = str(write_design(tmp_path, "retail"))
    default = api11.ok("design", input=path)
    explicit = api11.ok("design", input=path, mode="3nf", dialect="tsql")
    assert default == explicit and default["tables"]["mode"] == "3nf"


def test_the_same_input_gives_the_same_bytes_every_time(tmp_path, api11):
    path = str(write_design(tmp_path, "retail"))
    runs = [api11.call("design", input=path, mode="snowflake") for _ in range(3)]
    assert len({json.dumps(r["result"], sort_keys=True) for r in runs}) == 1


def test_design_is_read_only(tmp_path, api11):
    path = write_design(tmp_path, "retail")
    before = sorted(p.name for p in tmp_path.iterdir()), path.read_bytes()
    api11.ok("design", input=str(path), mode="star", drop=True)
    api11.ok("design_from_data", source=str(_csv(tmp_path)))
    assert (sorted(p.name for p in tmp_path.iterdir()), path.read_bytes()) == (
        sorted([*before[0], "orders.csv"]),
        before[1],
    )


def test_a_design_with_errors_has_no_ddl_and_does_not_pass(tmp_path, api11, capsys):
    path = write_design(tmp_path, "failing")
    result = api11.ok("design", input=str(path), mode="star")
    assert result["passed"] is False and result["ddl"] is None and result["tables"] is None
    assert any(f["code"] == "D001" and f["severity"] == "error" for f in result["lint"])
    code, _, err = cli(capsys, "design", path, "--mode", "star")
    assert code == 1 and "D001" in err


def test_a_warning_alone_still_passes(tmp_path, api11):
    result = api11.ok("design", input=str(write_design(tmp_path, "keyless")))
    assert result["passed"] is True and any(f["severity"] == "warning" for f in result["lint"])
    assert result["ddl"].startswith("-- Schema design")


def test_a_large_ddl_goes_to_a_file(tmp_path, api11):
    path = str(write_design(tmp_path, "retail"))
    inline = api11.ok("design", input=path, mode="snowflake")["ddl"]
    response = api11.call("design", {"max_inline_bytes": 1024}, input=path, mode="snowflake")
    spilled = response["result"]["ddl"]
    assert spilled["spilled"] is True and spilled["bytes"] > 1024
    assert json.loads(open(spilled["path"]).read()) == inline
    assert "result_in_file" in [w["code"] for w in response["warnings"]]


# ---- design: errors ----------------------------------------------------------------------------


def test_design_errors(tmp_path, api11):
    api11.fail("design", "input.not_found", input=str(tmp_path / "none.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    api11.fail("design", "input.invalid_schema", input=str(bad))
    bad.write_text(json.dumps({"format": "other"}))
    api11.fail("design", "input.invalid_schema", input=str(bad))
    doc = retail_design()
    doc["entities"][0]["keys"] = [["no_such_attribute"]]
    bad.write_text(json.dumps(doc))
    error = api11.fail("design", "input.invalid_schema", input=str(bad))
    assert "no_such_attribute" in error["message"]
    bad.write_bytes(b"\xff\xfe")
    api11.fail("design", "input.invalid_schema", input=str(bad))
    doc = retail_design()
    doc["version"] = 2
    bad.write_text(json.dumps(doc))
    error = api11.fail("design", "input.unsupported_format_version", input=str(bad))
    assert "version 2" in error["message"] and error["hint"]
    doc["version"] = 0
    bad.write_text(json.dumps(doc))
    api11.fail("design", "input.invalid_schema", input=str(bad))
    api11.fail("design", "io.read_failed", input=str(tmp_path))


@pytest.mark.parametrize(
    "args",
    [{"mode": "galaxy"}, {"dialect": "oracle"}, {"drop": "yes"}, {"schema_name": 3}],
)
def test_design_refuses_bad_arguments(tmp_path, api11, args):
    api11.fail(
        "design", "usage.invalid_argument", input=str(write_design(tmp_path, "tiny")), **args
    )
    api11.fail("design", "usage.missing_argument")


def test_the_modes_and_dialects_are_the_engines():
    from shape.bridge.handlers import design

    assert design._MODES == MODES and design._DIALECTS == DIALECTS


# ---- design_from_data --------------------------------------------------------------------------


def _csv(folder):
    path = folder / "orders.csv"
    with open(path, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["order_id", "customer", "city", "country", "amount"])
        for i in range(60):
            city = ["Oslo", "Lima", "Kyiv"][i % 3]
            country = {"Oslo": "NO", "Lima": "PE", "Kyiv": "UA"}[city]
            out.writerow([i, f"c{i % 12}", city, country, f"{i * 1.5:.2f}"])
    return path


def test_from_data_is_the_document_the_cli_builds(tmp_path, api11, capsys):
    path = _csv(tmp_path)
    out = tmp_path / "design.json"
    code, _, _ = cli(capsys, "design", path, "--from-data", "--name", "orders", "-o", out)
    assert code == 0
    result = api11.ok("design_from_data", source=str(path), name="orders")
    assert result == json.loads(out.read_text())
    assert (json.dumps(result, indent=2, sort_keys=True) + "\n") == out.read_text()
    assert result["format"] == "shape-design" and result["version"] == 1
    assert result["entities"][0]["name"] == "orders"


def test_from_data_names_the_design_after_the_file_by_default(tmp_path, api11):
    result = api11.ok("design_from_data", source=str(_csv(tmp_path)))
    assert result["name"] == "orders"


def test_the_design_from_data_designs_a_schema(tmp_path, api11):
    path = tmp_path / "d.json"
    path.write_text(json.dumps(api11.ok("design_from_data", source=str(_csv(tmp_path)))))
    result = api11.ok("design", input=str(path))
    assert result["passed"] is True and "CREATE TABLE" in result["ddl"]


def test_from_data_errors(tmp_path, api11):
    api11.fail("design_from_data", "input.not_found", source=str(tmp_path / "none.csv"))
    (tmp_path / "x.txt").write_text("a,b\n1,2\n")
    api11.fail("design_from_data", "input.invalid_schema", source=str(tmp_path / "x.txt"))
    (tmp_path / "empty.csv").write_text("")
    api11.fail("design_from_data", "input.invalid_schema", source=str(tmp_path / "empty.csv"))
    api11.fail("design_from_data", "usage.missing_argument")
    api11.fail("design_from_data", "usage.invalid_argument", source=1)
