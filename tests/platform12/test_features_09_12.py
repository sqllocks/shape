import json
import threading
import urllib.request

from shape.observability import EventBus, Metrics
from shape.scenarios import Scenario, apply_scenario, generate_scenario
from shape.testing import generate_tests
from shape.webapp import ShapeService, serve

S = {
    "rows": 100,
    "columns": {
        "age": {
            "kind": "numeric",
            "count": 100,
            "null_count": 0,
            "distinct_estimate": 50,
            "mean": 40,
            "variance_population": 100,
            "min": 18,
            "max": 80,
        }
    },
}


def test_test_generation():
    cases = generate_tests(S, {"columns": {"age": {"required": True, "min": 18, "max": 80}}}, 50, 1)
    assert {x.name for x in cases} == {"valid", "missing_age", "above_max_age", "below_min_age"}


def test_scenario():
    sc = Scenario("aging", {"columns.age.mean": "+10%", "columns.age.variance_population": 121})
    m = apply_scenario(S, sc)
    assert m["columns"]["age"]["mean"] == 44 and m["columns"]["age"]["variance_population"] == 121
    _, d, r = generate_scenario(S, sc, 1000, 2)
    assert len(d["age"]) == 1000


def test_observability():
    m = Metrics()
    m.inc("shape_rows_total", 10, source="x")
    m.gauge("shape_quality", 0.99)
    assert "shape_rows_total" in m.prometheus() and "shape_quality" in m.prometheus()
    seen = []
    b = EventBus()
    b.subscribe(seen.append)
    b.emit("drift", field="age")
    assert seen[0].attributes["field"] == "age"


def test_web_api_live_server():
    s = ShapeService()
    s.put("customer", S)
    server = serve(s)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert json.load(urllib.request.urlopen(base + "/health"))["status"] == "ok"
        assert json.load(urllib.request.urlopen(base + "/v1/shapes/customer"))["rows"] == 100
        assert (
            json.load(
                urllib.request.urlopen(base + "/v1/shapes/customer/query?q=column(%22age%22).mean")
            )["result"]
            == 40
        )
        assert b"<h1>customer</h1>" in urllib.request.urlopen(base + "/dashboard/customer").read()
    finally:
        server.shutdown()
        server.server_close()
