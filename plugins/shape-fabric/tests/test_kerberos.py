"""W2-10 item 1: ``--auth kerberos`` (keytab sign-in) for ``mssql://`` targets, with a fake ``kinit``
on ``PATH`` and the in-repo fake SQL Server.

What every test ends up checking: the credential cache is private and gone when the command ends
(also after an error), ``KRB5CCNAME`` is set only while a connection is opened, and the keytab bytes
and the decoded Key Vault secret appear in no output, message or log record.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest
from shape_fabric import _tsql, kerberos
from shape_fabric.auth import AuthSettings, writer_options
from shape_fabric.errors import AuthError
from shape_fabric.testing import FakeSqlServer

from shape.cli.main import main
from shape.security import credrefs

pytestmark = pytest.mark.contract

KEYTAB_BYTES = b"\x05\x02KEYTAB-SECRET-BYTES\x00\xff"
PRINCIPAL = "svc_shape@CORP.EXAMPLE"
URI = "mssql://sql01.corp.example/appdb"

KINIT = textwrap.dedent(
    """\
    #!{python}
    import json, os, sys
    args = sys.argv[1:]
    log = os.environ["FAKE_KINIT_LOG"]
    keytab = args[args.index("-t") + 1] if "-t" in args else None
    entry = {{
        "args": args,
        "krb5ccname": os.environ.get("KRB5CCNAME"),
        "keytab_exists": bool(keytab and os.path.exists(keytab)),
        "keytab_mode": oct(os.stat(keytab).st_mode & 0o777) if keytab and os.path.exists(keytab) else None,
        "keytab_bytes": open(keytab, "rb").read().hex() if keytab and os.path.exists(keytab) else None,
    }}
    with open(log, "a") as fh:
        fh.write(json.dumps(entry) + "\\n")
    if os.environ.get("FAKE_KINIT_FAIL"):
        sys.stderr.write(os.environ["FAKE_KINIT_FAIL"] + "\\n")
        sys.exit(1)
    cache = args[args.index("-c") + 1]
    with open(cache, "w") as fh:
        fh.write("TICKET")
    """
)


@pytest.fixture
def kinit(tmp_path, monkeypatch):
    """A fake ``kinit`` first on PATH; ``calls()`` reads what it was run with."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "kinit"
    script.write_text(KINIT.format(python=sys.executable))
    script.chmod(0o755)
    log = tmp_path / "kinit.log"
    monkeypatch.setenv("FAKE_KINIT_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("KRB5CCNAME", raising=False)

    class K:
        @staticmethod
        def calls():
            if not log.exists():
                return []
            return [json.loads(line) for line in log.read_text().splitlines()]

    return K


@pytest.fixture
def keytab_file(tmp_path):
    path = tmp_path / "svc.keytab"
    path.write_bytes(KEYTAB_BYTES)
    path.chmod(0o600)
    return path


def _cache_of(call):
    args = call["args"]
    return Path(args[args.index("-c") + 1])


# --- the session -------------------------------------------------------------------------


def test_kinit_runs_with_the_keytab_and_a_private_cache(kinit, keytab_file):
    session = kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    try:
        (call,) = kinit.calls()
        assert call["args"][:3] == ["-k", "-t", str(keytab_file)]
        assert call["args"][-1] == PRINCIPAL
        cache = _cache_of(call)
        assert cache == session.cache_path and cache.exists()
        assert stat.S_IMODE(cache.parent.stat().st_mode) == 0o700  # no one else can read it
        assert cache.parent != keytab_file.parent
        assert call["krb5ccname"] == str(cache)  # the child's environment, not ours
        assert "KRB5CCNAME" not in os.environ
    finally:
        session.close()
    assert not cache.exists() and not cache.parent.exists()


def test_close_is_idempotent_and_release_all_removes_every_cache(kinit, keytab_file):
    a = kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    b = kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    caches = [a.cache_path, b.cache_path]
    a.close()
    a.close()
    assert not caches[0].exists() and caches[1].exists()
    kerberos.release_all()
    assert not caches[1].exists()
    kerberos.release_all()  # nothing left: not an error


def test_krb5ccname_is_set_only_while_connecting(kinit, keytab_file, monkeypatch):
    monkeypatch.setenv("KRB5CCNAME", "FILE:/somebody/elses/cache")
    session = kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    seen = []

    def real(connection_string, credential=None, **kw):
        seen.append(os.environ.get("KRB5CCNAME"))
        return "connection"

    try:
        assert session.wrap(real)("Server=x", None) == "connection"
        assert seen == [str(session.cache_path)]
        assert os.environ["KRB5CCNAME"] == "FILE:/somebody/elses/cache"  # put back

        def failing(*_a, **_kw):
            raise RuntimeError("login failed")

        with pytest.raises(RuntimeError):
            session.wrap(failing)("Server=x", None)
        assert os.environ["KRB5CCNAME"] == "FILE:/somebody/elses/cache"
    finally:
        session.close()
    monkeypatch.delenv("KRB5CCNAME")
    session = kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    try:
        session.wrap(real)("Server=x", None)
        assert "KRB5CCNAME" not in os.environ
    finally:
        session.close()


def test_a_key_vault_keytab_is_decoded_used_and_removed_at_once(kinit):
    credrefs.register_resolver("kv", lambda rest: base64.b64encode(KEYTAB_BYTES).decode())
    try:
        session = kerberos.KerberosSession("kv://kv1/svc-keytab", PRINCIPAL)
    finally:
        credrefs.unregister_resolver("kv")
    try:
        (call,) = kinit.calls()
        assert bytes.fromhex(call["keytab_bytes"]) == KEYTAB_BYTES
        assert call["keytab_mode"] == "0o600"
        keytab = Path(call["args"][call["args"].index("-t") + 1])
        assert not keytab.exists()  # the temporary keytab is gone as soon as kinit returned
    finally:
        session.close()


def test_a_key_vault_secret_that_is_not_base64_is_refused_without_echoing_it(kinit):
    credrefs.register_resolver("kv", lambda rest: "not base64 !!! TOP-SECRET")
    try:
        with pytest.raises(AuthError) as err:
            kerberos.KerberosSession("kv://kv1/svc-keytab", PRINCIPAL)
    finally:
        credrefs.unregister_resolver("kv")
    assert "base64" in str(err.value) and "TOP-SECRET" not in str(err.value)
    assert kinit.calls() == []


def test_a_keytab_that_others_can_read_is_refused(kinit, keytab_file):
    keytab_file.chmod(0o640)
    with pytest.raises(AuthError, match="chmod 600"):
        kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    assert kinit.calls() == []


def test_a_missing_keytab_file_and_an_unsupported_reference_are_refused(kinit, tmp_path):
    with pytest.raises(AuthError, match="keytab"):
        kerberos.KerberosSession(f"file://{tmp_path / 'absent.keytab'}", PRINCIPAL)
    with pytest.raises(AuthError, match=r"file://PATH or kv://VAULT/NAME"):
        kerberos.KerberosSession("env://KEYTAB", PRINCIPAL)
    assert kinit.calls() == []


def test_no_kinit_on_path_is_the_documented_error(tmp_path, monkeypatch, keytab_file):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(AuthError) as err:
        kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    assert str(err.value) == "--auth kerberos needs the MIT Kerberos client (kinit) on PATH"


def test_a_failed_kinit_reports_its_message_the_reference_and_the_principal(
    kinit, keytab_file, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("FAKE_KINIT_FAIL", "kinit: Client not found in Kerberos database")
    with pytest.raises(AuthError) as err:
        kerberos.KerberosSession(f"file://{keytab_file}", PRINCIPAL)
    text = str(err.value)
    assert "Client not found in Kerberos database" in text
    assert PRINCIPAL in text and f"file://{keytab_file}" in text
    assert KEYTAB_BYTES.decode("latin-1") not in text
    assert KEYTAB_BYTES.decode("latin-1") not in caplog.text
    (call,) = kinit.calls()
    assert not _cache_of(call).parent.exists()  # nothing is left behind by a failure


def test_the_failure_message_never_carries_the_keytab_even_if_kinit_echoes_it(kinit, monkeypatch):
    secret = base64.b64encode(KEYTAB_BYTES).decode()
    credrefs.register_resolver("kv", lambda rest: secret)
    monkeypatch.setenv("FAKE_KINIT_FAIL", f"kinit: bad keytab {secret}")
    try:
        with pytest.raises(AuthError) as err:
            kerberos.KerberosSession("kv://kv1/svc-keytab", PRINCIPAL)
    finally:
        credrefs.unregister_resolver("kv")
    assert secret not in str(err.value) and "kv://kv1/svc-keytab" in str(err.value)


# --- settings and writer options ---------------------------------------------------------


def test_settings_accept_the_kerberos_mode_and_its_companions_and_hide_nothing_secret():
    s = AuthSettings.from_mapping(
        {"mode": "kerberos", "keytab": "file:///k", "principal": PRINCIPAL}
    )
    assert (s.mode, s.keytab, s.principal) == ("kerberos", "file:///k", PRINCIPAL)
    assert "file:///k" not in repr(s)  # a reference is still hidden: nothing is shown but the mode


def test_writer_options_give_a_trusted_connection_and_the_session(kinit, keytab_file):
    options = writer_options(
        {"mode": "kerberos", "keytab": f"file://{keytab_file}", "principal": PRINCIPAL}
    )
    try:
        assert set(options) == {"kerberos"}
        assert isinstance(options["kerberos"], kerberos.KerberosSession)
    finally:
        kerberos.release_all()


def test_kerberos_needs_a_keytab_and_a_principal_off_windows(kinit):
    with pytest.raises(AuthError, match="--keytab"):
        writer_options({"mode": "kerberos", "principal": PRINCIPAL})
    with pytest.raises(AuthError, match="--principal"):
        writer_options({"mode": "kerberos", "keytab": "file:///k"})
    assert kinit.calls() == []


def test_keytab_on_windows_is_refused_and_the_signed_in_account_is_used(kinit, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(AuthError) as err:
        writer_options({"mode": "kerberos", "keytab": "file:///k", "principal": PRINCIPAL})
    assert str(err.value) == (
        "on Windows, --auth kerberos uses the signed-in account; leave out --keytab"
    )
    assert writer_options({"mode": "kerberos"}) == {"trusted_connection": True}
    assert kinit.calls() == []  # Windows never runs kinit


def test_settings_of_other_modes_do_not_take_a_keytab():
    with pytest.raises(AuthError, match="belong to --auth kerberos"):
        writer_options({"mode": "cli", "keytab": "file:///k"})


# --- through the command line ------------------------------------------------------------


DOC = {
    "schema_version": 1,
    "model": {"name": "t", "seed": 5},
    "tables": {
        "customer": {
            "name": "customer",
            "primary_key": ["customer_id"],
            "columns": {
                "customer_id": {
                    "name": "customer_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence"},
                    "nullable": False,
                    "null_rate": 0.0,
                }
            },
        }
    },
    "relationships": [],
    "generation": {"scale": "small", "scales": {"small": {"customer": 25}}},
}


@pytest.fixture
def world(tmp_path, monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(DOC))
    server = FakeSqlServer()
    seen = []

    def connect(connection_string, credential=None, **kw):
        seen.append(
            {
                "cs": connection_string,
                "credential": credential,
                "krb5ccname": os.environ.get("KRB5CCNAME"),
                "cache_exists": os.path.exists(os.environ.get("KRB5CCNAME", "")),
            }
        )
        return server.connect(connection_string, credential, **kw)

    monkeypatch.setattr(_tsql, "connect", connect)

    class W:
        pass

    w = W()
    w.schema, w.server, w.seen, w.caplog = str(schema), server, seen, caplog

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out, out.err

    w.run = run
    return w


def _flags(keytab_file, *extra):
    return (
        "--auth", "kerberos", "--keytab", f"file://{keytab_file}", "--principal", PRINCIPAL,
        *extra,
    )  # fmt: skip


def _no_secret(world, out, err):
    seen = out + err + world.caplog.text
    for secret in (KEYTAB_BYTES.decode("latin-1"), base64.b64encode(KEYTAB_BYTES).decode()):
        assert secret not in seen


def test_generate_to_mssql_signs_in_with_the_keytab_and_cleans_up(world, kinit, keytab_file):
    code, out, err = world.run("generate", world.schema, "--to", URI, *_flags(keytab_file))
    assert code == 0, err
    assert len(world.server.rows("dbo", "customer")) == 25
    first = world.seen[0]
    assert "Trusted_Connection=yes" in first["cs"]
    assert "UID=" not in first["cs"] and "PWD=" not in first["cs"]
    assert first["credential"] is None  # no token: Kerberos signs in through the driver
    (call,) = kinit.calls()
    cache = _cache_of(call)
    assert first["krb5ccname"] == str(cache) and first["cache_exists"]
    assert not cache.exists() and not cache.parent.exists()  # removed when the command ended
    assert "KRB5CCNAME" not in os.environ
    _no_secret(world, out, err)


def test_the_cache_is_removed_when_the_command_fails(world, kinit, keytab_file, monkeypatch):
    def boom(*_a, **_kw):
        raise RuntimeError("login failed for 'svc_shape'")

    monkeypatch.setattr(_tsql, "connect", boom)
    code, out, err = world.run("generate", world.schema, "--to", URI, *_flags(keytab_file))
    assert code != 0
    (call,) = kinit.calls()
    assert not _cache_of(call).parent.exists()
    assert "KRB5CCNAME" not in os.environ
    _no_secret(world, out, err)


def test_emit_to_mssql_signs_in_with_the_keytab(world, kinit, keytab_file):
    code, out, err = world.run(
        "emit", world.schema, "--to", URI, *_flags(keytab_file), "--max-events", "10"
    )
    assert code == 0, err
    assert world.seen and "Trusted_Connection=yes" in world.seen[0]["cs"]
    (call,) = kinit.calls()
    assert not _cache_of(call).parent.exists()
    _no_secret(world, out, err)


def test_stream_to_mssql_signs_in_with_the_keytab(world, kinit, keytab_file):
    code, out, err = world.run(
        "stream", world.schema, "-t", "customer", "--to", URI, *_flags(keytab_file),
        "--max-events", "10",
    )  # fmt: skip
    assert code == 0, err
    assert world.seen and "Trusted_Connection=yes" in world.seen[0]["cs"]
    (call,) = kinit.calls()
    assert not _cache_of(call).parent.exists()


def test_no_kinit_is_exit_2_with_the_documented_message(world, keytab_file, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    code, out, err = world.run("generate", world.schema, "--to", URI, *_flags(keytab_file))
    assert code == 2
    assert "--auth kerberos needs the MIT Kerberos client (kinit) on PATH" in err
    assert world.seen == []


def test_a_failed_kinit_is_exit_2_and_names_the_reference_and_principal(
    world, kinit, keytab_file, monkeypatch
):
    monkeypatch.setenv("FAKE_KINIT_FAIL", "kinit: Preauthentication failed")
    code, out, err = world.run("generate", world.schema, "--to", URI, *_flags(keytab_file))
    assert code == 2
    assert "Preauthentication failed" in err and PRINCIPAL in err and f"file://{keytab_file}" in err
    assert world.seen == []
    (call,) = kinit.calls()
    assert not _cache_of(call).parent.exists()
    _no_secret(world, out, err)


def test_a_literal_keytab_path_is_refused_as_not_a_reference(world, kinit, keytab_file):
    code, _, err = world.run(
        "generate", world.schema, "--to", URI,
        "--auth", "kerberos", "--keytab", str(keytab_file), "--principal", PRINCIPAL,
    )  # fmt: skip
    assert code == 2 and "credential reference" in err
    assert kinit.calls() == []


def test_keytab_on_windows_is_exit_2(world, kinit, keytab_file, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    code, _, err = world.run("generate", world.schema, "--to", URI, *_flags(keytab_file))
    assert code == 2
    assert "on Windows, --auth kerberos uses the signed-in account; leave out --keytab" in err


def test_kerberos_is_for_mssql_targets_only(world, kinit, keytab_file, tmp_path):
    code, _, err = world.run(
        "generate", world.schema, "--to", "warehouse://wh.example.test/db", *_flags(keytab_file)
    )
    assert code == 2 and "mssql://" in err
    assert kinit.calls() == []


def test_kerberos_with_a_scale_mode_job_is_refused_not_half_supported(
    world, kinit, keytab_file, tmp_path
):
    code, _, err = world.run(
        "generate", world.schema, "--scale-mode", "local_single", "--sink", "sql_database",
        *_flags(keytab_file),
    )  # fmt: skip
    assert code == 2 and "kerberos" in err
    assert kinit.calls() == []


def test_the_sink_adds_trusted_connection_to_a_given_connection_string(
    world, kinit, keytab_file, monkeypatch
):
    monkeypatch.setenv(
        "SHAPE_TEST_CS", "Driver={ODBC Driver 18 for SQL Server};Server=sql01;Database=d"
    )
    code, _, err = world.run(
        "generate", world.schema, "--to", URI, "--connection-string", "env://SHAPE_TEST_CS",
        *_flags(keytab_file),
    )  # fmt: skip
    assert code == 0, err
    assert world.seen[0]["cs"].count("Trusted_Connection=yes") == 1


def test_a_connection_string_that_holds_a_login_is_refused_with_kerberos(
    world, kinit, keytab_file, monkeypatch
):
    monkeypatch.setenv("SHAPE_TEST_CS", "Server=sql01;Database=d;UID=sa")
    code, _, err = world.run(
        "generate", world.schema, "--to", URI, "--connection-string", "env://SHAPE_TEST_CS",
        *_flags(keytab_file),
    )  # fmt: skip
    assert code == 2 and "login" in err
