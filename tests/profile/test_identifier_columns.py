"""Issue #46: CSV identifier columns (ZIP, NDC, NPI, member numbers) keep their leading zeros.

A column of digits with leading zeros, or of one fixed width of five or more digits under an
identifier name, is text when profiled, learned from, generated and written. Every test here
failed before the fix (the column was an integer and ``02134`` became 2134)."""

from __future__ import annotations

import json
import random
import warnings
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.io import CsvOptions, open_source, read_table
from shape.io.identifiers import (
    DigitStats,
    name_strongly_suggests_identifier,
    name_suggests_identifier,
)
from shape.kernel import dispatch
from shape.profile.engine import EngineOptions, profile_table


@pytest.fixture(params=["python", "rust"])
def kernel(request, monkeypatch):
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


ZIPS = ["02134", "01002", "98101", "00000", "10001", "06510"]


def _csv(tmp_path: Path, text: str, name: str = "t.csv") -> str:
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def _summary(path: str, **kw):
    return shape.profile(path, name="t", **kw).summary()["columns"]


# ---- the filed repro --------------------------------------------------------------------------


def test_the_filed_repros(tmp_path, kernel):
    a = _csv(tmp_path, "zip,amount\n" + "02134,10\n01002,20\n98101,30\n" * 40)
    c = _csv(tmp_path, "zip\n" + "02134\n98101\n" * 100, "c.csv")
    d = _csv(tmp_path, "zip\n" + "02134\n98101\nUNKNOWN\n" * 40, "d.csv")
    for path in (a, c, d):
        col = _summary(path)["zip"]
        assert col["dtype"] == "string"
    assert _summary(a)["zip"]["min"] == "01002"
    assert _summary(c)["zip"]["min"] == "02134"
    assert _summary(a)["amount"]["dtype"] == "integer"  # a real number stays a number


def test_the_placeholder_is_not_zero(tmp_path, kernel):
    rows = ["zip"] + [random.Random(i).choice(ZIPS) for i in range(500)]
    path = _csv(tmp_path, "\n".join(rows) + "\n")
    col = shape.profile(path).to_dict()["columns"]["zip"]
    assert col["dtype"] == "string"
    assert col["min_value"][-1] == "00000"
    counts = col["value_counts_ext"]
    assert "00000" in counts and "0" not in counts
    assert set(counts) == set(ZIPS)


def test_a_profile_saved_and_loaded_keeps_the_text(tmp_path, kernel):
    path = _csv(tmp_path, "zip\n" + "02134\n98101\n" * 50)
    out = tmp_path / "z.shape"
    shape.save(shape.profile(path), out)
    col = shape.load(out).to_dict()["columns"]["zip"]
    assert col["dtype"] == "string" and set(col["enum_values"]) == {"02134", "98101"}


# ---- the rule -----------------------------------------------------------------------------------


def test_leading_zeros_decide_whatever_the_name(tmp_path, kernel):
    path = _csv(tmp_path, "score,n\n" + "007,1\n042,2\n100,3\n" * 20)
    cols = _summary(path)
    assert cols["score"]["dtype"] == "string"
    assert cols["n"]["dtype"] == "integer"


def test_a_bare_zero_and_decimals_are_numbers(tmp_path, kernel):
    path = _csv(tmp_path, "a,b,c\n" + "0,0.5,10\n1,0.25,20\n2,0.75,30\n" * 20)
    cols = _summary(path)
    assert [cols[k]["dtype"] for k in "abc"] == ["integer", "float", "integer"]


def test_fixed_width_with_an_identifier_name_is_text(tmp_path, kernel):
    rng = random.Random(3)
    lines = ["npi,member_id,patient_number,zip"]
    for _ in range(300):
        lines.append(
            f"{rng.randint(10**9, 2 * 10**9 - 1)},{rng.randint(10**7, 10**8 - 1)},"
            f"{rng.randint(10**5, 10**6 - 1)},{rng.randint(10000, 99999)}"
        )
    cols = _summary(_csv(tmp_path, "\n".join(lines) + "\n"))
    assert {k: v["dtype"] for k, v in cols.items()} == dict.fromkeys(
        ("npi", "member_id", "patient_number", "zip"), "string"
    )


