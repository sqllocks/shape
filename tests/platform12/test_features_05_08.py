import numpy as np
import pytest

from shape.generation.joint import fit_joint_numeric, generate_joint_numeric
from shape.lineage import LineageGraph
from shape.reference import ReferenceAssetStore
from shape.temporal import detect_period, fit_temporal, generate_temporal, generate_temporal_values


def test_joint_reconstruction_correlation():
    rng = np.random.default_rng(4)
    x = rng.normal(size=50000)
    y = 0.85 * x + rng.normal(scale=0.3, size=50000)
    m = fit_joint_numeric({"x": x, "y": y})
    g = generate_joint_numeric(m, 100000, 5)
    assert abs(np.corrcoef(x, y)[0, 1] - np.corrcoef(g["x"], g["y"])[0, 1]) < 0.02


def test_temporal_fit_generate():
    t = np.arange(0, 10000, 10, dtype=float)
    m = fit_temporal(t)
    g = generate_temporal(m, 1000, 3)
    assert len(g) == 1000 and np.all(np.diff(g) >= 0) and abs(np.diff(g).mean() - 10) < 1


def test_reference_assets_immutable(tmp_path):
    s = ReferenceAssetStore(tmp_path)
    r = s.publish("states", "2026", [{"code": "OH", "name": "Ohio"}])
    assert s.index("states", "2026", "code")["OH"]["name"] == "Ohio"
    assert s.publish("states", "2026", [{"code": "OH", "name": "Ohio"}]).content_id == r.content_id
    with pytest.raises(ValueError):
        s.publish("states", "2026", [{"code": "OH", "name": "Different"}])


def test_lineage_blast_radius_and_cycle():
    g = LineageGraph()
    for n in ("raw", "clean", "model", "report"):
        g.add_shape(n, {})
    g.connect("raw", "clean")
    g.connect("clean", "model")
    g.connect("model", "report")
    assert g.downstream("raw") == ("clean", "model", "report")
    with pytest.raises(ValueError):
        g.connect("report", "raw")


def test_temporal_value_replay():
    t = np.arange(1000, dtype=float)
    y = 5 + 0.01 * t + 2 * np.sin(2 * np.pi * t / 24)
    m = fit_temporal(t, y, period=24)
    g = generate_temporal_values(m, t, baseline=float(y.mean()), seed=2)
    assert len(g) == 1000 and np.isfinite(g).all()


def test_period_detection():
    t = np.arange(240, dtype=float)
    y = np.sin(2 * np.pi * t / 24)
    assert abs(detect_period(y) - 24) < 1e-9
    assert abs(fit_temporal(t, y, period="auto").period - 24) < 1e-9
