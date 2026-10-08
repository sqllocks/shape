"""DEMO-LIVE F-1: Delta tables in a Fabric notebook go to OneLake by ``abfss://``, not the mount.

A Delta write through ``/lakehouse/default/Tables`` fails in Fabric ("Unable to rename file":
delta-rs commits by renaming, which the mount refuses). The notebooks write to the default
lakehouse's OneLake URI with ``storage_options={"bearer_token": <storage token>,
"use_fabric_endpoint": "true"}``, as the owner's working live workaround did. The Fabric surfaces
(``notebookutils`` and delta-rs's object store) are mocked; the live check is in
``docs/plans/demo_status/DEMO-LIVE.md``.
"""

from __future__ import annotations

import types
from pathlib import Path

import pyarrow as pa
import pytest

from shape.builtins.sources import delta as delta_source
from shape.integrations.fabric import generation, onelake

WS_ID = "11111111-2222-3333-4444-555555555555"
LH_ID = "66666666-7777-8888-9999-000000000000"
TABLES_URI = f"abfss://{WS_ID}@onelake.dfs.fabric.microsoft.com/{LH_ID}/Tables"


def _utils(context: dict | None = None, token: str = "tok-123") -> types.SimpleNamespace:
    """A ``notebookutils`` stand-in: the runtime context and ``credentials.getToken``."""
    audiences: list[str] = []

    def get_token(audience: str) -> str:
        audiences.append(audience)
        return token

    return types.SimpleNamespace(
        runtime=types.SimpleNamespace(context=dict(context or {})),
        credentials=types.SimpleNamespace(getToken=get_token),
        audiences=audiences,
    )


IDS = {"defaultLakehouseId": LH_ID, "defaultLakehouseWorkspaceId": WS_ID}


# ------------------------------------------------------------------------- the location


def test_the_default_lakehouse_uri_is_built_from_the_ids():
    assert onelake.default_tables_uri(_utils(IDS)) == TABLES_URI
    ctx = {"defaultLakehouseId": LH_ID, "currentWorkspaceId": WS_ID}
    assert onelake.default_tables_uri(_utils(ctx)) == TABLES_URI


def test_without_ids_the_names_are_used_and_quoted():
    ctx = {"defaultLakehouseName": "shape_demo", "currentWorkspaceName": "Shape Demo"}
    assert onelake.default_tables_uri(_utils(ctx)) == (
        "abfss://Shape%20Demo@onelake.dfs.fabric.microsoft.com/shape_demo.Lakehouse/Tables"
    )


def test_no_default_lakehouse_in_fabric_is_an_error_not_the_mount():
    assert onelake.default_tables_uri(_utils({})) is None
    with pytest.raises(RuntimeError, match="No default lakehouse"):
        onelake.tables_location("/lakehouse/default/Tables", _utils({}))
    # without notebookutils.credentials it is not Fabric: the local folder
    no_credentials = types.SimpleNamespace(runtime=types.SimpleNamespace(context={}))
    assert onelake.tables_location("/t", no_credentials) == ("/t", None)


def test_a_mapping_context_that_is_not_a_dict_is_read():
    class Context:
        def __init__(self, data):
            self._data = data

        def keys(self):
            return self._data.keys()

        def __getitem__(self, key):
            return self._data[key]

    utils = _utils()
    utils.runtime.context = Context(IDS)
    assert onelake.default_tables_uri(utils) == TABLES_URI


def test_the_storage_options_carry_the_storage_token_and_the_fabric_endpoint():
    utils = _utils(IDS)
    assert onelake.storage_options(utils) == {
        "bearer_token": "tok-123",
        "use_fabric_endpoint": "true",
    }
    assert utils.audiences == ["storage"]


def test_in_fabric_the_tables_go_to_onelake_with_the_token():
    uri, options = onelake.tables_location("/lakehouse/default/Tables", _utils(IDS))
    assert uri == TABLES_URI
    assert options == {"bearer_token": "tok-123", "use_fabric_endpoint": "true"}


def test_an_override_wins_and_must_be_an_abfss_uri():
    other = "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables/"
    uri, options = onelake.tables_location("/x", _utils(IDS), override=other)
    assert uri == other.rstrip("/") and options is not None
    with pytest.raises(ValueError, match="abfss"):
        onelake.tables_location("/x", _utils(IDS), override="/lakehouse/default/Tables")


