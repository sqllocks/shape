"""Owner LIVE-FIX regressions, without a cloud service or a secret."""

import sys
from types import SimpleNamespace

import pyarrow as pa
import pytest

import shape
from shape.builtins.sinks.delta import _location
from shape.builtins.sources import _azure_auth as auth
from shape.builtins.sources.delta import DeltaSource


@pytest.mark.parametrize(
    "variable,key",
    [
        ("AZURE_STORAGE_ACCOUNT_KEY", "account_key"),
        ("AZURE_STORAGE_SAS_TOKEN", "sas_token"),
        ("AZURE_STORAGE_CONNECTION_STRING", "connection_string"),
    ],
)
def test_cloud_read_uses_the_storage_environment(monkeypatch, variable, key):
    for name in (
        "AZURE_STORAGE_ACCOUNT_KEY",
        "AZURE_STORAGE_SAS_TOKEN",
        "AZURE_STORAGE_CONNECTION_STRING",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "contract-value")
    monkeypatch.setattr(auth, "_import_notebookutils", lambda: None)
    monkeypatch.setattr(auth, "_default_credential", lambda: pytest.fail("Entra must not be tried"))
    assert dict(auth.resolve({}).adlfs_options) == {key: "contract-value"}
    credential = object()
    assert auth.resolve({"credential": credential}).credential is credential


def test_a_time_range_stays_text():
    profile = shape.profile({"buckets": pa.table({"HourlyBucket": ["18:00-18:59", "19:00-19:59"]})})
    assert profile.to_dict()["tables"]["buckets"]["columns"]["HourlyBucket"]["dtype"] == "string"


@pytest.mark.parametrize(
    "schema_name,folder", [(None, "Tables"), ("dbo", "Tables/dbo"), ("sales", "Tables/sales")]
)
def test_delta_prefix_and_explicit_lakehouse_schema(schema_name, folder):
    options = {"account_key": "contract-value", "table_prefix": "shape_"}
    options["schema_enabled"] = bool(schema_name)
    if schema_name:
        options["schema_name"] = schema_name
    base = "delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables"
    target, _ = _location(base, "orders", options)
    assert (
        target == f"abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/{folder}/shape_orders"
    )


def test_a_missing_delta_table_names_the_path(monkeypatch):
    from deltalake.exceptions import TableNotFoundError

    def missing(*args, **kwargs):
        raise TableNotFoundError("Generic delta kernel error: No files in log segment")

    monkeypatch.setitem(sys.modules, "deltalake", SimpleNamespace(DeltaTable=missing))
    uri = "delta+abfss://data@acct.dfs.core.windows.net/missing"
    with pytest.raises(FileNotFoundError, match="no Delta table at .*missing"):
        DeltaSource().schema(uri, account_key="contract-value")


def test_generate_cli_passes_the_prefix_and_lakehouse_schema(monkeypatch):
    from shape.builtins.sinks import delta
    from shape.cli.main import main

    locations = []
    monkeypatch.setenv("SHAPE_CONFIRM_REMOTE", "1")
    monkeypatch.setenv("LIVE_KEY", "contract-value")
    monkeypatch.setattr(
        delta,
        "_deltalake",
        lambda: SimpleNamespace(
            write_deltalake=lambda location, *args, **kwargs: locations.append(location)
        ),
    )
    assert (
        main(
            [
                "generate",
                "retail",
                "--scale",
                "small",
                "--seed",
                "42",
                "--to",
                "delta+abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Tables",
                "--table-prefix",
                "shape_",
                "--schema-name",
                "dbo",
                "--sink-config",
                "delta.schema_enabled=true",
                "--sink-config",
                "delta.account_key=env://LIVE_KEY",
            ]
        )
        == 0
    )
    assert locations
    assert all("/Tables/dbo/shape_" in path for path in locations)


@pytest.mark.parametrize("options", [["--format", "csv"], ["--scale-mode", "local_single"]])
def test_a_writer_that_cannot_honour_the_prefix_refuses_it(tmp_path, capsys, options, monkeypatch):
    from shape.cli.main import main

    monkeypatch.setenv("SHAPE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    assert (
        main(
            [
                "generate",
                "retail",
                "--scale",
                "small",
                "--table-prefix",
                "shape_",
                "-o",
                str(tmp_path / "output"),
                *options,
            ]
        )
        == 2
    )
    assert "--table-prefix" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()
