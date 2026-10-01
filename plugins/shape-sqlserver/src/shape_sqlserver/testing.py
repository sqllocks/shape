"""Test doubles for code that talks to SQL Server through DB-API (``pyodbc``).

Standard library only, and importable on its own (the parity harness loads this file by path).
``FakeConnection`` answers the catalog queries of :mod:`shape_sqlserver.sql` and the
``SELECT TOP n`` / ``SELECT ... ORDER BY`` reads from in-memory tables, with the same row and
value types ``pyodbc`` returns, so profiling and reading run end to end with no server.
:func:`scenario` builds deterministic databases for tests and for the nightly emulator job
(:func:`ddl` and :func:`insert_rows` load the same data into a real SQL Server).
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
import zlib
from collections import namedtuple
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class FakeColumn:
    name: str
    type_name: str
    nullable: bool = True
    identity: bool = False
    max_length: int = 0
    precision: int = 0
    scale: int = 0


@dataclass(slots=True)
class FakeTable:
    name: str
    columns: list[FakeColumn]
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    schema: str = "dbo"
    primary_key: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FakeForeignKey:
    name: str
    child_table: str
    child_columns: tuple[str, ...]
    parent_table: str
    parent_columns: tuple[str, ...]
    schema: str = "dbo"


_TopRead = re.compile(
    r"^\s*SELECT\s+(?:TOP\s+(?P<top>\d+)\s+)?(?P<cols>.+?)\s+FROM\s+"
    r"\[(?P<schema>(?:[^\]]|\]\])+)\]\.\[(?P<table>(?:[^\]]|\]\])+)\]"
    r"(?:\s+ORDER\s+BY\s+(?P<order>.+?))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_SPREAD = re.compile(
    r"^\s*\(CAST\(CHECKSUM\((?P<cols>[^)]*)\) AS bigint\) \* (?P<mul>\d+)\) % (?P<mod>\d+)",
    re.IGNORECASE,
)
_Bracketed = re.compile(r"\[((?:[^\]]|\]\])+)\]")


def checksum(values: tuple[Any, ...]) -> int:
    """A stand-in for T-SQL ``CHECKSUM(a, b, ...)``: a signed 32-bit hash of the values. It is
    deterministic, but it is not the server's function (which returns an integer unchanged):
    tests that need the server's own order run against a real server."""
    h = zlib.crc32(repr(values).encode())
    return h - (1 << 32) if h >= 1 << 31 else h


def spread_hash(values: tuple[Any, ...], mul: int = 1327217885, mod: int = 2147483647) -> int:
    """``(CAST(CHECKSUM(values) AS bigint) * mul) % mod`` as T-SQL computes it: the remainder
    takes the sign of the dividend."""
    product = checksum(values) * mul
    return product % mod if product >= 0 else -((-product) % mod)


def _row_type(fields: str) -> Any:
    """A row class with attribute access, like ``pyodbc.Row``."""
    return namedtuple("Row", fields)


def _sort_key(position: int) -> Callable[[tuple[Any, ...]], tuple[bool, Any]]:
    """NULLs first, like SQL Server."""
    return lambda row: (row[position] is not None, row[position])


def _names(text: str) -> list[str]:
    return [m.replace("]]", "]") for m in _Bracketed.findall(text)]


