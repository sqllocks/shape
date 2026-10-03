"""P6-07b, core side: the ``--auth`` options as the command line reads them. The plugin builds the
credentials (``plugins/shape-fabric/tests``); here the plugin is a stub, so core is tested alone."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

from shape.cli import auth
from shape.cli.main import main

pytestmark = pytest.mark.contract


def parse(*argv: str) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    auth.add_arguments(p)
    return p.parse_args(list(argv))


# --- reading the options -----------------------------------------------------------------


def test_no_option_is_no_settings():
    assert auth.settings_from_args(parse()) is None


def test_every_mode_is_a_choice():
    for mode in ("cli", "msi", "spn", "sql", "device-code", "fabric"):
        assert auth.settings_from_args(parse("--auth", mode)) == {"mode": mode}


def test_spn_settings_keep_the_secret_as_a_reference():
    a = parse(
        "--auth", "spn", "--tenant-id", "t", "--client-id", "c",
        "--client-secret", "kv://vault-one/spn",
    )  # fmt: skip
    assert auth.settings_from_args(a) == {
        "mode": "spn",
        "tenant_id": "t",
        "client_id": "c",
        "client_secret": "kv://vault-one/spn",
    }


def test_the_mode_is_inferred_from_a_sql_login_or_a_client_secret():
    assert (
        auth.settings_from_args(parse("--sql-user", "u", "--sql-password", "env://P"))["mode"]
        == "sql"
    )
    assert (
        auth.settings_from_args(
            parse("--tenant-id", "t", "--client-id", "c", "--client-secret", "env://S")
        )["mode"]
        == "spn"
    )


@pytest.mark.parametrize("flag", ["--client-secret", "--sql-password"])
@pytest.mark.parametrize("literal", ["hunter2", "p@ss;word", "file-without-scheme.txt"])
def test_a_secret_must_be_a_reference_and_is_not_repeated_in_the_error(flag, literal):
    extra = ["--auth", "spn"] if flag == "--client-secret" else ["--auth", "sql"]
    with pytest.raises(ValueError) as err:
        auth.settings_from_args(parse(*extra, flag, literal))
    assert "credential reference" in str(err.value) and literal not in str(err.value)


def test_a_connection_string_with_a_password_is_refused_but_a_reference_is_not():
    cs = "Driver={ODBC Driver 18 for SQL Server};Server=s;Database=d"
    assert auth.connection_string_from_args(parse("--connection-string", cs)) == cs
    assert auth.connection_string_from_args(parse("--connection-string", "env://CS")) == "env://CS"
    for held in ("PWD=x", "Password=x", "AccountKey=x", "SharedAccessKey=x"):
        with pytest.raises(ValueError) as err:
            auth.connection_string_from_args(parse("--connection-string", f"{cs};{held}"))
        assert "env://NAME" in str(err.value) and "=x" not in str(err.value)


def test_options_of_the_wrong_mode_are_refused():
    with pytest.raises(ValueError, match="belong to --auth sql"):
        auth.settings_from_args(parse("--auth", "cli", "--sql-user", "u"))
    with pytest.raises(ValueError, match="belongs to --auth spn"):
        auth.settings_from_args(parse("--auth", "cli", "--client-secret", "env://S"))


def test_no_option_takes_a_password_value_by_name():
    """The help names no ``--password`` style option that takes a literal."""
    p = argparse.ArgumentParser()
    auth.add_arguments(p)
    options = {s for a in p._actions for s in a.option_strings}
    assert "--password" not in options and "--pwd" not in options and "--secret" not in options
    for action in p._actions:
        if action.dest in ("client_secret", "sql_password"):
            assert action.metavar == "REF"


# --- the plugin is loaded only when used --------------------------------------------------


def test_the_cli_module_imports_no_plugin_or_cloud_code_at_import_time():
    import ast

    tree = ast.parse(open(auth.__file__, encoding="utf-8").read())
    top_level = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level += [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            top_level.append(node.module or "")
    assert not [m for m in top_level if m.startswith(("shape_fabric", "azure"))]


def test_make_credential_without_the_plugin_says_how_to_get_it(monkeypatch):
    monkeypatch.setitem(sys.modules, "shape_fabric.auth", None)
    with pytest.raises(ValueError, match=r"shape-fabric plugin.*sqllocks-shape\[fabric\]"):
        auth.make_credential({"mode": "cli"})
    assert auth.make_credential(None) is None


# --- fabric_spark signs in with --auth ----------------------------------------------------

SCHEMA = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 5},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["id"],
            "columns": {
                "id": {
                    "name": "id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                    "nullable": False,
                    "null_rate": 0.0,
                }
            },
        }
    },
    "relationships": [],
    "generation": {"scale": "small", "scales": {"small": {"t": 10}}},
}


@pytest.fixture
def stub_plugin(monkeypatch):
    """A stand-in for ``shape_fabric.auth`` / ``_auth`` that records what core asks of it."""
    asked = {"settings": [], "tokens": []}
    mod = types.ModuleType("shape_fabric.auth")

    class AuthSettings:
        @staticmethod
        def from_mapping(data):
            asked["settings"].append(dict(data))
            return dict(data)

    mod.AuthSettings = AuthSettings
    mod.build_credential = lambda settings: None if settings["mode"] == "sql" else settings["mode"]
    base = types.ModuleType("shape_fabric._auth")
    base.SCOPE_STORAGE = "https://storage.azure.com/.default"
    base.token_for = lambda cred, scope: asked["tokens"].append((cred, scope)) or f"tok:{scope}"
    monkeypatch.setitem(sys.modules, "shape_fabric.auth", mod)
    monkeypatch.setitem(sys.modules, "shape_fabric._auth", base)
    return asked


def test_spark_tokens_come_from_the_chosen_sign_in(stub_plugin):
    from shape.scale.api import FABRIC_API_SCOPE, _auth_tokens

    api, storage = _auth_tokens({"mode": "spn", "tenant_id": "t"})
    assert (api, storage) == (
        f"tok:{FABRIC_API_SCOPE}",
        "tok:https://storage.azure.com/.default",
    )
    assert stub_plugin["tokens"][0] == ("spn", FABRIC_API_SCOPE)


def test_spark_with_a_sql_login_is_an_error(stub_plugin):
    from shape.scale.api import _auth_tokens

    with pytest.raises(ValueError, match="database login"):
        _auth_tokens({"mode": "sql"})


def test_the_request_stores_references_only_so_it_can_resume(tmp_path):
    from shape.cli.main import _build_parser
    from shape.cli.scale import build_request

    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(SCHEMA))
    a = _build_parser().parse_args(
        [
            "generate",
            str(schema),
            "--scale-mode",
            "local_single",
            "--sink",
            "sql_database",
            "--connection-string",
            "env://CS",
            "--auth",
            "sql",
            "--sql-user",
            "app",
            "--sql-password",
            "file:///run/secrets/pw",
        ]  # fmt: skip
    )
    request = build_request(a)
    assert request["auth"] == {
        "mode": "sql",
        "sql_user": "app",
        "sql_password": "file:///run/secrets/pw",
    }
    assert request["sink_config"]["sql_database"]["connection_string"] == "env://CS"


def test_connection_string_flag_needs_a_sql_sink(tmp_path, capsys):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(SCHEMA))
    code = main(
        [
            "generate",
            str(schema),
            "--scale-mode",
            "local_single",
            "--sink",
            "memory",
            "--connection-string",
            "Server=s;Database=d",
        ]  # fmt: skip
    )
    assert code == 2
    assert "warehouse and sql_database" in capsys.readouterr().err


def test_a_secret_in_sink_config_is_refused(tmp_path, capsys):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(SCHEMA))
    code = main(
        [
            "generate",
            str(schema),
            "--scale-mode",
            "local_single",
            "--sink",
            "kql",
            "--sink-config",
            "kql.token=eyJraG9wZQ.abc.def",
        ]  # fmt: skip
    )
    err = capsys.readouterr().err
    assert code == 2 and "credential reference" in err and "eyJraG9wZQ" not in err


def test_profile_auth_needs_a_cloud_source(tmp_path, capsys):
    f = tmp_path / "t.csv"
    f.write_text("a\n1\n")
    code = main(["profile", str(f), "-o", str(tmp_path / "o.shape"), "--auth", "cli"])
    assert code == 2 and "in the cloud" in capsys.readouterr().err


def test_profile_hands_the_credential_to_the_source(monkeypatch, tmp_path, stub_plugin):
    """``shape profile onelake://... --auth cli`` gives the source a ``credential`` option."""
    import pyarrow as pa

    from shape.plugins import host as host_module

    received = {}

    class Source:
        name = "fake"
        schemes = ("fake",)

        def can_open(self, uri):
            return uri.startswith("fake://")

        def schema(self, uri, **options):
            received["schema"] = options
            return pa.schema([("a", pa.int64())])

        def read(self, uri, **options):
            received["read"] = options
            yield pa.RecordBatch.from_pydict({"a": [1, 2, 3]})

    class Host:
        def records(self, group):
            return [types.SimpleNamespace(group=group, name="fake")]

        def try_get(self, group, name):
            return Source()

    monkeypatch.setattr(host_module, "default_host", lambda: Host())
    out = tmp_path / "o.shape"
    code = main(["profile", "fake://ws/lh/Tables/t", "-o", str(out), "--auth", "cli"])
    assert code == 0 and out.exists()
    assert received["schema"] == {"credential": "cli"} == received["read"]
    # without --auth the source gets no credential
    received.clear()
    assert main(["profile", "fake://ws/lh/Tables/t", "-o", str(out)]) == 0
    assert received["schema"] == {}


