import numpy as np

from shape.generation.joint import fit_joint_numeric, generate_joint_numeric


def test_joint_reconstruction_correlation():
    rng = np.random.default_rng(4)
    x = rng.normal(size=50000)
    y = 0.85 * x + rng.normal(scale=0.3, size=50000)
    m = fit_joint_numeric({"x": x, "y": y})
    g = generate_joint_numeric(m, 100000, 5)
    assert abs(np.corrcoef(x, y)[0, 1] - np.corrcoef(g["x"], g["y"])[0, 1]) < 0.02