def test_numbers_that_merely_have_a_name_or_a_width_stay_numbers(tmp_path, kernel):
    rng = random.Random(4)
    lines = ["customer_id,year,rating,reading,order_code"]
    for i in range(300):
        lines.append(
            f"{i + 1},{rng.choice([2019, 2020, 2021])},{rng.randint(1, 5)},"
            f"{rng.randint(10**5, 10**6 - 1)},{rng.randint(1, 9999)}"
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cols = _summary(_csv(tmp_path, "\n".join(lines) + "\n"))
    assert all(v["dtype"] == "integer" for v in cols.values()), cols


def test_a_suspicious_integer_column_warns_and_names_the_option(tmp_path, kernel):
    rng = random.Random(5)
    lines = ["phone,reading,amount"] + [
        f"{rng.randint(10, 99)},{rng.randint(10**5, 10**6 - 1)},{rng.randint(1, 999)}"
        for _ in range(200)
    ]
    path = _csv(tmp_path, "\n".join(lines) + "\n")
    with pytest.warns(UserWarning, match="--string-columns") as caught:
        cols = _summary(path)
    message = str(caught[0].message)
    assert "'phone'" in message and "'reading'" in message and "'amount'" not in message
    assert cols["phone"]["dtype"] == "integer"
    # and the option does what it says
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cols = _summary(path, string_columns=["phone", "reading"])
    assert cols["phone"]["dtype"] == cols["reading"]["dtype"] == "string"


def test_name_words():
    for name in ("zip", "ZipCode", "member_id", "memberid", "NPI", "ndc_code", "patientNo"):
        assert name_suggests_identifier(name), name
    for name in ("amount", "age", "price", "quantity", "paid", "valid"):
        assert not name_suggests_identifier(name), name
    assert name_strongly_suggests_identifier("zip_code")
    assert not name_strongly_suggests_identifier("customer_id")


def test_digit_stats():
    s = DigitStats()
    s.add(pa.array(["12345", "99999", None]))
    assert s.fixed_width == 5 and not s.leading_zero
    s.add(pa.array(["1234"]))
    assert s.fixed_width is None
    s = DigitStats()
    s.add(pa.chunked_array([pa.array(["1"]), pa.array(["0123"])]))
    assert s.leading_zero


# ---- the options --------------------------------------------------------------------------------


def test_string_columns_types_and_infer_off(tmp_path, kernel):
    path = _csv(tmp_path, "a,b,c\n" + "5,2.5,1\n6,3.5,2\n7,4.5,3\n" * 10)
    assert _summary(path)["a"]["dtype"] == "integer"
    assert _summary(path, string_columns=["a"])["a"]["dtype"] == "string"
    assert _summary(path, types={"a": "string", "b": "string"})["b"]["dtype"] == "string"
    with pytest.raises(ValueError, match="unknown Arrow type name"):
        _summary(path, types={"a": "bogus"})
    off = _summary(_csv(tmp_path, "z,n\n" + "02134,5\n98101,6\n" * 10, "o.csv"), infer_types="off")
    assert off["z"]["dtype"] == "string"  # zeros are never dropped, even with inference off
    with pytest.raises(ValueError, match="infer_types"):
        _summary(path, infer_types="maybe")


def test_the_cli_options(tmp_path, kernel, capsys):
    path = _csv(tmp_path, "a,zip\n" + "5,02134\n6,98101\n" * 10)
    types = tmp_path / "types.json"
    types.write_text(json.dumps({"a": "string"}))
    out = tmp_path / "p.shape"
    assert main(["profile", path, "-o", str(out), "--types", str(types)]) == 0
    assert shape.load(out).summary()["columns"]["a"]["dtype"] == "string"
    assert main(["profile", path, "-o", str(out), "--string-columns", "a,zip"]) == 0
    assert shape.load(out).summary()["columns"]["a"]["dtype"] == "string"
    assert main(["profile", path, "-o", str(out), "--infer-types", "off"]) == 0
    assert shape.load(out).summary()["columns"]["zip"]["dtype"] == "string"
    assert main(["profile", path, "-o", str(out)]) == 0
    cols = shape.load(out).summary()["columns"]
    assert cols["a"]["dtype"] == "integer" and cols["zip"]["dtype"] == "string"
    types.write_text("[1]")
    assert main(["profile", path, "-o", str(out), "--types", str(types)]) == 2
    assert "JSON object" in capsys.readouterr().err


def test_a_folder_of_files_shares_one_decision(tmp_path, kernel):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a.csv").write_text("zip,n\n02134,1\n98101,2\n")
    (tmp_path / "d" / "b.csv").write_text("zip,n\n98101,3\n10001,4\n")  # no zero here
    col = shape.profile(str(tmp_path / "d")).summary()["columns"]
    assert col["zip"]["dtype"] == "string" and col["n"]["dtype"] == "integer"


# ---- the readers --------------------------------------------------------------------------------


def _ids(tmp_path: Path, rows: int = 400) -> Path:
    rng = random.Random(7)
    path = tmp_path / "ids.csv"
    with path.open("w") as fh:
        fh.write("member_id,zip,npi,amount\n")
        for _ in range(rows):
            fh.write(
                f"{rng.randint(1, 10**9):010d},{rng.choice(ZIPS)},"
                f"{rng.randint(10**9, 2 * 10**9 - 1)},{rng.randint(1, 999)}\n"
            )
    return path


def test_shape_io_reads_identifiers_as_text(tmp_path):
    path = _ids(tmp_path)
    for csv in (None, CsvOptions(stream=True), CsvOptions(stream=True, block_size=1 << 12)):
        src = open_source(str(path), csv=csv)
        assert [str(f.type) for f in src.schema] == ["string", "string", "string", "int64"]
    table = read_table(str(path))
    assert set(table["zip"].to_pylist()) == set(ZIPS)
    assert all(len(v) == 10 for v in table["member_id"].to_pylist())


def test_shape_io_options(tmp_path):
    path = _ids(tmp_path)
    off = open_source(str(path), csv=CsvOptions(infer_types="off")).schema
    assert {str(f.type) for f in off} == {"string"}
    forced = open_source(str(path), csv=CsvOptions(string_columns=("amount",))).schema
    assert str(forced.field("amount").type) == "string"
    typed = open_source(str(path), csv=CsvOptions(column_types={"zip": "int64"})).schema
    assert str(typed.field("zip").type) == "int64"  # an explicit type wins over the rule
    with pytest.raises(ValueError, match="infer_types"):
        _ = open_source(str(path), csv=CsvOptions(infer_types="x")).schema


def test_files_after_the_first_take_its_types(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "a.csv").write_text("zip,n\n02134,1\n98101,2\n")
    (tmp_path / "p" / "b.csv").write_text("zip,n\n98101,3\n10001,4\n")
    table = read_table(str(tmp_path / "p"))
    assert table["zip"].to_pylist() == ["02134", "98101", "98101", "10001"]


@pytest.mark.parametrize("mode", ["exact", "bounded"])
def test_the_profile_engine_keeps_the_text(tmp_path, mode):
    entry = profile_table(str(_ids(tmp_path)), options=EngineOptions(mode=mode))
    kinds = {c["name"]: c["kind"] for c in entry["columns"]}
    assert kinds == {"member_id": "text", "zip": "text", "npi": "text", "amount": "int"}
    zip_col = next(c for c in entry["columns"] if c["name"] == "zip")
    assert zip_col["min"] == "00000"


# ---- generation ---------------------------------------------------------------------------------


def _generated(tmp_path: Path, rows: int = 600) -> pa.Table:
    prof = shape.profile(str(_ids(tmp_path)), name="ids")
    shape_file = tmp_path / "ids.shape"
    shape.save(prof, shape_file)
    out = tmp_path / "gen"
    assert (
        main(
            ["generate", "--from", str(shape_file), "--rows", str(rows), "-f", "csv"]
            + ["-o", str(out)]
        )
        == 0
    )
    return read_table(str(out / "ids.csv"))


def test_generate_from_a_profile_keeps_the_zeros_and_the_width(tmp_path):
    table = _generated(tmp_path)
    assert pa.types.is_string(table["member_id"].type)
    assert pa.types.is_string(table["zip"].type) and pa.types.is_string(table["npi"].type)
    assert set(table["zip"].to_pylist()) <= set(ZIPS)
    assert "00000" in set(table["zip"].to_pylist())
    assert {len(v) for v in table["member_id"].to_pylist()} == {10}
    assert any(v.startswith("0") for v in table["member_id"].to_pylist())
    assert all(v.isdigit() for v in table["npi"].to_pylist())


def test_the_csv_file_itself_quotes_the_text(tmp_path):
    out = tmp_path / "gen" / "ids.csv"
    _generated(tmp_path)
    first = out.read_text().splitlines()[1]
    assert '"00000"' in first or '"02134"' in first or any(f'"{z}"' in first for z in ZIPS)


def test_learn_builds_text_generators(tmp_path):
    out = tmp_path / "learned.json"
    assert main(["learn", str(_ids(tmp_path)), "-o", str(out)]) == 0
    cols = next(iter(json.loads(out.read_text())["tables"].values()))["columns"]
    assert cols["member_id"]["type"] == "string"
    assert cols["member_id"]["generator"] == {"strategy": "pattern", "format": "{digits:10}"}
    assert cols["zip"]["generator"]["output_type"] == "string"
    assert set(cols["zip"]["generator"]["values"]) == set(ZIPS)
    assert cols["amount"]["type"] == "integer"
    gen = tmp_path / "g"
    assert main(["generate", str(out), "-f", "csv", "-o", str(gen), "--scale", "small"]) == 0
    table = read_table(str(next(gen.glob("*.csv"))))
    assert set(table["zip"].to_pylist()) <= set(ZIPS)


def test_the_digits_token_makes_fixed_width_digit_text():
    from shape.generation.engine import Engine  # noqa: PLC0415
    from shape.generation.schema import GenSchema  # noqa: PLC0415

    doc = {
        "schema_version": 1,
        "model": {"name": "d", "domain": "d", "schema_mode": "3nf", "seed": 5},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "zip": {
                        "name": "zip",
                        "type": "string",
                        "generator": {"strategy": "pattern", "format": "{digits:5}"},
                    },
                },
            }
        },
        "generation": {"scale": "s", "scales": {"s": {"t": 3000}}},
    }
    table = Engine(GenSchema.from_dict(doc)).generate()["t"]
    values = table["zip"].to_pylist()
    assert {len(v) for v in values} == {5} and all(v.isdigit() for v in values)
    assert sum(v.startswith("0") for v in values) > 100  # about a tenth have a leading zero


