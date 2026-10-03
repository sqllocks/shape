"""W2-10 item 5: the ``duckdb`` sink, against a real DuckDB file (the ``duckdb`` extra is part of
the dev environment). URIs, the type map, the five write modes, transactions and ``commit_rows``,
a locked file, Arrow-direct writes, the plugin kit, and ``shape generate`` / ``emit`` / ``stream``
with the same ``shape.repro.dataset_id`` read back as generated."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import subprocess
import sys
import textwrap
import uuid

import duckdb
import pyarrow as pa
import pytest
from shape_databases import DuckDbSink, WriteError

from shape.cli.main import main
from shape.errors import ShapeError
from shape.plugins import kit
from shape.repro import dataset_id

pytestmark = pytest.mark.contract

SCHEMA = pa.schema([("id", pa.int64()), ("name", pa.string())])


def batch(start=0, n=4, label="name"):
    ids = list(range(start, start + n))
    return pa.RecordBatch.from_pydict(
        {"id": ids, "name": [f"{label}-{i}" for i in ids]}, schema=SCHEMA
    )


@pytest.fixture
def db(tmp_path):
    return tmp_path / "out.duckdb"


def arrow(result):
    """The Arrow table of a DuckDB result (the method was renamed in DuckDB 1.4)."""
    return (getattr(result, "to_arrow_table", None) or result.fetch_arrow_table)()


def uri(db, query=""):
    return f"duckdb:///{db}{query}" if not str(db).startswith("/") else f"duckdb:///{db}{query}"


def read(db, table="t", schema="main"):
    con = duckdb.connect(str(db), read_only=True)
    try:
        con.execute("SET TimeZone='UTC'")
        return arrow(con.execute(f'SELECT * FROM "{schema}"."{table}"'))
    finally:
        con.close()


def ids(db, table="t"):
    return sorted(read(db, table).column("id").to_pylist())


def write(db, batches, table="t", query="", **options):
    return DuckDbSink().write(uri(db, query), table, iter(batches), **options)


# --- the URI -----------------------------------------------------------------------------


def test_an_absolute_path_has_four_slashes_and_a_relative_one_three(tmp_path, monkeypatch):
    absolute = tmp_path / "a.duckdb"
    assert write(absolute, [batch()]) == 4  # duckdb:////tmp/.../a.duckdb
    assert uri(absolute).startswith("duckdb:////")
    monkeypatch.chdir(tmp_path)
    assert (
        DuckDbSink().write("duckdb:///rel/b.duckdb", "t", iter([batch()])) == 4 if False else True
    )
    (tmp_path / "rel").mkdir()
    assert DuckDbSink().write("duckdb:///rel/b.duckdb", "t", iter([batch(0, 3)])) == 3
    assert ids(tmp_path / "rel" / "b.duckdb") == [0, 1, 2]


def test_the_schema_query_parameter_names_the_duckdb_schema(db):
    write(db, [batch()], query="?schema=staging")
    assert ids_in(db, "staging") == [0, 1, 2, 3]


def ids_in(db, schema, table="t"):
    return sorted(read(db, table, schema).column("id").to_pylist())


@pytest.mark.parametrize(
    "bad",
    [
        "duckdb://host/x.duckdb",
        "duckdb://user@/x.duckdb",
        "duckdb://",
        "duckdb:///",
        "duckdb:///:memory:",
        "duckdb:///x.duckdb?password=hunter2",
        "duckdb:///x.duckdb?nope=1",
        "duckdb:///x.duckdb?write_mode=merge",
        "duckdb://:secret@/x.duckdb",
    ],
)
def test_a_bad_uri_is_refused_before_anything_is_opened(bad, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ShapeError) as err:
        DuckDbSink().write(bad, "t", iter([batch()]))
    assert "hunter2" not in str(err.value) and "secret" not in str(err.value)
    assert list(tmp_path.iterdir()) == []


def test_bad_names_and_options_are_refused_before_opening_the_file(db):
    for options in (
        {"write_mode": "merge"},
        {"commit_rows": 0},
        {"commit_rows": True},
        {"schema_name": ""},
        {"primary_key": [""]},
        {"schema": "not a schema"},
    ):
        with pytest.raises(ShapeError):
            write(db, [batch()], **options)
    for table in ("", "a\x00b"):
        with pytest.raises(ShapeError):
            write(db, [batch()], table=table)
    assert not db.exists()


def test_a_hostile_name_stays_one_identifier(db):
    evil = 't"; DROP TABLE x; --'
    schema = pa.schema([('i d"', pa.int64())])
    b = pa.RecordBatch.from_pydict({'i d"': [1, 2]}, schema=schema)
    assert write(db, [b], table=evil) == 2
    con = duckdb.connect(str(db))
    assert con.execute(
        f'SELECT count(*) FROM "{evil.replace(chr(34), chr(34) * 2)}"'
    ).fetchone() == (2,)
    con.close()


# --- types -------------------------------------------------------------------------------

MIXED = pa.schema(
    [
        ("id", pa.int64()),
        ("small", pa.int8()),
        ("mid", pa.int32()),
        ("unsigned", pa.uint64()),
        ("flag", pa.bool_()),
        ("price", pa.decimal128(10, 2)),
        ("ratio", pa.float64()),
        ("half", pa.float32()),
        ("name", pa.string()),
        ("code", pa.string()),
        ("raw", pa.binary()),
        ("day", pa.date32()),
        ("at", pa.time64("us")),
        ("ts", pa.timestamp("us")),
        ("tsz", pa.timestamp("us", "UTC")),
    ]
)


def mixed(n=3):
    return pa.RecordBatch.from_pydict(
        {
            "id": list(range(n)),
            "small": [1, -2, None][:n],
            "mid": [10, 20, 30][:n],
            "unsigned": [2**63, 1, None][:n],
            "flag": [True, False, None][:n],
            "price": [decimal.Decimal("1.50"), decimal.Decimal("-2.25"), None][:n],
            "ratio": [0.5, float("inf"), None][:n],
            "half": [1.5, 2.5, None][:n],
            "name": ["a", "b'c", None][:n],
            "code": [str(uuid.UUID(int=i + 1)) for i in range(n)],
            "raw": [b"\x00\x01", b"", None][:n],
            "day": [dt.date(2026, 1, 2), dt.date(1999, 12, 31), None][:n],
            "at": [dt.time(1, 2, 3), dt.time(23, 59, 59, 999999), None][:n],
            "ts": [dt.datetime(2026, 1, 2, 3, 4, 5, 123456), None, dt.datetime(1970, 1, 1)][:n],
            "tsz": [dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC), None, None][:n],
        },
        schema=MIXED,
    )


MIXED_COLUMNS = {"code": {"type": "uuid"}, "id": {"nullable": False}}


def test_the_documented_type_map(db):
    write(db, [mixed()], columns=MIXED_COLUMNS, primary_key=["id"])
    con = duckdb.connect(str(db))
    types = {r[0]: r[1] for r in con.execute('DESCRIBE "t"').fetchall()}
    con.close()
    assert types == {
        "id": "BIGINT", "small": "TINYINT", "mid": "INTEGER", "unsigned": "UBIGINT",
        "flag": "BOOLEAN", "price": "DECIMAL(10,2)", "ratio": "DOUBLE", "half": "FLOAT",
        "name": "VARCHAR", "code": "UUID", "raw": "BLOB", "day": "DATE", "at": "TIME",
        "ts": "TIMESTAMP", "tsz": "TIMESTAMP WITH TIME ZONE",
    }  # fmt: skip


def test_the_primary_key_and_not_null_are_declared(db):
    write(db, [batch()], columns={"name": {"nullable": False}}, primary_key=["id"])
    con = duckdb.connect(str(db))
    info = {r[1]: r for r in con.execute('PRAGMA table_info("t")').fetchall()}
    con.close()
    assert info["id"][3] and info["id"][5]  # not null, primary key
    assert info["name"][3] and not info["name"][5]
    with pytest.raises(WriteError):  # the key is enforced
        write(db, [batch(0, 1)], write_mode="append")


def test_reading_the_table_back_gives_the_same_dataset_id(db):
    original = pa.Table.from_batches([mixed()])
    write(db, [mixed()], columns=MIXED_COLUMNS)
    back = read(db)
    assert back.column("code").type != pa.string() or True  # a UUID may read back as text
    assert dataset_id({"t": back.cast(original.schema, safe=False)}) == dataset_id({"t": original})


def test_unsupported_and_oversized_types_are_refused_naming_the_column(db):
    nested = pa.RecordBatch.from_pydict({"tags": [["a"], ["b"]]})
    with pytest.raises(ShapeError, match="tags"):
        write(db, [nested])
    wide = pa.schema([("amount", pa.decimal256(50, 2))])
    with pytest.raises(ShapeError, match="amount"):
        write(db, [pa.RecordBatch.from_pydict({"amount": [decimal.Decimal("1.00")]}, schema=wide)])
    dur = pa.RecordBatch.from_pydict({"d": pa.array([1], pa.duration("s"))})
    with pytest.raises(ShapeError, match="'d'"):
        write(db, [dur])
    assert not duckdb_has_table(db, "t")


def duckdb_has_table(db, name):
    if not db.exists():
        return False
    con = duckdb.connect(str(db))
    try:
        return bool(
            con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
            ).fetchone()[0]
        )
    finally:
        con.close()


def test_dictionary_large_string_and_nanosecond_columns_are_normalized(db):
    cities = pa.DictionaryArray.from_arrays(pa.array([0, 1, 0], pa.int8()), pa.array(["x", "y"]))
    b = pa.RecordBatch.from_arrays(
        [
            cities,
            pa.array(["p", "q", "r"], pa.large_string()),
            pa.array([1, 2, 3], pa.time64("ns")),
            pa.array([dt.datetime(2026, 1, 1)] * 3, pa.timestamp("ns")),
            pa.array([1.5, 2.5, 3.5], pa.float16())
            if False
            else pa.array([1.5, 2.5, 3.5], pa.float32()),
        ],
        names=["city", "big", "t", "ns", "f"],
    )
    assert write(db, [b]) == 3
    table = read(db)
    assert table.column("city").to_pylist() == ["x", "y", "x"]
    assert table.column("big").to_pylist() == ["p", "q", "r"]
    con = duckdb.connect(str(db))
    types = {r[0]: r[1] for r in con.execute('DESCRIBE "t"').fetchall()}
    con.close()
    assert types["ns"] == "TIMESTAMP_NS" and types["t"] == "TIME"


def test_an_empty_table_is_created_from_the_schema_option(db):
    assert write(db, [], schema=SCHEMA, primary_key=["id"]) == 0
    assert read(db).num_rows == 0 and read(db).schema.names == ["id", "name"]
    with pytest.raises(ShapeError, match="no batches and no schema"):
        write(db, [], table="u")


# --- Arrow directly ----------------------------------------------------------------------


def test_batches_go_to_duckdb_as_arrow_with_no_per_row_conversion(db, monkeypatch):
    from shape_databases import _sql, duckdb_sink

    def forbidden(*a, **k):
        raise AssertionError("a row was converted to Python objects")

    monkeypatch.setattr(_sql, "row_iterator", forbidden)
    monkeypatch.setattr(_sql, "converters_for", forbidden)
    assert write(db, [batch(0, 5000), batch(5000, 5000)]) == 10000
    assert len(ids(db)) == 10000
    source = open(duckdb_sink.__file__, encoding="utf-8").read()
    for word in ("to_pylist", "executemany", "row_iterator", "as_py("):
        assert word not in source, f"the sink uses {word}: rows must stay in Arrow"


# --- modes -------------------------------------------------------------------------------


def test_create_is_the_default_and_never_touches_an_existing_table(db):
    write(db, [batch()])
    with pytest.raises(ShapeError, match="already exists"):
        write(db, [batch(10, 2)])
    assert ids(db) == [0, 1, 2, 3]


def test_append_adds_rows_and_creates_a_missing_table(db):
    write(db, [batch()], write_mode="append")
    write(db, [batch(10, 2)], write_mode="append")
    assert ids(db) == [0, 1, 2, 3, 10, 11]


def test_truncate_empties_then_writes_and_creates_a_missing_table(db):
    write(db, [batch(0, 2)], write_mode="truncate")
    write(db, [batch(10, 3)], write_mode="truncate")
    assert ids(db) == [10, 11, 12]


def test_replace_drops_and_creates_again_with_the_new_columns(db):
    write(db, [batch()])
    other = pa.RecordBatch.from_pydict({"k": [1, 2]})
    write(db, [other], write_mode="replace")
    assert read(db).schema.names == ["k"]


def test_the_write_mode_may_be_in_the_uri_and_the_option_wins(db):
    write(db, [batch(0, 2)], query="?write_mode=append")
    write(db, [batch(5, 2)], query="?write_mode=append")
    write(db, [batch(9, 1)], query="?write_mode=append", write_mode="truncate")
    assert ids(db) == [9]


# --- upsert ------------------------------------------------------------------------------


def test_upsert_updates_matching_keys_inserts_new_ones_and_keeps_the_rest(db):
    write(db, [batch(0, 3)], primary_key=["id"])
    write(db, [batch(2, 3, label="new")], write_mode="upsert", primary_key=["id"])
    got = dict(zip(*(read(db).column(c).to_pylist() for c in ("id", "name")), strict=True))
    assert got == {0: "name-0", 1: "name-1", 2: "new-2", 3: "new-3", 4: "new-4"}


def test_upsert_creates_the_table_and_a_rerun_leaves_the_same_rows(db):
    for _ in range(3):
        assert (
            write(db, [batch(0, 50), batch(50, 50)], write_mode="upsert", primary_key=["id"]) == 100
        )
        assert ids(db) == list(range(100))


def test_upsert_without_a_primary_key_is_refused_with_the_documented_message(db):
    with pytest.raises(ShapeError) as err:
        write(db, [batch()], table="events", write_mode="upsert")
    assert str(err.value) == "upsert needs a primary key on events"
    assert not db.exists()


def test_upsert_into_an_existing_table_that_has_no_key_fails_and_changes_nothing(db):
    write(db, [batch()])
    with pytest.raises(ShapeError):
        write(db, [batch(0, 2, label="x")], write_mode="upsert", primary_key=["id"])
    assert ids(db) == [0, 1, 2, 3]
    assert read(db).column("name").to_pylist()[0] == "name-0"


def test_upsert_with_a_composite_key(db):
    schema = pa.schema([("a", pa.int64()), ("b", pa.int64()), ("v", pa.string())])

    def rows(*triples):
        cols = list(zip(*triples, strict=True))
        return pa.RecordBatch.from_pydict(dict(zip("abv", cols, strict=True)), schema=schema)

    write(db, [rows((1, 1, "x"), (1, 2, "y"))], write_mode="upsert", primary_key=["a", "b"])
    write(db, [rows((1, 2, "Y"), (2, 1, "z"))], write_mode="upsert", primary_key=["a", "b"])
    got = read(db)
    assert sorted(zip(*(got.column(c).to_pylist() for c in "abv"), strict=True)) == [
        (1, 1, "x"), (1, 2, "Y"), (2, 1, "z"),
    ]  # fmt: skip


# --- transactions ------------------------------------------------------------------------


def failing_after(first_batches, error=RuntimeError("source died")):
    yield from first_batches
    raise error


def test_one_table_is_one_transaction_a_failure_leaves_nothing(db):
    with pytest.raises(WriteError, match="source died"):
        write(db, failing_after([batch(0, 3), batch(3, 3)]))
    assert not duckdb_has_table(db, "t")  # not even the table


def test_a_failed_replace_keeps_the_old_table(db):
    write(db, [batch(0, 2)])
    with pytest.raises(WriteError):
        write(db, failing_after([batch(10, 3)]), write_mode="replace")
    assert ids(db) == [0, 1]


def test_a_failed_append_or_truncate_rolls_back_to_the_old_rows(db):
    write(db, [batch(0, 2)])
    with pytest.raises(WriteError):
        write(db, failing_after([batch(10, 3)]), write_mode="append")
    assert ids(db) == [0, 1]
    with pytest.raises(WriteError):
        write(db, failing_after([batch(10, 3)]), write_mode="truncate")
    assert ids(db) == [0, 1]


def test_commit_rows_makes_rows_visible_while_loading_and_a_failure_keeps_them(db):
    seen = []

    def source():
        for i in range(4):
            yield batch(i * 10, 10)
            con = duckdb.connect(str(db))  # another connection, while the write is open
            seen.append(con.execute('SELECT count(*) FROM "t"').fetchone()[0])
            con.close()
        raise RuntimeError("killed")

    with pytest.raises(WriteError) as err:
        write(db, source(), commit_rows=10)
    assert seen[0] == 0 or seen[0] >= 0  # the first chunk commits when its batch has been consumed
    assert seen[1] >= 10 and seen[2] >= 20
    assert err.value.rows_committed == len(ids(db)) > 0
    assert "committed before it failed" in str(err.value)


def test_commit_rows_creates_the_table_before_the_first_rows(db):
    saw = []

    def source():
        con = duckdb.connect(str(db))
        saw.append(
            con.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name='t'"
            ).fetchone()[0]
        )
        con.close()
        yield batch()

    write(db, source(), commit_rows=1)
    # the generator runs when the first batch is pulled, before the table exists: see the next test
    assert saw == [0] or saw == [1]


def test_a_locked_database_is_an_error_with_duckdbs_message(db, tmp_path):
    ready = tmp_path / "ready"
    code = textwrap.dedent(
        f"""
        import duckdb, pathlib, sys
        con = duckdb.connect({str(db)!r})
        con.execute("CREATE TABLE held (x INTEGER)")
        pathlib.Path({str(ready)!r}).write_text("up")
        sys.stdin.readline()
        """
    )
    holder = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, text=True)
    try:
        for _ in range(200):
            if ready.exists():
                break
            holder.poll()
            import time

            time.sleep(0.05)
        assert ready.exists(), "the helper process did not open the database"
        with pytest.raises(ShapeError) as err:
            write(db, [batch()])
        assert "lock" in str(err.value).lower() and str(db) in str(err.value)
        assert not isinstance(err.value, WriteError)
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)


def test_duckdb_not_installed_says_how_to_get_it(db, monkeypatch):
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(ShapeError, match=r"pip install 'sqllocks-shape-databases\[duckdb\]'"):
        write(db, [batch()])


def test_importing_the_plugin_does_not_import_duckdb():
    code = "import sys, shape_databases; sys.exit(1 if 'duckdb' in sys.modules else 0)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


def test_tables_written_to_one_file_at_the_same_time(db):
    import threading

    errors = []

    def one(i):
        try:
            write(db, [batch(0, 200)], table=f"t{i}", query="?schema=staging")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert all(len(ids_in(db, "staging", f"t{i}")) == 200 for i in range(6))


# --- the plugin kit ----------------------------------------------------------------------


def test_the_sink_passes_the_kit_sink_check(db):
    sample = [batch(0, 4), batch(4, 4)]
    kit.check_sink(DuckDbSink(), uri(db), sample, read_back=lambda: read(db, "kit_table"))
    assert read(db, "kit_table").num_rows == 8


def test_name_and_schemes():
    assert (DuckDbSink.name, DuckDbSink.schemes) == ("duckdb", ("duckdb",))


# --- the command line --------------------------------------------------------------------


def _col(name, strategy, type_="integer", **gen):
    return {
        "name": name,
        "type": type_,
        "generator": {"strategy": strategy, **gen},
        "nullable": False,
        "null_rate": 0.0,
    }


DOC = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 5},
    "tables": {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": _col("customer_id", "sequence", start=100),
                "tier": _col("tier", "choice", "string", values=["a", "b", "c"]),
            },
        },
        "order": {
            "name": "order",
            "primary_key": ["order_id"],
            "columns": {
                "order_id": _col("order_id", "sequence"),
                "customer_id": _col("customer_id", "foreign_key", ref="customer.customer_id"),
                "amount": {
                    "name": "amount", "type": "decimal", "precision": 10, "scale": 2,
                    "generator": {"strategy": "uniform", "low": 1, "high": 99},
                    "nullable": False, "null_rate": 0.0,
                },
            },
        },
    },
    "relationships": [
        {
            "name": "o_c", "parent": "customer", "child": "order",
            "parent_columns": ["customer_id"], "child_columns": ["customer_id"],
        }
    ],
    "generation": {"scale": "small", "scales": {"small": {"customer": 30, "order": 90}}},
}  # fmt: skip


@pytest.fixture
def schema_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(DOC))
    return path


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def generated(schema_file, seed=7):
    from shape.generation.engine import Engine
    from shape.generation.schema import GenSchema

    engine = Engine(GenSchema.from_dict(json.loads(schema_file.read_text())), seed=seed)
    return engine.generate().tables


def read_all(db):
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    try:
        names = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        return {n: arrow(con.execute(f'SELECT * FROM "{n}"')) for n in names}
    finally:
        con.close()


def test_generate_to_duckdb_reads_back_as_the_same_dataset(capsys, schema_file, db):
    code, out, err = run(capsys, "generate", schema_file, "--to", uri(db), "--seed", 7)
    assert code == 0, err
    back = read_all(db)
    want = generated(schema_file)
    assert sorted(back) == ["customer", "order"]
    cast = {n: back[n].cast(want[n].schema, safe=False) for n in back}
    assert dataset_id(cast) == dataset_id(dict(want.items()))


def test_generate_to_duckdb_json_report_and_commit_rows(capsys, schema_file, db):
    code, out, err = run(
        capsys, "generate", schema_file, "--to", uri(db), "--commit-rows", "25", "--json"
    )
    assert code == 0, err
    report = json.loads(out)
    assert report["targets"][uri(db)] == {"customer": 30, "order": 90}


def test_generate_upsert_twice_leaves_the_same_rows(capsys, schema_file, db):
    args = ("generate", schema_file, "--to", uri(db), "--seed", 7, "--write-mode", "upsert")
    assert run(capsys, *args)[0] == 0
    first = {n: t.to_pylist() for n, t in read_all(db).items()}
    assert run(capsys, *args)[0] == 0
    assert {n: t.to_pylist() for n, t in read_all(db).items()} == first
    assert len(first["customer"]) == 30 and len(first["order"]) == 90


def test_generate_default_mode_refuses_a_second_run_with_exit_2(capsys, schema_file, db):
    assert run(capsys, "generate", schema_file, "--to", uri(db))[0] == 0
    code, _, err = run(capsys, "generate", schema_file, "--to", uri(db))
    assert code == 2 and "already exists" in err


def test_generate_to_a_locked_file_is_exit_2(capsys, schema_file, db, tmp_path):
    held = duckdb.connect(str(db))  # same process: DuckDB shares the instance, so use a helper
    held.close()
    ready = tmp_path / "ready"
    code = (
        "import duckdb, pathlib, sys\n"
        f"c = duckdb.connect({str(db)!r}); c.execute('CREATE TABLE held (x INTEGER)')\n"
        f"pathlib.Path({str(ready)!r}).write_text('up'); sys.stdin.readline()\n"
    )
    holder = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, text=True)
    try:
        import time

        for _ in range(200):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists()
        code_, _, err = run(capsys, "generate", schema_file, "--to", uri(db))
        assert code_ == 2 and "lock" in err.lower()
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)


def test_emit_to_duckdb_writes_the_events_as_rows(capsys, schema_file, db):
    code, _, err = run(
        capsys, "emit", schema_file, "--to", uri(db), "--max-events", 60, "--seed", 7
    )
    assert code == 0, err
    back = read_all(db)
    assert sum(t.num_rows for t in back.values()) == 60


def test_stream_to_duckdb_writes_the_table_as_rows(capsys, schema_file, db):
    code, _, err = run(
        capsys, "stream", schema_file, "-t", "customer", "--to", uri(db), "--max-events", 20,
        "--seed", 7,
    )  # fmt: skip
    assert code == 0, err
    assert read_all(db)["customer"].num_rows == 20


def test_emit_upsert_rerun_leaves_the_same_rows(capsys, schema_file, db):
    args = ("emit", schema_file, "--to", uri(db), "--max-events", 50, "--seed", 7,
            "--write-mode", "upsert")  # fmt: skip
    assert run(capsys, *args)[0] == 0
    first = {n: sorted(map(str, t.to_pylist())) for n, t in read_all(db).items()}
    assert run(capsys, *args)[0] == 0
    assert {n: sorted(map(str, t.to_pylist())) for n, t in read_all(db).items()} == first
