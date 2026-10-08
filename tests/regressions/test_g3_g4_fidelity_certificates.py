"""G3 and G4 (plan Appendix A).

G3: ``certify`` scored a column missing from the generated data as 1.0 and passed a reference
that describes nothing. G4: ``shape certify-shapes`` always exited 0, whatever the score.
"""

from __future__ import annotations

import json

from shape.capture import capture_rows
from shape.cli.main import main
from shape.generation import certify


def _rows(n=50):
    return [{"x": i, "s": "a" if i % 2 else "b"} for i in range(n)]


def test_g3_missing_column_scores_zero():
    ref = capture_rows(_rows()).to_dict()
    generated = [{"x": r["x"]} for r in _rows()]  # column "s" is missing
    c = certify(ref, generated, tolerance=0.1)
    missing = [m for m in c.metrics if m.path == "columns.s"]
    assert len(missing) == 1
    assert missing[0].score == 0.0 and missing[0].passed is False
    assert c.passed is False


def test_g3_missing_column_lowers_the_overall_score():
    ref = capture_rows(_rows()).to_dict()
    full = certify(ref, _rows(), tolerance=0.1)
    partial = certify(ref, [{"x": r["x"]} for r in _rows()], tolerance=0.1)
    only_x = [m for m in full.metrics if m.path.startswith("columns.x.")]
    # the missing column contributes a zero to the mean, so the score is below the mean of the
    # metrics that exist
    assert partial.score < sum(m.score for m in only_x) / len(only_x)
    assert partial.score < full.score


def test_g3_empty_reference_fails():
    c = certify({"columns": {}}, _rows(), tolerance=0.1)
    assert c.passed is False
    assert c.score == 0.0


def test_g3_reference_without_columns_key_fails():
    c = certify({}, _rows(), tolerance=0.1)
    assert c.passed is False and c.score == 0.0


def test_g3_empty_generated_data_fails_against_a_reference():
    ref = capture_rows(_rows()).to_dict()
    c = certify(ref, [], tolerance=0.1)
    assert c.passed is False


def test_g3_identical_data_still_passes():
    ref = capture_rows(_rows()).to_dict()
    c = certify(ref, _rows(), tolerance=0.01)
    assert c.passed and c.score > 0.99


def _shape_file(path, rows):
    path.write_text(json.dumps(capture_rows(rows).to_dict()))
    return str(path)


def test_g4_certify_shapes_exits_nonzero_below_the_threshold(tmp_path, capsys):
    target = _shape_file(tmp_path / "t.json", _rows())
    observed = _shape_file(tmp_path / "o.json", [{"y": i} for i in range(50)])
    assert main(["certify-shapes", target, observed]) != 0
    out = json.loads(capsys.readouterr().out)
    assert out["score"] < 0.9


def test_g4_certify_shapes_exits_zero_for_equal_shapes(tmp_path, capsys):
    target = _shape_file(tmp_path / "t.json", _rows())
    assert main(["certify-shapes", target, target]) == 0
    assert json.loads(capsys.readouterr().out)["score"] == 1.0


def test_g4_threshold_option_decides_the_exit_code(tmp_path, capsys):
    target = _shape_file(tmp_path / "t.json", _rows())
    shifted = _shape_file(tmp_path / "o.json", [{"x": i * 2, "s": "a"} for i in range(50)])
    capsys.readouterr()
    assert main(["certify-shapes", target, shifted, "--threshold", "0.0"]) == 0
    assert main(["certify-shapes", target, shifted, "--threshold", "1.0"]) != 0


def test_g4_certify_shapes_empty_target_fails(tmp_path):
    target = tmp_path / "t.json"
    target.write_text(json.dumps({"columns": {}}))
    assert main(["certify-shapes", str(target), str(target)]) != 0
