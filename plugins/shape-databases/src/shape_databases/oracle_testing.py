"""Small Oracle fake exercising bound DML and catalog answers without a driver/account."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class FakeOracle:
    """An injectable connector with observable statements, bindings and failures."""

    def __init__(
        self,
        *,
        version: str = "23.5.0.0.0",
        max_string_size: str = "STANDARD",
        fail_connect: str | None = None,
        fail_dml_call: int | None = None,
        catalog_answers: Mapping[str, list[Any]] | None = None,
        arrow_batches: list[Any] | None = None,
    ) -> None:
        self.version = version
        self.max_string_size = max_string_size
        self.fail_connect = fail_connect
        self.fail_dml_call = fail_dml_call
        self.catalog_answers = dict(catalog_answers or {})
        self.arrow_batches = arrow_batches or []
        self.connections: list[dict[str, Any]] = []
        self.statements: list[tuple[str, Any]] = []
        self.ddl: list[str] = []
        self.dml: list[tuple[str, list[Any]]] = []
        self.input_sizes: list[tuple[Any, ...]] = []
        self.version_reads = 0
        self.parameter_reads = 0
        self.commits = 0
        self.rollbacks = 0
        self.tables: set[str] = set()
        self.fetch_sizes: list[int] = []

    def connect(self, **kwargs: Any) -> FakeOracleConnection:
        if self.fail_connect:
            raise RuntimeError(self.fail_connect)
        self.connections.append(kwargs)
        return FakeOracleConnection(self)


class FakeOracleConnection:
    def __init__(self, server: FakeOracle) -> None:
        self.server = server

    @property
    def version(self) -> str:
        self.server.version_reads += 1
        return self.server.version

    def cursor(self) -> FakeOracleCursor:
        return FakeOracleCursor(self.server)

    def commit(self) -> None:
        self.server.commits += 1

    def rollback(self) -> None:
        self.server.rollbacks += 1

    def close(self) -> None:
        pass

    def fetch_df_batches(self, statement: str, parameters: Any = None, *, size: int) -> Any:
        self.server.statements.append((statement, parameters))
        self.server.fetch_sizes.append(size)
        yield from self.server.arrow_batches


class FakeOracleCursor:
    def __init__(self, server: FakeOracle) -> None:
        self.server = server
        self.rows: list[Any] = []
        self.arraysize = 100
        self.outputtypehandler: Any = None

    def execute(self, statement: str, parameters: Any = None, **kwargs: Any) -> None:
        self.server.statements.append((statement, parameters))
        upper = statement.upper()
        self.rows = []
        for key, rows in self.server.catalog_answers.items():
            if key in statement:
                self.rows = list(rows)
                return
        if "V$PARAMETER" in upper:
            self.server.parameter_reads += 1
            self.rows = [(self.server.max_string_size,)]
        elif upper.startswith("CREATE TABLE"):
            self.server.ddl.append(statement)
            self.server.tables.add(statement.split(" (", 1)[0].removeprefix("CREATE TABLE "))
        elif upper.startswith("DROP TABLE"):
            self.server.tables.discard(statement.removeprefix("DROP TABLE "))
        elif "FROM ALL_TABLES" in upper and upper.startswith("SELECT 1"):
            name = parameters[1]
            if any(table.endswith('"' + name + '"') for table in self.server.tables):
                self.rows = [(1,)]

    def executemany(self, statement: str, rows: Any) -> None:
        values = list(rows)
        self.server.dml.append((statement, values))
        if self.server.fail_dml_call == len(self.server.dml):
            raise RuntimeError('ORA-00001: unique constraint "fixture_pk" violated')

    def setinputsizes(self, *args: Any) -> None:
        self.server.input_sizes.append(args)

    def fetchone(self) -> Any:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[Any]:
        rows, self.rows = self.rows, []
        return rows

    def fetchmany(self, size: int) -> list[Any]:
        self.server.fetch_sizes.append(size)
        rows, self.rows = self.rows[:size], self.rows[size:]
        return rows

    def close(self) -> None:
        pass
