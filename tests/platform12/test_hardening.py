import numpy as np

from shape.generation.joint import JointModel, generate_joint_numeric


def test_joint_psd_repair():
    m = JointModel(("a", "b"), (0.0, 0.0), (1.0, 1.0), ((1.0, 1.2), (1.2, 1.0)))
    g = generate_joint_numeric(m, 10000, 1)
    assert np.isfinite(g["a"]).all() and len(g["b"]) == 10000