class FakeCursor:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn
        self._rows: list[Any] = []
        self._pos = 0
        self.description: list[tuple[Any, ...]] | None = None
        self.closed = False

    def _set(self, names: Sequence[str], rows: Iterable[Any]) -> None:
        self.description = [(n, None, None, None, None, None, None) for n in names]
        self._rows = list(rows)
        self._pos = 0

    def execute(self, sql: str, params: Sequence[Any] = ()) -> FakeCursor:
        if self.closed:
            raise RuntimeError("cursor is closed")
        self._conn.statements.append(sql.strip())
        self._conn.dispatch(self, sql, tuple(params))
        return self

    def fetchall(self) -> list[Any]:
        rows, self._pos = self._rows[self._pos :], len(self._rows)
        return rows

    def fetchmany(self, size: int = 1) -> list[Any]:
        rows = self._rows[self._pos : self._pos + size]
        self._pos += len(rows)
        return rows

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """An in-memory SQL Server: ``tables``, declared ``foreign_keys``, and what to break.

    ``statements`` records every SQL text executed. ``fail_reads`` names tables whose data
    queries raise (catalog queries still work).
    """

    def __init__(
        self,
        tables: Sequence[FakeTable],
        foreign_keys: Sequence[FakeForeignKey] = (),
        *,
        fail_reads: Iterable[str] = (),
        row_counts: dict[str, int] | None = None,
    ) -> None:
        self.tables = list(tables)
        self.foreign_keys = list(foreign_keys)
        self.fail_reads = set(fail_reads)
        self.row_counts = row_counts or {}
        self.statements: list[str] = []
        self.closed = False

    def cursor(self) -> FakeCursor:
        if self.closed:
            raise RuntimeError("connection is closed")
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True

    def _object_id(self, table: FakeTable) -> int:
        return 1000 + self.tables.index(table)

    def _by_id(self, object_id: int) -> FakeTable:
        return self.tables[int(object_id) - 1000]

    def dispatch(self, cur: FakeCursor, sql: str, params: tuple[Any, ...]) -> None:
        text = " ".join(sql.split())
        if "FROM sys.tables t JOIN sys.schemas s" in text and "sys.partitions" in text:
            rows_t = _row_type("schema_name table_name row_count")
            cur._set(
                rows_t._fields,
                [
                    rows_t(
                        t.schema,
                        t.name,
                        self.row_counts.get(t.name, len(t.rows)),
                    )
                    for t in sorted(self.tables, key=lambda x: x.name)
                    if t.schema == params[0]
                ],
            )
        elif text.startswith("SELECT s.name AS schema_name, t.name AS table_name, t.object_id"):
            rows_t = _row_type("schema_name table_name object_id")
            cur._set(
                rows_t._fields,
                [
                    rows_t(t.schema, t.name, self._object_id(t))
                    for t in sorted(self.tables, key=lambda x: x.name)
                    if t.schema == params[0]
                ],
            )
        elif "FROM sys.columns c JOIN sys.types tp" in text:
            rows_t = _row_type(
                "column_name type_name max_length precision scale "
                "is_nullable is_identity column_id",
            )
            table = self._by_id(params[0])
            cur._set(
                rows_t._fields,
                [
                    rows_t(
                        c.name,
                        c.type_name,
                        c.max_length,
                        c.precision,
                        c.scale,
                        c.nullable,
                        c.identity,
                        i + 1,
                    )
                    for i, c in enumerate(table.columns)
                ],
            )
        elif "FROM sys.key_constraints kc" in text:
            rows_t = _row_type("column_name")
            table = self._by_id(params[0])
            cur._set(rows_t._fields, [rows_t(c) for c in table.primary_key])
        elif "FROM sys.foreign_keys fk" in text:
            rows_t = _row_type(
                "fk_name child_table child_column parent_table parent_column ordinal"
            )
            out = []
            for fk in sorted(self.foreign_keys, key=lambda f: f.name):
                if fk.schema != params[0]:
                    continue
                for i, (cc, pc) in enumerate(zip(fk.child_columns, fk.parent_columns, strict=True)):
                    out.append(rows_t(fk.name, fk.child_table, cc, fk.parent_table, pc, i + 1))
            cur._set(rows_t._fields, out)
        else:
            self._read(cur, text)

    def _read(self, cur: FakeCursor, text: str) -> None:
        m = _TopRead.match(text)
        if not m:
            raise RuntimeError(f"FakeConnection cannot run: {text}")
        name = m["table"].replace("]]", "]")
        table = next((t for t in self.tables if t.name == name and t.schema == m["schema"]), None)
        if table is None:
            raise RuntimeError(f"Invalid object name '{m['schema']}.{name}'.")
        if name in self.fail_reads:
            raise RuntimeError(f"The SELECT permission was denied on the object '{name}'.")
        index = {c.name: i for i, c in enumerate(table.columns)}
        wanted = list(index) if m["cols"].strip() == "*" else _names(m["cols"])
        rows = list(table.rows)
        if m["order"]:
            order = m["order"]
            keys = _names(order)
            hashed = _SPREAD.match(order)
            if hashed:  # ORDER BY (CAST(CHECKSUM(a, b) AS bigint) * m) % p, c: hash, then columns
                keys = keys[len(_names(hashed["cols"])) :]
            for key in reversed(keys):
                rows.sort(key=_sort_key(index[key]))
            if hashed:
                cols = [index[c] for c in _names(hashed["cols"])]
                mul, mod = int(hashed["mul"]), int(hashed["mod"])
                rows.sort(key=lambda r: spread_hash(tuple(r[i] for i in cols), mul, mod))
        if m["top"]:
            rows = rows[: int(m["top"])]
        pick = [index[c] for c in wanted]
        cur._set(wanted, [tuple(r[i] for i in pick) for r in rows])