def test_outside_fabric_the_local_folder_is_kept(monkeypatch):
    monkeypatch.setattr(onelake, "_notebookutils", lambda utils: utils)
    assert onelake.tables_location("/tmp/tables") == ("/tmp/tables", None)
    with pytest.raises(RuntimeError, match="not a Fabric notebook"):
        onelake.storage_options()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("abfss://a@b/c", True),
        ("abfs://a@b/c", True),
        ("delta+abfss://a@b/c", True),
        ("/lakehouse/default/Tables", False),
        ("C:\\tables", False),
    ],
)
def test_is_table_uri(text, expected):
    assert onelake.is_table_uri(text) is expected


# ------------------------------------------------------------- write_delta_tables plumbing


class _Recorder:
    """Stands in for ``deltalake.write_deltalake`` and records each call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, location, data, **kwargs):
        table = data.read_all() if hasattr(data, "read_all") else data
        self.calls.append({"location": location, "table": table, **kwargs})


@pytest.fixture()
def recorder(monkeypatch):
    import deltalake

    rec = _Recorder()
    monkeypatch.setattr(deltalake, "write_deltalake", rec)
    return rec


@pytest.fixture(scope="module")
def small_result():
    return generation.generate_domain("retail", scale="small", seed=42)


def _no_credential_lookup(monkeypatch):
    def refuse(_options):
        raise AssertionError("a token was given: no other credential may be looked up")

    monkeypatch.setattr(delta_source.auth, "resolve", refuse)


@pytest.mark.parametrize("scheme", ["abfss://", "delta+abfss://"])
def test_a_uri_is_never_collapsed_and_the_storage_options_reach_delta_rs(
    scheme, recorder, small_result, monkeypatch
):
    _no_credential_lookup(monkeypatch)
    base = TABLES_URI.replace("abfss://", scheme)
    options = {"bearer_token": "tok-123", "use_fabric_endpoint": "true"}
    written = generation.write_delta_tables(
        small_result, base, prefix="t_", storage_options=options
    )
    assert [w["deltaTable"] for w in written] == [
        f"t_{name}" for name in small_result.generation_order
    ]
    assert [c["location"] for c in recorder.calls] == [
        f"{TABLES_URI}/t_{name}" for name in small_result.generation_order
    ]
    for call in recorder.calls:
        # exactly the options the owner's live workaround used: nothing added, nothing resolved
        assert call["storage_options"] == options
        assert call["mode"] == "overwrite" and call["schema_mode"] == "overwrite"
        for field in call["table"].schema:  # delta_ready + the sink's UTC cast
            if pa.types.is_timestamp(field.type):
                assert field.type.unit == "us" and field.type.tz == "UTC"


def test_append_keeps_the_table_schema(recorder, small_result, monkeypatch):
    _no_credential_lookup(monkeypatch)
    generation.write_delta_tables(
        small_result, TABLES_URI, mode="append", storage_options={"bearer_token": "t"}
    )
    assert {c["mode"] for c in recorder.calls} == {"append"}
    assert all("schema_mode" not in c for c in recorder.calls)


def test_a_path_collapses_a_uri_which_is_why_the_location_stays_text():
    # the bug: Path("delta+abfss://ws@host/x") is "delta+abfss:/ws@host/x"
    assert str(Path("delta+abfss://ws@host/x")) != "delta+abfss://ws@host/x"


def test_local_folders_still_work_given_as_text_or_path(small_result, tmp_path):
    from deltalake import DeltaTable

    generation.write_delta_tables(small_result, tmp_path / "a")
    generation.write_delta_tables(small_result, str(tmp_path / "b"))
    for root in ("a", "b"):
        assert DeltaTable(str(tmp_path / root / "customer")).to_pyarrow_table().num_rows == 1000


# ------------------------------------------------------------------ the source's options


def test_a_token_in_storage_options_is_the_credential(monkeypatch):
    _no_credential_lookup(monkeypatch)
    got = delta_source._storage_options(
        TABLES_URI, {"storage_options": {"bearer_token": "tok", "use_fabric_endpoint": "true"}}
    )
    assert got == {"bearer_token": "tok", "use_fabric_endpoint": "true"}


def test_without_a_token_the_credential_is_resolved_as_before(monkeypatch):
    from shape.builtins.sources import _azure_auth as auth

    monkeypatch.setattr(
        delta_source.auth,
        "resolve",
        lambda options: auth.Resolved("explicit-token", credential=auth.StaticTokenCredential("x")),
    )
    got = delta_source._storage_options(TABLES_URI, {})
    assert got == {"azure_storage_token": "x", "use_fabric_endpoint": "true"}
