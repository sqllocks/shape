"""#294: a non-loopback PostgreSQL / MySQL destination is TLS-verified unless the URI opts out."""

import ssl

import pytest
from shape_databases import MySqlSink, PostgresSink
from shape_databases.errors import WriteError
from shape_databases.testing import FakeServer, sample_batch

from shape.errors import ShapeError

LOOPBACK = ["localhost", "127.0.0.1", "127.9.9.9", "[::1]"]
REMOTE = ["db.example", "10.0.0.5", "[2001:db8::1]"]


def _params(sink_cls, dialect, uri, **options):
    server = FakeServer(dialect)
    sink_cls(connect=server.connect).write(uri, "t", iter([sample_batch()]), **options)
    return server.events[0][1]


@pytest.mark.parametrize("host", REMOTE)
def test_postgres_remote_host_defaults_to_verify_full(host):
    assert _params(PostgresSink, "postgres", f"postgresql://u@{host}/d")["sslmode"] == "verify-full"


@pytest.mark.parametrize("host", LOOPBACK)
def test_postgres_loopback_keeps_the_driver_default(host):
    assert "sslmode" not in _params(PostgresSink, "postgres", f"postgresql://u@{host}/d")


def test_postgres_without_host_is_a_unix_socket_and_unchanged():
    assert "sslmode" not in _params(PostgresSink, "postgres", "postgresql:///d")


@pytest.mark.parametrize("mode", ["disable", "prefer", "require", "verify-ca"])
def test_postgres_explicit_sslmode_wins(mode):
    uri = f"postgresql://u@db.example/d?sslmode={mode}"
    assert _params(PostgresSink, "postgres", uri)["sslmode"] == mode


@pytest.mark.parametrize("host", REMOTE)
def test_mysql_remote_host_gets_a_verifying_context(host):
    ctx = _params(MySqlSink, "mysql", f"mysql://u@{host}/d")["ssl"]
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True and ctx.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("host", LOOPBACK)
def test_mysql_loopback_keeps_the_driver_default(host):
    params = _params(MySqlSink, "mysql", f"mysql://u@{host}/d")
    assert "ssl" not in params and not params.get("ssl_disabled")


@pytest.mark.parametrize("opt_out", ["ssl=false", "ssl-mode=DISABLED", "ssl-mode=disabled"])
def test_mysql_opt_out_in_the_uri_sends_no_tls(opt_out):
    params = _params(MySqlSink, "mysql", f"mysql://u@db.example/d?{opt_out}")
    assert "ssl" not in params and "ssl-mode" not in params
    assert params["ssl_disabled"] is True


def test_mysql_ssl_ca_is_the_trust_store_of_the_verified_context(monkeypatch):
    seen = {}
    real = ssl.create_default_context

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real()

    monkeypatch.setattr(ssl, "create_default_context", spy)
    params = _params(MySqlSink, "mysql", "mysql://u@db.example/d?ssl_ca=/etc/ca.pem")
    assert seen["cafile"] == "/etc/ca.pem"
    assert params["ssl"].check_hostname is True and "ssl_ca" not in params


def test_mysql_default_uses_the_system_ca_store(monkeypatch):
    seen = {}
    real = ssl.create_default_context

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real()

    monkeypatch.setattr(ssl, "create_default_context", spy)
    _params(MySqlSink, "mysql", "mysql://u@db.example/d")
    assert seen.get("cafile") is None


def test_mysql_unknown_ssl_mode_is_refused():
    with pytest.raises(ShapeError, match="must be one of DISABLED"):
        MySqlSink(connect=FakeServer("mysql").connect).write(
            "mysql://u@db.example/d?ssl-mode=maybe", "t", iter([sample_batch()])
        )


@pytest.mark.parametrize(
    ("sink_cls", "dialect", "scheme", "optout"),
    [
        (PostgresSink, "postgres", "postgresql", "sslmode=disable"),
        (MySqlSink, "mysql", "mysql", "ssl=false"),
    ],
)
def test_a_tls_failure_names_the_host_and_the_opt_out(sink_cls, dialect, scheme, optout):
    server = FakeServer(dialect, fail_connect="SSL: CERTIFICATE_VERIFY_FAILED certificate verify")
    with pytest.raises(WriteError) as info:
        sink_cls(connect=server.connect).write(
            f"{scheme}://u@db.example/d", "t", iter([sample_batch()])
        )
    assert "db.example" in str(info.value) and optout in str(info.value)


def test_a_non_tls_failure_gets_no_opt_out_hint():
    server = FakeServer("postgres", fail_connect="no route to host")
    with pytest.raises(WriteError) as info:
        PostgresSink(connect=server.connect).write(
            "postgresql://u@db.example/d", "t", iter([sample_batch()])
        )
    assert "sslmode" not in str(info.value)


def test_no_hint_when_the_uri_chose_the_mode_itself():
    server = FakeServer("postgres", fail_connect="SSL error: certificate verify failed")
    with pytest.raises(WriteError) as info:
        PostgresSink(connect=server.connect).write(
            "postgresql://u@db.example/d?sslmode=require", "t", iter([sample_batch()])
        )
    assert "opt out" not in str(info.value)