# --- deterministic data -------------------------------------------------------------------


class Rng:
    """A tiny splitmix64 generator, identical on every Python version and platform."""

    def __init__(self, seed: int) -> None:
        self._s = seed & 0xFFFFFFFFFFFFFFFF

    def u64(self) -> int:
        self._s = (self._s + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self._s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        return self.u64() % n

    def unit(self) -> float:
        return (self.u64() >> 11) / float(1 << 53)

    def pick(self, items: Sequence[Any], weights: Sequence[int] | None = None) -> Any:
        if weights is None:
            return items[self.below(len(items))]
        point = self.below(sum(weights))
        for item, w in zip(items, weights, strict=True):
            if point < w:
                return item
            point -= w
        return items[-1]

    def maybe_null(self, value: Any, percent: int) -> Any:
        return None if self.below(100) < percent else value


def _money(cents: int) -> Decimal:
    return Decimal(f"{cents // 100}.{cents % 100:02d}")


_BASE = dt.datetime(2020, 1, 1, 8, 0, 0)


def _col(name: str, type_name: str, **kw: Any) -> FakeColumn:
    return FakeColumn(name, type_name, **kw)


def _retail(scale: int, rng: Rng) -> tuple[list[FakeTable], list[FakeForeignKey]]:
    n_cust, n_prod, n_ord, n_item = 2500 * scale, 300 * scale, 4000 * scale, 6000 * scale
    segments = ["consumer", "corporate", "home office", "small business"]
    categories = [f"category {i}" for i in range(9)]
    statuses = ["new", "paid", "shipped", "delivered", "returned"]
    customers = []
    for i in range(1, n_cust + 1):
        customers.append(
            (
                i,
                f"Customer {i}",
                f"user{i}@example.test",
                rng.pick(segments, [50, 25, 15, 10]),
                rng.maybe_null(_money(rng.below(900000)), 8),
                int(300 + rng.below(550)),
                _BASE + dt.timedelta(minutes=rng.below(500000)),
                rng.maybe_null(dt.date(1950, 1, 1) + dt.timedelta(days=rng.below(20000)), 5),
                rng.maybe_null(rng.below(100) < 60, 2),
                rng.maybe_null(int(rng.unit() ** 3 * 5000), 10),
                f"R{rng.below(15):02d}",
                str(uuid.UUID(int=rng.u64() << 64 | rng.u64())),
            )
        )
    products = []
    for i in range(1, n_prod + 1):
        products.append(
            (
                i,
                f"SKU-{i:05d}",
                rng.pick(categories),
                _money(100 + rng.below(50000)),
                rng.maybe_null(round(0.1 + rng.unit() * 30, 3), 15),
                round(1 + rng.unit() * 4, 1),
                rng.below(255),
                rng.maybe_null(f"Product {i} " + "x" * rng.below(40), 20),
            )
        )
    orders = []
    for i in range(1, n_ord + 1):
        orders.append(
            (
                10_000_000_000 + i,
                1 + rng.below(n_cust),
                dt.date(2022, 1, 1) + dt.timedelta(days=rng.below(900)),
                rng.pick(statuses, [10, 30, 25, 30, 5]),
                _money(500 + rng.below(300000)),
                rng.maybe_null(dt.time(rng.below(24), rng.below(60), rng.below(60)), 30),
                rng.maybe_null(f"note {rng.below(200)}", 70),
            )
        )
    items = []
    line = 0
    for i in range(n_item):
        if i % 3 == 0:
            line = 0
        line += 1
        items.append(
            (
                10_000_000_000 + 1 + (i // 3) % n_ord,
                line,
                1 + rng.below(n_prod),
                1 + rng.below(9),
                _money(100 + rng.below(50000)),
                rng.maybe_null(round(rng.unit() * 0.3, 2), 60),
            )
        )
    tables = [
        FakeTable(
            "customer",
            [
                _col(
                    "customer_id", "int", nullable=False, identity=True, max_length=4, precision=10
                ),
                _col("name", "nvarchar", max_length=160),
                _col("email", "varchar", max_length=120),
                _col("segment", "varchar", max_length=20),
                _col("balance", "decimal", max_length=9, precision=12, scale=2),
                _col("credit_score", "smallint", max_length=2, precision=5),
                _col("created_at", "datetime2", max_length=8, precision=27, scale=7),
                _col("birth_date", "date", max_length=3, precision=10),
                _col("is_active", "bit", max_length=1, precision=1),
                _col("loyalty_points", "int", max_length=4, precision=10),
                _col("region_code", "char", max_length=3),
                _col("guid", "uniqueidentifier", max_length=16),
            ],
            customers,
            primary_key=("customer_id",),
        ),
        FakeTable(
            "product",
            [
                _col("product_id", "int", nullable=False, max_length=4, precision=10),
                _col("sku", "varchar", max_length=20),
                _col("category", "varchar", max_length=30),
                _col("price", "money", max_length=8, precision=19, scale=4),
                _col("weight_kg", "float", max_length=8, precision=53),
                _col("rating", "real", max_length=4, precision=24),
                _col("in_stock", "tinyint", max_length=1, precision=3),
                _col("description", "nvarchar", max_length=-1),
            ],
            products,
            primary_key=("product_id",),
        ),
        FakeTable(
            "orders",
            [
                _col("order_id", "bigint", nullable=False, max_length=8, precision=19),
                _col("customer_id", "int", nullable=False, max_length=4, precision=10),
                _col("order_date", "date", max_length=3, precision=10),
                _col("status", "varchar", max_length=12),
                _col("total", "decimal", max_length=9, precision=10, scale=2),
                _col("ship_time", "time", max_length=5, precision=16, scale=7),
                _col("notes", "varchar", max_length=200),
            ],
            orders,
            primary_key=("order_id",),
        ),
        FakeTable(
            "order_item",
            [
                _col("order_id", "bigint", nullable=False, max_length=8, precision=19),
                _col("line_no", "int", nullable=False, max_length=4, precision=10),
                _col("product_id", "int", nullable=False, max_length=4, precision=10),
                _col("quantity", "int", max_length=4, precision=10),
                _col("unit_price", "decimal", max_length=9, precision=10, scale=2),
                _col("discount", "float", max_length=8, precision=53),
            ],
            items,
            primary_key=("order_id", "line_no"),
        ),
        FakeTable(
            "audit_log",
            [
                _col("id", "int", nullable=False, identity=True, max_length=4, precision=10),
                _col("event", "varchar", max_length=50),
                _col("at", "datetime2", max_length=8, precision=27, scale=7),
            ],
            [],
            primary_key=("id",),
        ),
        FakeTable(
            "config",
            [_col("name", "varchar", nullable=False, max_length=30), _col("value", "int")],
            [("version", 3)],
            primary_key=("name",),
        ),
    ]
    fks = [
        FakeForeignKey(
            "fk_orders_customer", "orders", ("customer_id",), "customer", ("customer_id",)
        ),
        FakeForeignKey("fk_item_orders", "order_item", ("order_id",), "orders", ("order_id",)),
        FakeForeignKey(
            "fk_item_product", "order_item", ("product_id",), "product", ("product_id",)
        ),
    ]
    return tables, fks


def _warehouse(scale: int, rng: Rng, *, empty: bool, contained: bool) -> list[FakeTable]:
    """Tables with no declared keys, as a Fabric warehouse has. ``contained`` makes the fact
    table's key columns hold only dimension keys; otherwise they hold unrelated numbers."""
    n_c, n_p, n_f = (0, 0, 0) if empty else (120 * scale, 40 * scale, 900 * scale)
    regions = ["north", "south", "east", "west"]
    dimc = [(i, f"Name {i}", rng.pick(regions)) for i in range(1, n_c + 1)]
    dimp = [(i, f"Widget {i}", f"line {i % 5}") for i in range(1, n_p + 1)]
    fact = []
    for i in range(1, n_f + 1):
        c = 1 + rng.below(n_c) if contained else 900_000 + rng.below(50)
        p = 1 + rng.below(n_p) if contained else 800_000 + rng.below(30)
        fact.append(
            (
                i,
                c,
                p,
                _money(100 + rng.below(90000)),
                dt.date(2024, 1, 1) + dt.timedelta(days=rng.below(300)),
            )
        )
    return [
        FakeTable(
            "dimcustomer",
            [
                _col("customer_key", "int", nullable=False, max_length=4, precision=10),
                _col("customer_name", "varchar", max_length=100),
                _col("region", "varchar", max_length=20),
            ],
            dimc,
        ),
        FakeTable(
            "dimproduct",
            [
                _col("product_key", "int", nullable=False, max_length=4, precision=10),
                _col("product_name", "varchar", max_length=100),
                _col("product_line", "varchar", max_length=20),
            ],
            dimp,
        ),
        FakeTable(
            "factsales",
            [
                _col("sale_id", "int", nullable=False, max_length=4, precision=10),
                _col("customer_key", "int", max_length=4, precision=10),
                _col("product_key", "int", max_length=4, precision=10),
                _col("amount", "decimal", max_length=9, precision=12, scale=2),
                _col("sale_date", "date", max_length=3, precision=10),
            ],
            fact,
        ),
    ]


def _name_only(scale: int, rng: Rng) -> list[FakeTable]:
    """No keys, and data that does not show a relationship; only the names do."""
    n_c, n_o = 60 * scale, 200 * scale
    customer = [(i, f"c{i}") for i in range(1, n_c + 1)]
    orders = [(500_000 + i, 700_000 + rng.below(40), _money(rng.below(9000))) for i in range(n_o)]
    return [
        FakeTable(
            "customer",
            [
                _col("id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=10),
            ],
            customer,
        ),
        FakeTable(
            "orders",
            [
                _col("order_id", "int", nullable=False, precision=10),
                _col("customer_id", "int", precision=10),
                _col("amount", "decimal", precision=10, scale=2),
            ],
            orders,
        ),
    ]


def _id_named(scale: int, rng: Rng) -> list[FakeTable]:
    """No declared keys, ``*_id`` columns whose values all exist in the parent table."""
    n_c, n_o = 80 * scale, 300 * scale
    customer = [(i, f"c{i}") for i in range(1, n_c + 1)]
    orders = [(i, 1 + rng.below(n_c), _money(rng.below(9000))) for i in range(1, n_o + 1)]
    return [
        FakeTable(
            "customer",
            [
                _col("customer_id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=10),
            ],
            customer,
        ),
        FakeTable(
            "orders",
            [
                _col("order_id", "int", nullable=False, precision=10),
                _col("customer_id", "int", precision=10),
                _col("amount", "decimal", precision=10, scale=2),
            ],
            orders,
        ),
    ]


def _mixed_keys(scale: int, rng: Rng) -> tuple[list[FakeTable], list[FakeForeignKey]]:
    """One declared foreign key (``orders.customer_id``) and two undeclared ones beside it:
    ``orders.product_id`` (every value exists in ``product``: the data shows it) and
    ``orders.region_key`` (numbers no region has; only the name suggests it)."""
    n_r, n_c, n_p, n_o = 8, 60 * scale, 25 * scale, 240 * scale
    region = [(i, f"region {i}") for i in range(1, n_r + 1)]
    customer = [(i, f"c{i}") for i in range(1, n_c + 1)]
    product = [(i, f"p{i}") for i in range(1, n_p + 1)]
    orders = [
        (i, 1 + rng.below(n_c), 1 + rng.below(n_p), 700 + rng.below(9)) for i in range(1, n_o + 1)
    ]
    tables = [
        FakeTable(
            "region",
            [
                _col("region_id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=20),
            ],
            region,
            primary_key=("region_id",),
        ),
        FakeTable(
            "customer",
            [
                _col("customer_id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=20),
            ],
            customer,
            primary_key=("customer_id",),
        ),
        FakeTable(
            "product",
            [
                _col("product_id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=20),
            ],
            product,
            primary_key=("product_id",),
        ),
        FakeTable(
            "orders",
            [
                _col("order_id", "int", nullable=False, precision=10),
                _col("customer_id", "int", nullable=False, precision=10),
                _col("product_id", "int", precision=10),
                _col("region_key", "int", precision=10),
            ],
            orders,
            primary_key=("order_id",),
        ),
    ]
    fks = [
        FakeForeignKey(
            "fk_orders_customer", "orders", ("customer_id",), "customer", ("customer_id",)
        )
    ]
    return tables, fks


def _key_names(scale: int, rng: Rng) -> list[FakeTable]:
    """No declared keys and data that shows no relationship (only names do). ``invoices.valid``
    and ``invoices.paid`` end in ``id`` but are not ids (tables ``val`` and ``pa`` exist to be
    matched wrongly); ``invoices.customer_id`` comes first but is a foreign key, so the key of
    ``invoices`` is ``invoice_id``; ``notes`` has only a foreign key and a text column, so it
    has no key at all."""
    n_c, n_i, n_n = 40 * scale, 150 * scale, 30 * scale
    customer = [(i, f"c{i}") for i in range(1, n_c + 1)]
    invoices = [
        (9000 + rng.below(n_c), i, rng.below(2), rng.below(2), f"inv {i}")
        for i in range(1, n_i + 1)
    ]
    notes = [(f"note {i % 7}", 9000 + rng.below(n_c)) for i in range(n_n)]
    flags = [(0, "no"), (1, "yes")]
    return [
        FakeTable(
            "customer",
            [
                _col("customer_id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=20),
            ],
            customer,
        ),
        FakeTable(
            "invoices",
            [
                _col("customer_id", "int", precision=10),
                _col("invoice_id", "int", nullable=False, precision=10),
                _col("valid", "int", precision=10),
                _col("paid", "int", precision=10),
                _col("memo", "varchar", max_length=20),
            ],
            invoices,
        ),
        FakeTable(
            "notes",
            [_col("note", "varchar", max_length=20), _col("customer_id", "int", precision=10)],
            notes,
        ),
        FakeTable(
            "pa",
            [
                _col("id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=5),
            ],
            flags,
        ),
        FakeTable(
            "val",
            [
                _col("id", "int", nullable=False, precision=10),
                _col("label", "varchar", max_length=5),
            ],
            flags,
        ),
    ]


def _no_keys(
    build: Callable[[int, Rng], list[FakeTable]],
) -> Callable[[int, Rng], tuple[list[FakeTable], list[FakeForeignKey]]]:
    def make(scale: int, rng: Rng) -> tuple[list[FakeTable], list[FakeForeignKey]]:
        return build(scale, rng), []

    return make


def _warehouse_full(scale: int, rng: Rng) -> list[FakeTable]:
    return _warehouse(scale, rng, empty=False, contained=True)


def _warehouse_empty(scale: int, rng: Rng) -> list[FakeTable]:
    return _warehouse(scale, rng, empty=True, contained=True)


SCENARIOS: dict[str, Callable[[int, Rng], tuple[list[FakeTable], list[FakeForeignKey]]]] = {
    "retail": _retail,
    "warehouse": _no_keys(_warehouse_full),
    "warehouse_empty": _no_keys(_warehouse_empty),
    "name_only": _no_keys(_name_only),
    "id_named": _no_keys(_id_named),
    "mixed_keys": _mixed_keys,
    "key_names": _no_keys(_key_names),
}


def scenario(name: str, scale: int = 1, seed: int = 7) -> FakeConnection:
    """A deterministic database: ``retail`` (declared keys, tables larger than a 1000-row
    sample, an empty and a one-row table), ``warehouse`` (no keys; key columns named ``*_key``,
    linked by name), ``warehouse_empty`` (no keys, no rows) or ``name_only`` (no keys; only
    column names suggest a relationship) or ``id_named`` (no keys; ``*_id`` columns whose
    values all exist in the parent table), ``mixed_keys`` (one declared foreign key, and
    undeclared ones beside it) or ``key_names`` (no keys; ``paid``/``valid`` columns and tables
    named like their stems, a foreign key first in its table, a table with no key column)."""
    tables, fks = SCENARIOS[name](scale, Rng(seed))
    return FakeConnection(tables, fks)


# --- loading the same data into a real server --------------------------------------------


def _column_ddl(c: FakeColumn) -> str:
    t = c.type_name
    if t in ("varchar", "nvarchar", "char", "nchar", "varbinary", "binary"):
        size = (
            "max"
            if c.max_length == -1
            else str(c.max_length // 2 if t.startswith("n") else c.max_length)
        )
        t = f"{t}({size})"
    elif t in ("decimal", "numeric"):
        t = f"{t}({c.precision},{c.scale})"
    elif t in ("datetime2", "time"):
        t = f"{t}({c.scale or 7})"
    ident = " IDENTITY(1,1)" if c.identity else ""
    return f"[{c.name}] {t}{ident} {'NULL' if c.nullable else 'NOT NULL'}"


def ddl(conn: FakeConnection, schema: str = "dbo") -> list[str]:
    """``CREATE TABLE`` / ``ALTER TABLE ... FOREIGN KEY`` statements for ``conn``'s tables."""
    out = []
    for t in conn.tables:
        cols = [_column_ddl(c) for c in t.columns]
        if t.primary_key:
            cols.append(f"PRIMARY KEY ({', '.join(f'[{c}]' for c in t.primary_key)})")
        out.append(f"CREATE TABLE [{schema}].[{t.name}] ({', '.join(cols)})")
    for fk in conn.foreign_keys:
        child = ", ".join(f"[{c}]" for c in fk.child_columns)
        parent = ", ".join(f"[{c}]" for c in fk.parent_columns)
        out.append(
            f"ALTER TABLE [{schema}].[{fk.child_table}] ADD CONSTRAINT [{fk.name}] FOREIGN KEY "
            f"({child}) REFERENCES [{schema}].[{fk.parent_table}] ({parent})"
        )
    return out


def insert_rows(cursor: Any, table: FakeTable, schema: str = "dbo", batch: int = 500) -> None:
    """Insert ``table``'s rows through a pyodbc cursor (identity columns keep their values)."""
    if not table.rows:
        return
    cols = ", ".join(f"[{c.name}]" for c in table.columns)
    marks = ", ".join("?" for _ in table.columns)
    # a test helper: schema and table names come from the fixture, never from a user
    sql = f"INSERT INTO [{schema}].[{table.name}] ({cols}) VALUES ({marks})"  # nosec B608
    identity = any(c.identity for c in table.columns)
    if identity:
        cursor.execute(f"SET IDENTITY_INSERT [{schema}].[{table.name}] ON")
    for i in range(0, len(table.rows), batch):
        cursor.executemany(sql, table.rows[i : i + batch])
    if identity:
        cursor.execute(f"SET IDENTITY_INSERT [{schema}].[{table.name}] OFF")