# ---- from-ddl ids, and every CSV writer -------------------------------------------------------


DDL = """
CREATE TABLE member (
  member_id CHAR(10) NOT NULL PRIMARY KEY,
  zip CHAR(5),
  npi VARCHAR(10),
  amount INT
);
"""


def test_from_ddl_character_ids_stay_text_through_every_writer(tmp_path):
    pytest.importorskip("faker")
    schema_file = tmp_path / "ddl.json"
    ddl = tmp_path / "ddl.sql"
    ddl.write_text(DDL)
    assert main(["from-ddl", str(ddl), "-o", str(schema_file)]) == 0
    cols = json.loads(schema_file.read_text())["tables"]["member"]["columns"]
    assert {cols[c]["type"] for c in ("member_id", "zip", "npi")} == {"string"}
    out = tmp_path / "out"
    for fmt in ("csv", "tsv", "parquet", "jsonl", "ipc"):
        assert main(["generate", str(schema_file), "-f", fmt, "-o", str(out / fmt)]) == 0
    csv_table = read_table(str(out / "csv" / "member.csv"))
    for name in ("zip", "npi"):  # leading zeros, or a fixed width under an identifier name
        assert pa.types.is_string(csv_table[name].type), name
    assert all(len(v) == 6 for v in csv_table["npi"].to_pylist())
    assert csv_table["npi"][0].as_py() == "000001"
    first = (out / "csv" / "member.csv").read_text().splitlines()[1]
    assert '"000001"' in first  # the writer quotes text, so nothing else reads it as a number
    parquet = pq.read_table(out / "parquet" / "member.parquet")
    assert pa.types.is_string(parquet["zip"].type) and pa.types.is_string(parquet["npi"].type)
    tsv = pacsv.read_csv(
        out / "tsv" / "member.tsv",
        parse_options=pacsv.ParseOptions(delimiter="\t"),
        convert_options=pacsv.ConvertOptions(column_types={"npi": pa.string()}),
    )
    assert tsv["npi"][0].as_py() == "000001"


