"""Where passwords come from, and the guarantee that a secret never leaks."""

import logging
import os
import stat

import pytest
from shape_databases import CredentialError, MySqlSink, PostgresSink, Secret, WriteError
from shape_databases._auth import resolve_password, scrub
from shape_databases.testing import FakeDriverError, FakeServer, sample_batch

from shape.errors import ShapeError

SECRET = "S3cr3t!pw/with:odd@chars"
PG = "postgresql://shape@h/db"
MY = "mysql://shape@h/db"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("SHAPE_POSTGRES_PASSWORD", "PGPASSWORD", "SHAPE_MYSQL_PASSWORD", "MYSQL_PWD"):
        monkeypatch.delenv(name, raising=False)


def _password_sent(sink_type, uri, dialect, **options):
    server = FakeServer(dialect)
    sink_type(connect=server.connect).write(uri, "t", iter([sample_batch()]), **options)
    return server.events[0][1].get("password")


def test_password_option_env_and_precedence(monkeypatch):
    assert _password_sent(PostgresSink, PG, "postgres") is None
    monkeypatch.setenv("PGPASSWORD", "from-pgpassword")
    assert _password_sent(PostgresSink, PG, "postgres") == "from-pgpassword"
    monkeypatch.setenv("SHAPE_POSTGRES_PASSWORD", "from-shape")
    assert _password_sent(PostgresSink, PG, "postgres") == "from-shape"
    assert _password_sent(PostgresSink, PG, "postgres", password="explicit") == "explicit"
    monkeypatch.setenv("MYSQL_PWD", "from-mysql-pwd")
    assert _password_sent(MySqlSink, MY, "mysql") == "from-mysql-pwd"
    monkeypatch.setenv("SHAPE_MYSQL_PASSWORD", "from-shape-mysql")
    assert _password_sent(MySqlSink, MY, "mysql") == "from-shape-mysql"


def test_env_and_file_references(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_DB_PW", SECRET)
    assert _password_sent(PostgresSink, PG, "postgres", password="env://MY_DB_PW") == SECRET
    path = tmp_path / "pw"
    path.write_text(SECRET + "\n", encoding="utf-8")
    path.chmod(0o600)
    assert _password_sent(MySqlSink, MY, "mysql", password=f"file://{path}") == SECRET
    assert _password_sent(MySqlSink, MY, "mysql", credential=f"file://{path}") == SECRET


def test_reference_errors_name_the_reference_never_a_value(tmp_path):
    with pytest.raises(CredentialError, match="NOPE_NOT_SET"):
        resolve_password({"password": "env://NOPE_NOT_SET"}, ())
    with pytest.raises(CredentialError, match="env:// needs"):
        resolve_password({"password": "env://"}, ())
    with pytest.raises(CredentialError, match="cannot read"):
        resolve_password({"password": f"file://{tmp_path}/missing"}, ())
    empty = tmp_path / "empty"
    empty.write_text("")
    empty.chmod(0o600)
    with pytest.raises(CredentialError, match="empty"):
        resolve_password({"password": f"file://{empty}"}, ())


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_a_secret_file_other_users_can_read_is_refused(tmp_path):
    path = tmp_path / "pw"
    path.write_text(SECRET)
    path.chmod(0o644)
    with pytest.raises(CredentialError, match="chmod 600") as info:
        resolve_password({"password": f"file://{path}"}, ())
    assert SECRET not in str(info.value)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_a_credential_object_or_function_gives_the_password_as_a_token():
    class Cred:
        def __init__(self):
            self.scopes = []

        def get_token(self, scope):
            self.scopes.append(scope)

            class T:
                token = SECRET

            return T()

    cred = Cred()
    assert _password_sent(PostgresSink, PG, "postgres", credential=cred) == SECRET
    assert cred.scopes == ["https://ossrh-postgresql.azure.com/.default"]
    assert _password_sent(MySqlSink, MY, "mysql", credential=lambda scope: SECRET) == SECRET
    got = []
    cred2 = Cred()
    cred2.get_token = lambda scope: got.append(scope) or type("T", (), {"token": "t"})()
    _password_sent(
        PostgresSink, PG, "postgres", credential=cred2, token_scope="api://custom/.default"
    )
    assert got == ["api://custom/.default"]


def test_a_failing_credential_does_not_echo_its_error():
    def boom(scope):
        raise RuntimeError(f"vault said: {SECRET}")

    with pytest.raises(CredentialError) as info:
        resolve_password({"credential": boom}, ())
    assert SECRET not in str(info.value) and info.value.__cause__ is None


def test_bad_credential_options():
    with pytest.raises(CredentialError, match="not both"):
        resolve_password({"password": "a", "credential": "env://X"}, ())
    with pytest.raises(CredentialError):
        resolve_password({"password": ""}, ())
    with pytest.raises(CredentialError):
        resolve_password({"credential": "plain-text"}, ())
    with pytest.raises(CredentialError):
        resolve_password({"credential": 42}, ())


def test_a_password_in_the_uri_is_refused_without_echoing_it(flavour):
    sink, uri, server = flavour
    scheme = uri.split(":")[0]
    with pytest.raises(ShapeError, match="must not be part of the URI") as info:
        sink.write(f"{scheme}://u:{SECRET.replace('/', '%2F')}@h/d", "t", iter([sample_batch()]))
    assert SECRET not in str(info.value) and "S3cr3t" not in str(info.value)
    assert server.events == []


def test_secret_has_no_revealing_repr():
    s = Secret(SECRET)
    assert SECRET not in repr(s) and SECRET not in str(s) and SECRET not in f"{s!r}{s}"


# -- the secret never appears in an exception message or a log record ------------------------
LEAKY = [
    "connection to server failed: password authentication failed for user shape (password={s})",
    "FATAL: could not connect: postgresql://shape:{s}@h/d refused",
    "Access denied for user 'shape'@'h' (using password: YES) pwd={s}",
    "raw echo of the secret: {s} and {s}",
]


def _every_message(exc):
    seen = []
    cur = exc
    while cur is not None and cur not in seen:
        seen.append(cur)
        cur = cur.__cause__ or cur.__context__
    return " | ".join(f"{type(e).__name__}: {e} {e.args!r}" for e in seen)


@pytest.mark.parametrize("template", LEAKY)
def test_no_exception_message_or_log_record_holds_the_secret(flavour, template, caplog, capsys):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_after_rows=1, fail_message=template.format(s=SECRET))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(WriteError) as failed:
        type(sink)(connect=server.connect).write(uri, "t", iter([sample_batch()]), password=SECRET)
    assert SECRET not in _every_message(failed.value)
    assert SECRET not in repr(failed.value)
    for record in caplog.records:
        assert SECRET not in record.getMessage()
        assert SECRET not in repr(record.__dict__)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


