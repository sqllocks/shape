"""Public Iceberg catalog endpoint options do not collide with positional table URIs."""

from types import SimpleNamespace

import pyarrow as pa
import pytest

from shape.builtins import _iceberg
from shape.builtins.sinks import iceberg as sink_module
from shape.builtins.sources.iceberg import IcebergSource

pytestmark = [pytest.mark.zero_network, pytest.mark.conformance]
URI = "iceberg://configured/ns/items"
ENDPOINT = "https://catalog.example"


class CatalogReached(BaseException):
    """Stop after capturing SDK configuration, before any table or storage I/O."""


@pytest.fixture
def captured_catalog(monkeypatch):
    calls = []

    def load_catalog(name, **options):
        calls.append((name, options))
        raise CatalogReached

    def dependency():
        return SimpleNamespace(load_catalog=load_catalog)

    monkeypatch.setattr(_iceberg, "dependency", dependency)
    monkeypatch.setattr(sink_module, "dependency", dependency)
    monkeypatch.setattr(sink_module, "partition_spec", lambda *args: None)
    return calls


@pytest.mark.parametrize("kind", ["rest", "sql", "glue", "hive"])
@pytest.mark.parametrize("entry", ["write", "open_table", "schema", "read", "profile_source"])
def test_public_catalog_uri_reaches_sdk_without_argument_collision(captured_catalog, kind, entry):
    options = {"catalog_type": kind, "catalog_uri": ENDPOINT}
    batch = pa.record_batch({"id": [1]})
    with pytest.raises(CatalogReached):
        if entry == "write":
            sink_module.IcebergSink().write(URI, "items", [batch], **options)
        elif entry == "open_table":
            sink_module.IcebergSink().open_table(URI, "items", batch.schema, **options)
        elif entry == "read":
            list(IcebergSource().read(URI, **options))
        else:
            getattr(IcebergSource(), entry)(URI, **options)
    assert captured_catalog == [("configured", {"type": kind, "uri": ENDPOINT})]


@pytest.mark.parametrize("kind", ["rest", "sql", "glue", "hive"])
@pytest.mark.parametrize("key", ["uri", "catalog_uri"])
def test_catalog_helper_keeps_legacy_endpoint_mapping(captured_catalog, kind, key):
    with pytest.raises(CatalogReached):
        _iceberg.catalog(URI, {"catalog_type": kind, key: ENDPOINT})
    assert captured_catalog == [("configured", {"type": kind, "uri": ENDPOINT})]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://user:fixture-secret@catalog.example",
        "https://user@catalog.example",
        "https://catalog.example?token=fixture-secret",
        "https://catalog.example?%63redential=fixture-secret",
        "https://catalog.example#fixture-secret",
    ],
)
def test_catalog_endpoint_alias_uses_existing_fail_closed_validation(captured_catalog, endpoint):
    with pytest.raises(ValueError) as error:
        _iceberg.catalog(URI, {"catalog_type": "rest", "catalog_uri": endpoint})
    assert "fixture-secret" not in str(error.value)
    assert not captured_catalog


@pytest.mark.parametrize("first,second", [(ENDPOINT, ENDPOINT), (None, ENDPOINT), (ENDPOINT, None)])
def test_explicit_endpoint_alias_and_legacy_option_are_ambiguous(captured_catalog, first, second):
    with pytest.raises(ValueError, match="catalog_uri.*uri|uri.*catalog_uri"):
        _iceberg.catalog(URI, {"catalog_uri": first, "uri": second})
    assert not captured_catalog