def test_the_other_csv_writers_keep_the_text(tmp_path):
    from shape.dimensional.files import write_table  # noqa: PLC0415
    from shape.quality.quarantine import QuarantineManager  # noqa: PLC0415

    table = pa.table({"zip": ZIPS, "n": list(range(len(ZIPS)))})
    path = tmp_path / "dim.csv"
    write_table(table, path, "csv")
    assert read_table(str(path))["zip"].to_pylist() == ZIPS
    assert '"02134"' in path.read_text()
    dest = QuarantineManager().quarantine_table(
        table, tmp_path / "q", "run1", "t", "why", fmt="csv"
    )
    assert read_table(str(dest))["zip"].to_pylist() == ZIPS


def test_incremental_continue_reads_back_the_text(tmp_path):
    from shape.cli.incremental import read_tables  # noqa: PLC0415

    pacsv.write_csv(pa.table({"zip": ZIPS, "n": list(range(len(ZIPS)))}), tmp_path / "t.csv")
    assert read_tables(tmp_path)["t"]["zip"].to_pylist() == ZIPS


def test_the_safe_profile_describes_an_identifier_as_text(tmp_path):
    from shape.privacy.safe_profile import to_safe_profile  # noqa: PLC0415

    prof = shape.profile(str(_ids(tmp_path)), name="ids")
    safe = to_safe_profile(prof).to_dict()
    cols = safe["columns"] if "columns" in safe else next(iter(safe["tables"].values()))["columns"]
    for name in ("zip", "member_id", "npi"):
        col = cols[name]
        assert col["dtype"] == "string", name
        assert col["mean"] is None and col["quantiles"] is None and col["bounds"] is None, name
    assert cols["zip"]["string_length"]["min"] == cols["zip"]["string_length"]["max"] == 5
    assert cols["amount"]["dtype"] == "integer"