def test_a_connect_error_that_echoes_the_secret_is_scrubbed(flavour, caplog):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_connect=f"cannot connect with password={SECRET}")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(WriteError) as failed:
        type(sink)(connect=server.connect).write(uri, "t", iter([sample_batch()]), password=SECRET)
    assert SECRET not in _every_message(failed.value)
    assert all(SECRET not in r.getMessage() for r in caplog.records)
    assert "***" in str(failed.value)


def test_a_real_traceback_holds_no_secret(flavour):
    import traceback

    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_after_rows=1, fail_message=f"boom {SECRET}")
    try:
        type(sink)(connect=server.connect).write(uri, "t", iter([sample_batch()]), password=SECRET)
    except WriteError as exc:
        text = "".join(traceback.format_exception(exc))
    assert SECRET not in text


def test_the_secret_is_not_in_what_the_server_was_told_except_as_the_login(flavour):
    sink, uri, server = flavour
    sink.write(uri, "t", iter([sample_batch()]), password=SECRET)
    sent = [e for e in server.events if e[0] != "connect"]
    assert SECRET not in repr(sent)


def test_scrub_hides_known_secrets_uri_passwords_and_keyword_pairs():
    text = f"x PWD={SECRET} y postgresql://u:{SECRET}@h/d z password: abc token=tok123 {SECRET}"
    out = scrub(text, [SECRET, None, Secret(SECRET)])
    assert SECRET not in out and "abc" not in out and "tok123" not in out
    assert scrub("a\n b   c") == "a b c"
    assert len(scrub("x" * 1000)) == 400


def test_other_driver_errors_are_reported_with_their_type(flavour):
    sink, uri, _ = flavour
    server = FakeServer(sink.dialect, fail_after_rows=1, fail_message="disk full")
    with pytest.raises(WriteError, match=r"\(FakeDriverError\): disk full"):
        type(sink)(connect=server.connect).write(uri, "t", iter([sample_batch()]))
    assert issubclass(FakeDriverError, Exception)