# --- fabric_spark end to end through the command line (stub sign-in, recorded Fabric) -----

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
from fakes import LH, WS, FakeFabric  # noqa: E402


@pytest.fixture
def spark(tmp_path, monkeypatch, stub_plugin):
    fake = FakeFabric()
    monkeypatch.setattr("shape.scale.http.urllib_transport", fake)
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    monkeypatch.delenv("SHAPE_FABRIC_STORAGE_TOKEN", raising=False)
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(SCHEMA))
    return types.SimpleNamespace(fake=fake, schema=str(schema), jobs=tmp_path / "jobs")


def _run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_spark_submit_uses_the_tokens_of_the_chosen_sign_in(capsys, spark, stub_plugin):
    code, out, err = _run(
        capsys, "generate", spark.schema, "--scale-mode", "fabric_spark",
        "--fabric-workspace", WS, "--fabric-lakehouse", LH,
        "--auth", "spn", "--tenant-id", "t", "--client-id", "c", "--client-secret", "env://S",
        "--json",
    )  # fmt: skip
    assert code == 0, err
    fabric_calls = [c for c in spark.fake.calls if "api.fabric" in c["url"]]
    assert fabric_calls and all(
        c["headers"]["Authorization"] == "Bearer tok:https://api.fabric.microsoft.com/.default"
        for c in fabric_calls
    )
    stored = "".join(p.read_text() for p in spark.jobs.iterdir())
    assert "tok:" not in stored  # no token in the job record
    assert (
        '"client_secret": "env://S"' in stored
    )  # the reference is, so a later command can sign in


