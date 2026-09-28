from shape.distributed import DistributedProfiler, partition_rows
from shape.etl import ShapeETL
from shape.hub import ShapeHub, ShapeRef
from shape.packages import DomainPackageStore
from shape.packs import DomainDefinition, DomainField


class T:
    def __init__(self):
        self.calls = []

    def request(self, m, p, b=None):
        self.calls.append((m, p, b))
        return {"shape": {"rows": 3}, "versions": ["1"]} if m == "GET" else {"ok": True}


def test_hub_protocol():
    t = T()
    h = ShapeHub(t)
    r = ShapeRef("o", "p", "customer")
    assert h.pull(r)["rows"] == 3
    assert h.push(r, {"rows": 1})["ok"]
    assert h.promote(r, "production")["ok"]
    assert len(t.calls) == 3


def test_domain_packages(tmp_path):
    s = DomainPackageStore(tmp_path)
    d1 = DomainDefinition("person", "1.0.0", (DomainField("id", "int"),))
    d2 = DomainDefinition(
        "person", "1.1.0", (DomainField("id", "int"), DomainField("name", "string", False))
    )
    h = s.publish(d1)
    s.publish(d2)
    assert s.versions("person") == ("1.0.0", "1.1.0")
    assert s.resolve("person")["domain"]["version"] == "1.1.0"
    assert s.install("person", tmp_path / "installed").version == "1.1.0"
    assert s.publish(d1) == h


def test_etl_evidence():
    r = (
        ShapeETL()
        .stage("positive", lambda rows: [x for x in rows if x["x"] > 0])
        .run([{"x": -1}, {"x": 2}, {"x": 3}])
    )
    assert (
        len(r.rows) == 2 and r.evidence[0].before["rows"] == 3 and r.evidence[0].after["rows"] == 2
    )


def test_distributed_profile_merge():
    rows = [{"x": i, "cat": str(i % 7)} for i in range(10000)]
    s = DistributedProfiler(4).profile(partition_rows(rows, 8))
    assert s["rows"] == 10000 and abs(s["columns"]["x"]["mean"] - 4999.5) < 1e-9


def test_live_hub_http_roundtrip():
    import threading

    from shape.hub import HTTPTransport, serve_hub

    server = serve_hub(token="t")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        h = ShapeHub(HTTPTransport(f"http://127.0.0.1:{server.server_port}", token="t"))
        r = ShapeRef("acme", "prod", "customer")
        h.push(r, {"rows": 7})["content_id"]
        assert h.pull(r)["rows"] == 7
        h.promote(r, "production")
        assert h.pull(ShapeRef("acme", "prod", "customer", "production"))["rows"] == 7
        assert h.versions(r)
    finally:
        server.shutdown()
        server.server_close()
