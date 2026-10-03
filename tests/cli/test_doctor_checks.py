"""W1-16: ``shape doctor`` Fabric access and broker reachability checks.

Every check runs against fakes (no network); the live variants are marked ``live``.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from shape.cli import doctor
from shape.cli import doctor_checks as dc
from shape.cli.main import main

pytestmark = pytest.mark.contract

V4 = (socket.AF_INET, "192.0.2.10")
V6 = (socket.AF_INET6, "2001:db8::10")


class FakeNet:
    """A scripted network: ``dns`` maps host -> addresses (or an exception), ``tcp`` the
    addresses that accept a connection, ``tls`` the hosts whose handshake works."""

    def __init__(self, dns=None, tcp=None, tls=None, http=None):
        self.dns_map = dns or {}
        self.tcp_ok = set(tcp or ())
        self.tls_map = tls or {}
        self.http_map = http or {}
        self.calls: list[tuple] = []

    def resolve(self, host, port):
        self.calls.append(("dns", host))
        r = self.dns_map.get(host, OSError("no such host"))
        if isinstance(r, Exception):
            raise r
        return list(r)

    def connect(self, address, port, timeout):
        self.calls.append(("tcp", address[1], port))
        if address[1] not in self.tcp_ok:
            raise TimeoutError("timed out")

    def handshake(self, host, port, timeout):
        self.calls.append(("tls", host))
        r = self.tls_map.get(host, True)
        if r is not True:
            raise r
        return "TLSv1.3"

    def http_get(self, url, headers, timeout):
        self.calls.append(("http", url))
        r = self.http_map.get(url)
        if isinstance(r, Exception):
            raise r
        return r if r is not None else 404


class Cred:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.scopes: list[str] = []

    def get_token(self, *scopes):
        self.scopes.extend(scopes)
        if self.fail:
            raise self.fail

        class T:
            token = "sekret-token"

        return T()


def by_id(checks, cid):
    return next(c for c in checks if c.id == cid)


# --- the shape of a result ---------------------------------------------------------------


def test_check_has_status_message_and_next_step():
    c = dc.Check("x", "fail", "broken", "do this")
    d = c.as_dict()
    assert d == {"id": "x", "status": "fail", "message": "broken", "next": "do this"}
    with pytest.raises(ValueError):
        dc.Check("x", "bogus", "m")


def test_summary_fails_only_on_fail():
    assert dc.summarize([dc.Check("a", "pass", "m"), dc.Check("b", "warn", "m")])["ok"] is True
    assert dc.summarize([dc.Check("a", "pass", "m"), dc.Check("b", "fail", "m")])["ok"] is False
    assert dc.summarize([])["ok"] is True


# --- broker: DNS, TCP, TLS, IPv6 ---------------------------------------------------------


def test_broker_all_pass():
    net = FakeNet(dns={"b1": [V4]}, tcp={"192.0.2.10"})
    cs = dc.check_broker("b1:9093", net=net, tls=True)
    assert [c.status for c in cs] == ["pass", "pass", "pass"]
    assert [c.id for c in cs] == ["broker.dns", "broker.tcp", "broker.tls"]


def test_broker_dns_failure_stops_and_says_what_to_do():
    cs = dc.check_broker("nope:9092", net=FakeNet(), tls=False)
    assert cs[0].status == "fail" and cs[0].id == "broker.dns"
    assert cs[0].next
    assert len(cs) == 1


def test_broker_tcp_refused():
    net = FakeNet(dns={"b": [V4]}, tcp=set())
    cs = dc.check_broker("b:9092", net=net, tls=False)
    assert by_id(cs, "broker.tcp").status == "fail"
    assert "broker.tls" not in [c.id for c in cs]


def test_broker_ipv6_unreachable_but_ipv4_works_warns():
    net = FakeNet(dns={"b": [V6, V4]}, tcp={"192.0.2.10"})
    cs = dc.check_broker("b:9092", net=net, tls=False)
    tcp = by_id(cs, "broker.tcp")
    assert tcp.status == "warn"
    assert "IPv6" in tcp.message and "2001:db8::10" in tcp.message
    assert tcp.next


def test_broker_only_ipv6_and_unreachable_fails_with_ipv6_hint():
    net = FakeNet(dns={"b": [V6]}, tcp=set())
    tcp = by_id(dc.check_broker("b:9092", net=net, tls=False), "broker.tcp")
    assert tcp.status == "fail" and "IPv6" in tcp.message


def test_broker_tls_failure():
    net = FakeNet(dns={"b": [V4]}, tcp={"192.0.2.10"}, tls={"b": OSError("certificate verify")})
    tls = by_id(dc.check_broker("b:9093", net=net, tls=True), "broker.tls")
    assert tls.status == "fail" and "certificate" in tls.message


def test_broker_several_servers_each_checked():
    net = FakeNet(dns={"a": [V4], "b": [V4]}, tcp={"192.0.2.10"})
    cs = dc.check_broker("a:9092,b:9092", net=net, tls=False)
    assert {c.detail for c in cs if c.id == "broker.dns"} == {"a:9092", "b:9092"}


def test_broker_default_port_when_missing():
    net = FakeNet(dns={"b": [V4]}, tcp={"192.0.2.10"})
    dc.check_broker("b", net=net, tls=False, default_port=9092)
    assert ("tcp", "192.0.2.10", 9092) in net.calls


def test_kafka_uri_target():
    host_port = dc.broker_target("kafka://h1:9092,h2:9092/topic")
    assert host_port.servers == ["h1:9092", "h2:9092"] and host_port.kind == "kafka"


def test_eventhubs_uri_target_uses_amqp_tls_port():
    t = dc.broker_target("eventhubs://ns.servicebus.windows.net/hub")
    assert t.servers == ["ns.servicebus.windows.net:5671"] and t.tls and t.kind == "eventhubs"


def test_eventhubs_bare_namespace_gets_domain():
    t = dc.broker_target("eventhubs://ns/hub")
    assert t.servers == ["ns.servicebus.windows.net:5671"]


def test_bad_broker_uri_is_an_error():
    with pytest.raises(ValueError, match="kafka://"):
        dc.broker_target("http://x")


def test_broker_auth_pass_and_fail_with_fake_credential():
    ok = dc.check_broker_auth(dc.broker_target("eventhubs://ns/hub"), credential=Cred())
    assert ok.status == "pass" and ok.id == "broker.auth"
    bad = dc.check_broker_auth(
        dc.broker_target("eventhubs://ns/hub"), credential=Cred(RuntimeError("AADSTS70000"))
    )
    assert bad.status == "fail" and "AADSTS70000" in bad.message


def test_kafka_auth_without_client_library_warns():
    c = dc.check_broker_auth(dc.broker_target("kafka://h:9092/t"), probe=None)
    assert c.status == "warn" and c.id == "broker.auth"


def test_kafka_auth_probe_result():
    c = dc.check_broker_auth(dc.broker_target("kafka://h:9092/t"), probe=lambda servers: None)
    assert c.status == "pass"
    c = dc.check_broker_auth(
        dc.broker_target("kafka://h:9092/t"), probe=lambda s: (_ for _ in ()).throw(OSError("SASL"))
    )
    assert c.status == "fail" and "SASL" in c.message


# --- fabric ------------------------------------------------------------------------------

ONELAKE = "onelake.dfs.fabric.microsoft.com"


def fabric_checks(net=None, cred=None, **kw):
    return dc.check_fabric(
        "onelake://ws/lh.Lakehouse",
        net=net or FakeNet(dns={ONELAKE: [V4]}, tcp={"192.0.2.10"}),
        credential=cred or Cred(),
        **kw,
    )


def test_fabric_reachable_signed_in_item_exists():
    url = "https://onelake.dfs.fabric.microsoft.com/ws?resource=filesystem&recursive=false&directory=lh.Lakehouse&maxResults=1"
    net = FakeNet(dns={ONELAKE: [V4]}, tcp={"192.0.2.10"}, http={url: 200})
    cs = fabric_checks(net=net)
    assert [c.status for c in cs] == ["pass"] * len(cs)
    assert {"fabric.onelake", "fabric.auth", "fabric.item"} <= {c.id for c in cs}


def test_fabric_endpoint_unreachable_stops_early():
    net = FakeNet()
    cs = fabric_checks(net=net)
    assert by_id(cs, "fabric.onelake").status == "fail"
    assert "fabric.auth" not in [c.id for c in cs]


def test_fabric_sign_in_fails_clearly_and_never_prints_token():
    cs = fabric_checks(cred=Cred(RuntimeError("az login needed")), auth_mode="cli")
    a = by_id(cs, "fabric.auth")
    assert a.status == "fail" and "cli" in a.message and a.next
    assert "fabric.item" not in [c.id for c in cs]


def test_fabric_sign_in_reports_mode_and_no_token():
    cs = fabric_checks(auth_mode="spn")
    a = by_id(cs, "fabric.auth")
    assert "spn" in a.message and "sekret-token" not in json.dumps([c.as_dict() for c in cs])


def test_fabric_item_missing_404_and_forbidden_403():
    def run(code):
        net = FakeNet(dns={ONELAKE: [V4]}, tcp={"192.0.2.10"}, http={})
        net.http_get = lambda url, headers, timeout: code  # type: ignore[method-assign]
        return by_id(fabric_checks(net=net), "fabric.item")

    assert run(404).status == "fail" and "workspace" in run(404).message
    assert run(403).status == "fail" and "permission" in run(403).message.lower()
    assert run(401).status == "fail"
    assert run(500).status == "warn"


def test_fabric_item_token_sent_as_bearer_and_scope_is_storage():
    seen: dict[str, Any] = {}
    net = FakeNet(dns={ONELAKE: [V4]}, tcp={"192.0.2.10"})

    def get(url, headers, timeout):
        seen.update(headers)
        return 200

    net.http_get = get  # type: ignore[method-assign]
    cred = Cred()
    fabric_checks(net=net, cred=cred)
    assert seen["Authorization"] == "Bearer sekret-token"
    assert cred.scopes == ["https://storage.azure.com/.default"]


def test_fabric_bad_target_is_an_error():
    with pytest.raises(ValueError):
        dc.check_fabric("https://example.com/x", net=FakeNet(), credential=Cred())


# --- delta-rs limits ---------------------------------------------------------------------


def test_delta_without_deltalake_warns():
    c = dc.check_delta_limits(None, version=None)
    assert c.status == "warn" and "deltalake" in c.message


def test_delta_features_clean_table_passes():
    c = dc.check_delta_limits(
        lambda: {"reader_features": [], "configuration": {}}, version="0.17.0"
    )
    assert c.status == "pass"


def test_delta_deletion_vectors_fail_with_next_step():
    c = dc.check_delta_limits(
        lambda: {"reader_features": ["deletionVectors"], "configuration": {}}, version="0.17.0"
    )
    assert c.status == "fail" and "deletion vectors" in c.message.lower() and c.next


def test_delta_column_mapping_fail():
    c = dc.check_delta_limits(
        lambda: {"reader_features": [], "configuration": {"delta.columnMapping.mode": "name"}},
        version="0.17.0",
    )
    assert c.status == "fail" and "column mapping" in c.message.lower()


def test_delta_both_limits_named():
    c = dc.check_delta_limits(
        lambda: {
            "reader_features": ["deletionVectors", "columnMapping"],
            "configuration": {"delta.columnMapping.mode": "id"},
        },
        version="0.17.0",
    )
    assert "deletion vectors" in c.message.lower() and "column mapping" in c.message.lower()


def test_delta_unreadable_table_warns():
    def boom():
        raise OSError("no log")

    c = dc.check_delta_limits(boom, version="0.17.0")
    assert c.status == "warn" and "no log" in c.message


def test_delta_features_reads_a_local_table(tmp_path):
    pytest.importorskip("deltalake")
    import pyarrow as pa
    from deltalake import write_deltalake

    write_deltalake(str(tmp_path / "t"), pa.table({"a": [1, 2]}))
    f = dc.delta_features(str(tmp_path / "t"))
    assert f["reader_features"] == [] and "delta.columnMapping.mode" not in f["configuration"]


# --- the command: --json for everything, composition --------------------------------------


def test_report_keeps_earlier_keys_and_adds_checks():
    rep = doctor.report()
    assert rep["python"] and "pyarrow" in rep and rep["checks"] == []


def test_cli_json_includes_every_check(monkeypatch, capsys):
    net = FakeNet(dns={"b": [V4]}, tcp={"192.0.2.10"})
    monkeypatch.setattr(dc, "default_net", lambda: net)
    assert main(["doctor", "--json", "--broker", "kafka://b:9092/t"]) == 0
    o = json.loads(capsys.readouterr().out)
    ids = [c["id"] for c in o["checks"]]
    assert ids[:2] == ["broker.dns", "broker.tcp"]
    assert {"id", "status", "message", "next"} <= set(o["checks"][0])
    assert o["ok"] is True


def test_cli_exit_1_when_a_check_fails_and_text_has_lines(monkeypatch, capsys):
    monkeypatch.setattr(dc, "default_net", lambda: FakeNet())
    assert main(["doctor", "--broker", "kafka://b:9092/t", "--no-auth-check"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "broker.dns" in out and "Next:" in out


def test_cli_warn_does_not_fail(monkeypatch, capsys):
    net = FakeNet(dns={"b": [V6, V4]}, tcp={"192.0.2.10"})
    monkeypatch.setattr(dc, "default_net", lambda: net)
    assert main(["doctor", "--broker", "kafka://b:9092/t", "--no-auth-check"]) == 0
    assert "WARN" in capsys.readouterr().out


def test_cli_bad_target_is_a_one_line_error(capsys):
    assert main(["doctor", "--broker", "http://x"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("shape: error:") and "Traceback" not in err


def test_cli_fabric_uses_auth_options(monkeypatch, capsys):
    net = FakeNet(dns={ONELAKE: [V4]}, tcp={"192.0.2.10"})
    monkeypatch.setattr(dc, "default_net", lambda: net)
    cred = Cred()
    seen = {}

    def make(settings):
        seen.update(settings or {})
        return cred

    monkeypatch.setattr("shape.cli.auth.make_credential", make)
    rc = main(
        [
            "doctor",
            "--json",
            "--fabric",
            "onelake://ws/lh",
            "--auth",
            "spn",
            "--tenant-id",
            "t",
            "--client-id",
            "c",
            "--client-secret",
            "env://S",
        ]
    )
    o = json.loads(capsys.readouterr().out)
    assert seen["mode"] == "spn" and seen["client_secret"] == "env://S"
    assert "fabric.auth" in [c["id"] for c in o["checks"]]
    assert rc in (0, 1)


def test_cli_literal_secret_refused(capsys):
    assert (
        main(["doctor", "--fabric", "onelake://ws/lh", "--auth", "spn", "--client-secret", "x"])
        == 2
    )
    assert "credential reference" in capsys.readouterr().err


def test_credential_failure_building_is_a_fail_line_not_a_traceback(monkeypatch, capsys):
    net = FakeNet(dns={ONELAKE: [V4]}, tcp={"192.0.2.10"})
    monkeypatch.setattr(dc, "default_net", lambda: net)

    def make(settings):
        raise ValueError("--auth needs the shape-fabric plugin")

    monkeypatch.setattr("shape.cli.auth.make_credential", make)
    assert main(["doctor", "--json", "--fabric", "onelake://ws/lh"]) == 1
    o = json.loads(capsys.readouterr().out)
    assert by_id([_C(c) for c in o["checks"]], "fabric.auth").status == "fail"


class _C:
    def __init__(self, d):
        self.__dict__.update(d)


# --- the real network layer, locally (no external network) --------------------------------


def test_real_net_connects_to_a_local_listener():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        net = dc.SocketNet()
        addrs = net.resolve("127.0.0.1", port)
        assert any(a[1] == "127.0.0.1" for a in addrs)
        net.connect(addrs[0], port, 2.0)
    finally:
        srv.close()


def test_real_net_refused_port_raises():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    with pytest.raises(OSError):
        dc.SocketNet().connect((socket.AF_INET, "127.0.0.1"), port, 1.0)


@pytest.mark.live
def test_live_onelake_endpoint_reachable():
    cs = dc.check_fabric_endpoint(dc.SocketNet())
    assert cs[0].status == "pass"


@pytest.mark.live
def test_live_eventhubs_namespace_dns():
    cs = dc.check_broker("management.azure.com:443", net=dc.SocketNet(), tls=True)
    assert [c.status for c in cs][:2] == ["pass", "pass"]
