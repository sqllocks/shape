"""W5-05 item 5: `shape seed` through database sinks (an in-memory sink here), the SQL script
target, modes, the dry run and determinism."""

from __future__ import annotations

import glob
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from shape.errors import ShapeError
from shape.testdata.seed import SeedRefused, build_plan, seed_target

pytest.importorskip("shape_domains")


class Db:
    """One in-memory database: tables of rows, and the order of every write."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.writes: list[tuple[str, str]] = []  # (table, mode)
        self.connections = 0
        self.options: dict[str, dict] = {}
        self.key_reads: list[str] = []  # tables whose keys were read before an append


class Conn:
    def __init__(self, db: Db) -> None:
        self.db, self.row = db, None

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        if sql.startswith("keys:"):  # FakeSink.keys_sql: every key of the table
            _, table, columns = sql.split(":")
            self.rows = [tuple(r[c] for c in columns.split(",")) for r in self.db.tables[table]]
            self.db.key_reads.append(table)
            return
        self.row = (1,) if params[1] in self.db.tables else None

    def fetchone(self):
        return self.row

    def fetchmany(self, size):
        out, self.rows = self.rows[:size], self.rows[size:]
        return out

    def close(self):
        pass


class FakeSink:
    """The database sink protocol seed uses: plan, connect hooks, exists_sql and write."""

    name = "postgres"
    exists_sql = "exists?"

    def __init__(self, db: Db, fail_on: str | None = None) -> None:
        self.db, self.fail_on = db, fail_on

    def plan(self, uri, table, options):
        key = list(options.get("primary_key") or [])
        return types.SimpleNamespace(schema_name=None, table=table, primary_key=key)

    def keys_sql(self, plan):
        return f"keys:{plan.table}:{','.join(plan.primary_key)}"

    def connect_params(self, plan):
        return {}

    def _connect(self, **params):
        self.db.connections += 1
        return Conn(self.db)

    def write(self, uri, table, batches, **options):
        mode = options["write_mode"]
        if table == self.fail_on:
            raise RuntimeError("disk full")
        if mode == "create" and table in self.db.tables:
            raise ShapeError(f"table {table} already exists")
        rows = [r for b in batches for r in b.to_pylist()]
        if mode == "truncate" or table not in self.db.tables:
            self.db.tables[table] = []
        self.db.tables[table] += rows
        self.db.writes.append((table, mode))
        self.db.options[table] = options
        return len(rows)


def seed(db, target="postgres://user@host/shape", **kw):
    kw.setdefault("scale", "tiny")
    sinks = kw.pop("sinks", {"postgres": FakeSink(db), "mysql": FakeSink(db)})
    return seed_target("retail", target, sinks=sinks, **kw)


def fk_pairs():
    from shape.generation.domains import load_domain

    schema = load_domain("retail").schema
    return [(r.parent, r.child) for r in schema.relationships if r.parent != r.child]


# ---- order and options ------------------------------------------------------------------------


def test_tables_are_written_parents_first_with_their_keys_and_column_facts():
    db = Db()
    result = seed(db)
    order = [t for t, _ in db.writes]
    assert order == list(result.written) and len(order) == 9
    for parent, child in fk_pairs():
        assert order.index(parent) < order.index(child), (parent, child)
    assert all(rows == 100 for rows in result.written.values())
    options = db.options["order"]
    assert options["primary_key"] == ["order_id"] and options["write_mode"] == "create"
    assert options["columns"]["customer_id"]["nullable"] is False
    assert options["columns"]["shipping_address_id"]["nullable"] is True
    assert options["schema"].names[0] == "order_id"


def test_mssql_postgresql_and_mysql_uris_pick_their_sink():
    for uri, sink in (
        ("mssql://h/db", "sqlserver"),
        ("sqlserver://h/db", "sqlserver"),
        ("postgres://h/db", "postgres"),
        ("postgresql://h/db", "postgres"),
        ("mysql://h/db", "mysql"),
    ):
        plan = build_plan("retail", uri, scale="tiny", seed=None, mode="create")[0]
        assert plan.sink == sink


def test_the_scale_and_seed_decide_the_rows_and_the_contents():
    db = Db()
    seed(db, scale="fabric_demo", seed=5)
    assert len(db.tables["order"]) == 1000 and len(db.tables["customer"]) == 200
    other = Db()
    seed(other, scale="fabric_demo", seed=6)
    assert db.tables["order"] != other.tables["order"]


# ---- modes ------------------------------------------------------------------------------------


def test_create_refuses_an_existing_table_and_writes_nothing():
    db = Db()
    db.tables["order_line"] = [{"old": 1}]  # late in the order: earlier tables must not be written
    with pytest.raises(SeedRefused, match="table\\(s\\) order_line already exist"):
        seed(db)
    assert db.writes == [] and list(db.tables) == ["order_line"]
    assert db.connections == 1  # the check opened one connection
    db.tables.update({"customer": [], "order": []})
    with pytest.raises(SeedRefused, match="customer, order, order_line"):
        seed(db)


def test_create_into_an_empty_database_then_again_is_refused():
    db = Db()
    seed(db)
    with pytest.raises(SeedRefused):
        seed(db)
    assert len(db.writes) == 9


def test_truncate_with_the_same_seed_leaves_identical_contents_and_other_seeds_do_not():
    db = Db()
    seed(db, mode="truncate", seed=11)
    first = json.dumps(db.tables, sort_keys=True, default=str)
    seed(db, mode="truncate", seed=11)
    assert json.dumps(db.tables, sort_keys=True, default=str) == first
    assert db.connections == 0  # only create looks at the database first
    seed(db, mode="truncate", seed=12)
    assert json.dumps(db.tables, sort_keys=True, default=str) != first


def test_append_adds_rows_and_creates_missing_tables():
    db = Db()
    seed(db, mode="append")
    assert all(len(rows) == 100 for rows in db.tables.values()) and len(db.tables) == 9
    db.tables["customer"] = [
        dict(r, customer_id=r["customer_id"] + 10_000) for r in db.tables["customer"]
    ]
    for table in list(db.tables):
        if table != "customer":
            del db.tables[table]
    seed(db, mode="append")  # no generated key is in the table: the rows are added
    assert len(db.tables["customer"]) == 200 and len(db.tables["order"]) == 100
    assert {m for _, m in db.writes} == {"append"}


def test_append_that_would_duplicate_a_primary_key_is_refused_before_anything_is_written():
    # Lead decision (2026-10-05): the docs say only "append adds the rows"; an append whose keys
    # are already in the table fails first, naming the table and the key, and writes nothing.
    db = Db()
    seed(db, mode="append", seed=5)
    before = json.dumps(db.tables, sort_keys=True, default=str)
    writes = len(db.writes)
    with pytest.raises(SeedRefused) as err:
        seed(db, mode="append", seed=5)
    message = str(err.value)
    assert "customer (customer_id = 1" in message and "order (order_id = 1" in message
    assert "nothing was written" in message and "--mode truncate" in message
    assert json.dumps(db.tables, sort_keys=True, default=str) == before
    assert len(db.writes) == writes  # no table written, not even the ones without a clash
    with pytest.raises(SeedRefused, match="customer"):  # the keys do not depend on the seed
        seed(db, mode="append", seed=6)


def test_an_append_clash_late_in_the_order_still_writes_nothing():
    db = Db()
    db.tables["order_line"] = [{"order_line_id": 7}]
    with pytest.raises(SeedRefused, match=r"order_line \(order_line_id = 7\)") as err:
        seed(db, mode="append")
    assert "customer" not in str(err.value)  # only the clashing table is named
    assert db.writes == [] and db.tables["order_line"] == [{"order_line_id": 7}]
    assert db.connections == 1 and db.key_reads == ["order_line"]


def test_an_unknown_mode_is_refused():
    with pytest.raises(ShapeError, match="unknown mode 'replace'.*create, truncate, append"):
        seed(Db(), mode="replace")


# ---- the dry run ------------------------------------------------------------------------------


class Exploding:
    def __getattr__(self, name):
        raise AssertionError(f"the dry run touched the sink ({name})")


def test_dry_run_prints_the_plan_and_connects_to_nothing():
    result = seed_target(
        "retail",
        "postgres://user:hunter2@host/shape",
        scale="fabric_demo",
        mode="truncate",
        dry_run=True,
        sinks={"postgres": Exploding()},
    )
    assert result.dry_run and result.written == {} and result.files == []
    names = [t["table"] for t in result.plan.tables]
    assert names[0] == "customer" and "order_line" in names
    assert {t["table"]: t["rows"] for t in result.plan.tables}["order"] == 1000
    text = "\n".join(result.plan.lines())
    assert "hunter2" not in text and "hunter2" not in json.dumps(result.to_dict())
    assert "4,670 rows in 9 tables" in text and "mode truncate" in text
    assert result.to_dict()["plan"]["rows"] == 4670


def test_dry_run_does_not_load_a_sink_at_all(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("loaded a sink")

    monkeypatch.setattr("shape.testdata.seed._sink", boom)
    for target in ("mssql://h/db", "mysql://h/db", "sql://somewhere"):
        assert seed_target("retail", target, scale="tiny", dry_run=True).dry_run


def test_a_plan_needs_a_known_scale_a_target_scheme_and_a_spec():
    with pytest.raises(ShapeError, match="unknown scale 'huge'"):
        build_plan("retail", "postgres://h/d", scale="huge", seed=None, mode="create")
    with pytest.raises(ShapeError, match="cannot seed 'oracle://h/d'.*sql://DIR"):
        build_plan("retail", "oracle://h/d", scale=None, seed=None, mode="create")
    with pytest.raises(ShapeError, match="cannot seed"):
        build_plan("retail", "/just/a/path", scale=None, seed=None, mode="create")
    with pytest.raises(Exception, match="nodomain"):
        build_plan("nodomain", "postgres://h/d", scale=None, seed=None, mode="create")


def test_a_schema_file_is_a_spec(tmp_path):
    from shape.generation.domains import load_domain

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(load_domain("retail").schema.to_dict()))
    db = Db()
    seed_target("retail", "postgres://h/d", scale="tiny", sinks={"postgres": FakeSink(db)})
    db2 = Db()
    seed_target(str(path), "postgres://h/d", scale="tiny", sinks={"postgres": FakeSink(db2)})
    assert db.tables == db2.tables
    path.write_text("{nope")
    with pytest.raises(ShapeError, match="is not valid JSON"):
        seed_target(str(path), "postgres://h/d", dry_run=True)


# ---- sinks that are missing, cannot say what exists, or fail --------------------------------


def test_a_sink_that_is_not_installed_says_how_to_install_it(monkeypatch):
    class Host:
        def get(self, group, name):
            raise KeyError(name)

    monkeypatch.setattr("shape.plugins.host.default_host", lambda: Host())
    with pytest.raises(
        ShapeError, match="no postgres sink is installed for postgres://h/d.*postgres\\]"
    ):
        seed_target("retail", "postgres://h/d", scale="tiny")
    with pytest.raises(ShapeError, match="no sqlserver sink.*sqlserver\\]"):
        seed_target("retail", "mssql://h/d", scale="tiny")


def test_a_sink_that_cannot_list_tables_works_for_append_but_not_create():
    class Blind:
        name = "mysql"

        def __init__(self):
            self.calls = 0

        def write(self, uri, table, batches, **options):
            self.calls += 1
            return sum(b.num_rows for b in batches)

    blind = Blind()
    with pytest.raises(ShapeError, match="cannot say which tables exist.*append or truncate"):
        seed_target("retail", "mysql://h/d", scale="tiny", sinks={"mysql": blind})
    assert blind.calls == 0
    seed_target("retail", "mysql://h/d", scale="tiny", mode="append", sinks={"mysql": blind})
    assert blind.calls == 9


def test_a_failed_write_names_the_tables_already_written():
    db = Db()
    with pytest.raises(
        ShapeError, match="seeding table order failed: disk full; tables written: "
    ) as err:
        seed(db, sinks={"postgres": FakeSink(db, fail_on="order")})
    assert "customer, address, product_category, product, promotion, store" in str(err.value)
    assert "order_line" not in str(err.value).split("tables written:")[1]


def test_the_sql_server_check_goes_through_its_connection_helper(monkeypatch):
    asked = []

    class SqlConnection:
        def __init__(self, conn_string, credential, connection, connect, warehouse):
            asked.append(conn_string)

        def table_exists(self, schema, table):
            asked.append((schema, table))
            return table == "store"

        def close(self):
            asked.append("closed")

    module = types.ModuleType("fakeplugin.sqldb")
    module.SqlConnection = SqlConnection
    monkeypatch.setitem(sys.modules, "fakeplugin.sqldb", module)

    class SqlServerSink:
        name = "sqlserver"
        _connect = None
        written: list[str] = []

        def _parse(self, uri):
            return {"schema": "test"}, "parts"

        def _connection_string(self, parts, conn_opts, credential):
            return "DRIVER=x;SERVER=h"

        def write(self, uri, table, batches, **options):
            self.written.append(table)
            return 1

    SqlServerSink.__module__ = "fakeplugin.sinks"
    sink = SqlServerSink()
    with pytest.raises(SeedRefused, match="table\\(s\\) store already exist"):
        seed_target("retail", "mssql://h/d", scale="tiny", sinks={"sqlserver": sink})
    assert (
        asked[0] == "DRIVER=x;SERVER=h" and ("test", "customer") in asked and asked[-1] == "closed"
    )
    assert sink.written == []


# ---- the SQL script target --------------------------------------------------------------------


def test_a_script_target_writes_one_numbered_script_per_table_in_foreign_key_order(tmp_path):
    result = seed_target("retail", f"sql://{tmp_path / 'out'}", scale="tiny")
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == [Path(f).name for f in result.files]
    assert files[0] == "01_customer.sql" and files[-1] == "09_return.sql"
    order = [n.split("_", 1)[1][:-4] for n in files]
    for parent, child in fk_pairs():
        assert order.index(parent) < order.index(child)
    text = (tmp_path / "out" / "07_order.sql").read_text()
    assert "CREATE TABLE [order]" in text and "INSERT INTO [order]" in text
    assert "DROP TABLE" not in text and "seed 42, scale tiny, mode create" in text
    assert result.written["order"] == 100


def test_script_target_refuses_existing_scripts_in_create_mode_and_writes_nothing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "07_order.sql").write_text("-- mine\n")
    with pytest.raises(SeedRefused, match="07_order.sql already exist.*nothing was written"):
        seed_target("retail", f"sql://{out}", scale="tiny")
    assert [p.name for p in out.iterdir()] == ["07_order.sql"]
    assert (out / "07_order.sql").read_text() == "-- mine\n"


def test_a_truncate_rerun_with_the_same_seed_gives_byte_identical_scripts(tmp_path):
    out = tmp_path / "out"
    seed_target("retail", f"sql://{out}", scale="tiny", seed=3, mode="truncate")
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    assert b"DROP TABLE" in first["07_order.sql"]  # a rerun replaces the table
    seed_target("retail", f"sql://{out}", scale="tiny", seed=3, mode="truncate")
    assert {p.name: p.read_bytes() for p in out.iterdir()} == first
    seed_target("retail", f"sql://{out}", scale="tiny", seed=4, mode="truncate")
    assert {p.name: p.read_bytes() for p in out.iterdir()} != first


def test_append_scripts_carry_rows_without_a_table_definition(tmp_path):
    seed_target("retail", f"sql://{tmp_path}", scale="tiny", mode="append")
    text = (tmp_path / "07_order.sql").read_text()
    assert "INSERT INTO" in text and "CREATE TABLE" not in text and "DROP TABLE" not in text


def test_the_scripts_load_in_order_into_a_real_database(tmp_path):
    """The postgres-dialect scripts run in file order against SQLite with foreign keys on the
    data: every child row finds its parent."""
    result = seed_target("retail", f"sql://{tmp_path}?dialect=postgres", scale="tiny")
    db = sqlite3.connect(":memory:")
    for script in sorted(glob.glob(str(tmp_path / "*.sql"))):
        db.executescript(Path(script).read_text())
    for name, rows in result.written.items():
        assert db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] == rows
    orphans = db.execute(
        'SELECT COUNT(*) FROM "order" o LEFT JOIN customer c ON c.customer_id = o.customer_id '
        "WHERE c.customer_id IS NULL"
    ).fetchone()[0]
    assert orphans == 0


def test_script_target_dialects_and_bad_targets(tmp_path):
    seed_target("retail", f"sql://{tmp_path / 'p'}?dialect=postgres", scale="tiny")
    assert 'CREATE TABLE "order"' in (tmp_path / "p" / "07_order.sql").read_text()
    seed_target("retail", f"sql://{tmp_path / 'm'}?dialect=mysql", scale="tiny")
    assert "CREATE TABLE `order`" in (tmp_path / "m" / "07_order.sql").read_text()
    with pytest.raises(ShapeError, match="unknown dialect 'oracle'"):
        seed_target("retail", f"sql://{tmp_path / 'x'}?dialect=oracle", scale="tiny")
    with pytest.raises(ShapeError, match="unknown parameter 'mode'"):
        seed_target("retail", f"sql://{tmp_path / 'x'}?mode=1", scale="tiny")
    with pytest.raises(ShapeError, match="give the directory"):
        seed_target("retail", "sql://", scale="tiny")
    assert not (tmp_path / "x").exists()


def test_the_sql_server_append_check_reads_the_keys_through_its_connection_helper(monkeypatch):
    statements = []

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchmany(self, size):
            out, self.rows = self.rows[:size], self.rows[size:]
            return out

        def close(self):
            pass

    class SqlConnection:
        def __init__(self, conn_string, credential, connection, connect, warehouse):
            pass

        def table_exists(self, schema, table):
            return table == "promotion"

        def execute(self, sql, *params):
            statements.append(sql)
            return Cursor([(3,), (500,)])

        def close(self):
            statements.append("closed")

    module = types.ModuleType("fakeplugin2.sqldb")
    module.SqlConnection = SqlConnection
    monkeypatch.setitem(sys.modules, "fakeplugin2.sqldb", module)

    class SqlServerSink:
        name = "sqlserver"
        _connect = None
        written: list[str] = []

        def _parse(self, uri):
            return {}, "parts"

        def _connection_string(self, parts, conn_opts, credential):
            return "DRIVER=x;SERVER=h"

        def write(self, uri, table, batches, **options):
            self.written.append(table)
            return 1

    SqlServerSink.__module__ = "fakeplugin2.sinks"
    sink = SqlServerSink()
    with pytest.raises(SeedRefused, match=r"promotion \(promotion_id = 3\)") as err:
        seed_target("retail", "mssql://h/d", scale="tiny", mode="append", sinks={"sqlserver": sink})
    assert "1 more" not in str(err.value)  # 500 is not a generated key
    assert statements == ["SELECT [promotion_id] FROM [dbo].[promotion]", "closed"]
    assert sink.written == []