def test_jobs_commands_sign_in_again_from_the_stored_references(capsys, spark, stub_plugin):
    code, out, _ = _run(
        capsys, "generate", spark.schema, "--scale-mode", "fabric_spark",
        "--fabric-workspace", WS, "--fabric-lakehouse", LH,
        "--auth", "msi", "--json",
    )  # fmt: skip
    job_id = json.loads(out)["job_id"]
    stub_plugin["tokens"].clear()
    spark.fake.job_status = "InProgress"
    code, out, err = _run(capsys, "jobs", "status", job_id, "--json")
    assert code == 0, err
    assert json.loads(out)["status"] == "running"
    assert stub_plugin["tokens"] and stub_plugin["tokens"][0][0] == "msi"  # signed in, no flag
    code, out, err = _run(capsys, "jobs", "cancel", job_id, "--json")
    assert code == 0 and json.loads(out)["cancelled"] is True


def test_a_local_job_never_signs_in_for_jobs_commands(capsys, tmp_path, monkeypatch, stub_plugin):
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "jobs"))
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps(SCHEMA))
    code, out, _ = _run(
        capsys, "generate", str(schema), "--scale-mode", "local_single", "--sink", "parquet",
        "-o", str(tmp_path / "out"), "--json",
    )  # fmt: skip
    job_id = json.loads(out)["job_id"]
    code, _, _ = _run(capsys, "jobs", "status", job_id, "--auth", "cli", "--json")
    assert code == 0 and stub_plugin["tokens"] == []


def test_the_environment_token_still_wins_and_signs_nothing(
    capsys, spark, monkeypatch, stub_plugin
):
    monkeypatch.setenv("SHAPE_FABRIC_TOKEN", "tok-env")
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor-env")
    code, _, err = _run(
        capsys, "generate", spark.schema, "--scale-mode", "fabric_spark",
        "--fabric-workspace", WS, "--fabric-lakehouse", LH, "--auth", "cli", "--json",
    )  # fmt: skip
    assert code == 0, err
    assert stub_plugin["tokens"] == []


# --- W2-10: --auth kerberos --------------------------------------------------------------


def test_kerberos_settings_keep_the_keytab_as_a_reference():
    a = parse(
        "--auth", "kerberos", "--keytab", "file:///etc/shape/svc.keytab",
        "--principal", "svc@CORP.EXAMPLE",
    )  # fmt: skip
    assert auth.settings_from_args(a) == {
        "mode": "kerberos",
        "keytab": "file:///etc/shape/svc.keytab",
        "principal": "svc@CORP.EXAMPLE",
    }
    assert "kerberos" in auth.AUTH_MODES


def test_the_kerberos_mode_is_inferred_from_a_keytab_or_a_principal():
    assert auth.settings_from_args(parse("--keytab", "kv://v/k"))["mode"] == "kerberos"
    assert auth.settings_from_args(parse("--principal", "a@B"))["mode"] == "kerberos"


def test_a_keytab_path_that_is_not_a_reference_is_refused():
    with pytest.raises(ValueError, match="credential reference"):
        auth.settings_from_args(parse("--auth", "kerberos", "--keytab", "/etc/shape/svc.keytab"))


def test_keytab_and_principal_belong_to_kerberos():
    with pytest.raises(ValueError, match="belong to --auth kerberos"):
        auth.settings_from_args(parse("--auth", "cli", "--keytab", "file:///k"))
    with pytest.raises(ValueError, match="belong to --auth kerberos"):
        auth.settings_from_args(parse("--auth", "sql", "--principal", "a@B"))
