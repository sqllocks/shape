from shape.capture import capture_rows
from shape.generation import Choice, GenerationPlan, certify


def test_categorical_distribution_drift_fails():
    a = GenerationPlan((("segment", Choice(("A", "B"), (0.9, 0.1))),), seed=1)
    b = GenerationPlan((("segment", Choice(("A", "B"), (0.1, 0.9))),), seed=2)
    ref = capture_rows(a.rows(5000)).to_dict()
    c = certify(ref, b.rows(5000), tolerance=0.1)
    assert not c.passed
    assert any(m.path.endswith("topk_distribution") and not m.passed for m in c.metrics)
