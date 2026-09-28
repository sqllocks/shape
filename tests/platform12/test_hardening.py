import random
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from shape.distributed import DistributedProfiler, merge_shapes
from shape.etl import ShapeETL
from shape.generation.joint import JointModel, generate_joint_numeric
from shape.hub import HTTPTransport, HubError, ShapeHub, ShapeRef, serve_hub
from shape.lineage import LineageGraph
from shape.observability import Metrics
from shape.packages import DomainPackageStore
from shape.packs import DomainDefinition, DomainField
from shape.reference import ReferenceAssetStore
from shape.scenarios import Scenario, apply_scenario
from shape.temporal import fit_temporal, generate_temporal
from shape.webapp import ShapeService, serve


def test_hub_auth_rejected():
    s = serve_hub(token="secret")
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        with pytest.raises(HubError):
            ShapeHub(HTTPTransport(f"http://127.0.0.1:{s.server_port}", token="bad")).pull(
                ShapeRef("o", "p", "n")
            )
    finally:
        s.shutdown()
        s.server_close()


def test_package_version_immutable(tmp_path):
    s = DomainPackageStore(tmp_path)
    s.publish(DomainDefinition("x", "1", (DomainField("a", "int"),)))
    with pytest.raises(ValueError):
        s.publish(DomainDefinition("x", "1", (DomainField("b", "int"),)))


def test_etl_contract_failure():
    with pytest.raises(ValueError):
        ShapeETL().stage("bad", lambda r: r, {"columns": {"missing": {"required": True}}}).run(
            [{"x": 1}]
        )


def test_distributed_empty_and_random_equivalence():
    assert merge_shapes([])["rows"] == 0
    for seed in range(20):
        rng = random.Random(seed)
        rows = [{"x": rng.randint(-100, 100)} for _ in range(1000)]
        s = DistributedProfiler(4).profile([rows[i::7] for i in range(7)])
        assert s["rows"] == 1000
        assert abs(s["columns"]["x"]["mean"] - sum(x["x"] for x in rows) / 1000) < 1e-9


def test_joint_psd_repair():
    m = JointModel(("a", "b"), (0.0, 0.0), (1.0, 1.0), ((1.0, 1.2), (1.2, 1.0)))
    g = generate_joint_numeric(m, 10000, 1)
    assert np.isfinite(g["a"]).all() and len(g["b"]) == 10000


def test_temporal_single_and_empty():
    m = fit_temporal([5])
    assert generate_temporal(m, 1)[0] == 5
    with pytest.raises(ValueError):
        fit_temporal([])


def test_reference_large_lookup(tmp_path):
    s = ReferenceAssetStore(tmp_path)
    s.publish("r", "1", ({"k": str(i)} for i in range(10000)))
    assert len(s.index("r", "1", "k")) == 10000


def test_lineage_deep_nonrecursive():
    g = LineageGraph()
    for i in range(3000):
        g.add_shape(str(i), {})
    for i in range(2999):
        g.connect(str(i), str(i + 1))
    assert len(g.downstream("0")) == 2999
    with pytest.raises(ValueError):
        g.connect("2999", "0")


def test_scenario_does_not_mutate_source():
    s = {"columns": {"x": {"mean": 10}}}
    m = apply_scenario(s, Scenario("x", {"columns.x.mean": "-20%"}))
    assert s["columns"]["x"]["mean"] == 10 and m["columns"]["x"]["mean"] == 8


def test_metrics_threadsafe():
    m = Metrics()

    def f():
        for _ in range(10000):
            m.inc("x")

    ts = [threading.Thread(target=f) for _ in range(8)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert list(m.snapshot()["counters"].values()) == [80000]


def test_web_404_and_bad_query():
    s = ShapeService()
    s.put("x", {"rows": 1, "columns": {}})
    server = serve(s)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/missing")
        assert e.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/v1/shapes/x/query?q=__import__(%22os%22)")
        assert e.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
