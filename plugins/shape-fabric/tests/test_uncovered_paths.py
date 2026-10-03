"""Paths the other tests did not reach: the lakehouse source's delegation, WriteResult's
summary, and the urllib transport of the Kusto client."""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pyarrow as pa
import pytest
from shape_fabric import kusto
from shape_fabric.errors import WriteError, WriteResult
from shape_fabric.source import LakehouseSource

from shape.builtins.sources import azure, delta


class _Recorder:
    def __init__(self, kind: str, seen: list[tuple[str, str, dict[str, Any]]]) -> None:
        self.kind = kind
        self.seen = seen

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        self.seen.append((self.kind, uri, options))
        return pa.schema([("x", pa.int64())])

    def read(self, uri: str, **options: Any) -> Any:
        self.seen.append((self.kind, uri, options))
        return iter([pa.record_batch({"x": [1]})])


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(delta, "DeltaSource", lambda: _Recorder("delta", calls))
    monkeypatch.setattr(azure, "AbfssSource", lambda: _Recorder("abfss", calls))
    return calls


def test_a_table_is_read_through_the_delta_source(seen: list[Any]) -> None:
    src = LakehouseSource()
    assert src.can_open("onelake://ws/lh/Tables/orders")
    assert src.schema("onelake://ws/lh/Tables/orders", a=1).names == ["x"]
    assert [b.num_rows for b in src.read("onelake://ws/lh/Tables/orders")] == [1]
    host = "onelake.dfs.fabric.microsoft.com"
    assert seen == [
        ("delta", f"delta+abfss://ws@{host}/lh.Lakehouse/Tables/orders", {"a": 1}),
        ("delta", f"delta+abfss://ws@{host}/lh.Lakehouse/Tables/orders", {}),
    ]


def test_files_are_read_through_the_abfss_source(seen: list[Any]) -> None:
    src = LakehouseSource()
    list(src.read("onelake://ws/lh/Files/raw/*.parquet"))
    assert seen[0][0] == "abfss" and seen[0][1].endswith("/lh.Lakehouse/Files/raw/*.parquet")
    assert not src.can_open("abfss://ws@onelake.dfs.fabric.microsoft.com/lh/Files/x")


def test_the_write_result_counts_and_summarises() -> None:
    result = WriteResult("dest", {"customer": 1200, "ordér": 3}, 2.25)
    assert (result.tables_written, result.rows_written) == (2, 1203)
    lines = result.summary().splitlines()
    assert lines[0] == "dest: 2 tables, 1,203 rows in 2.2s"
    assert lines[1].split() == ["customer", "1,200"] and lines[2].split() == ["ordér", "3"]
    assert WriteResult("empty").summary() == "empty: 0 tables, 0 rows in 0.0s"
    err = WriteError("boom", result)
    assert err.result is result and str(err) == "boom"


class _Answer:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self) -> _Answer:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return b"{}"


def test_the_urllib_transport_answers_status_headers_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kusto.urlrequest, "urlopen", lambda req, timeout: _Answer())
    assert kusto.urllib_transport("GET", "https://h/x", {}, b"", 1.0) == (
        200,
        {"Content-Type": "application/json"},
        b"{}",
    )


def test_the_urllib_transport_returns_an_http_error_as_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(req: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(
            "https://h/x", 429, "busy", {"Retry-After": "1"}, io.BytesIO(b"no")
        )  # type: ignore[arg-type]

    monkeypatch.setattr(kusto.urlrequest, "urlopen", refuse)
    status, headers, body = kusto.urllib_transport("GET", "https://h/x", {}, b"", 1.0)
    assert (status, headers["Retry-After"], body) == (429, "1", b"no")


@pytest.mark.parametrize("error", [urllib.error.URLError("dns"), TimeoutError("slow")])
def test_the_urllib_transport_turns_a_lost_connection_into_connection_error(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def lose(req: Any, timeout: float) -> Any:
        raise error

    monkeypatch.setattr(kusto.urlrequest, "urlopen", lose)
    with pytest.raises(ConnectionError, match="eventhouse"):
        kusto.urllib_transport("GET", "https://h/x", {}, b"", 1.0)
