"""Item 5 without the libraries: the report format, table selection and the exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from shape_integrations import evaluate, evaluation
from shape_integrations.testing import write_tables

DATA = Path(__file__).parent / "data" / "evaluation"


def run(*argv: str) -> int:
    from shape.plugins.cli import run_command
    from shape.plugins.host import default_host

    return run_command(default_host(), "evaluate", list(argv))


@pytest.fixture
def dirs(tmp_path):
    return (
        str(write_tables(tmp_path / "real", seed=1)),
        str(write_tables(tmp_path / "synth", seed=2)),
        str(write_tables(tmp_path / "control", seed=3)),
    )


# -- the persisted format -------------------------------------------------------------------


def test_a_report_declares_its_format_and_an_integer_version():
    r = evaluation.make_report("sdmetrics", "0.32.0", {"overall_score": 0.9})
    assert r == {
        "format": "shape-evaluation",
        "version": 1,
        "tool": "sdmetrics",
        "tool_version": "0.32.0",
        "results": {"overall_score": 0.9},
    }
    assert type(r["version"]) is int


def test_non_finite_numbers_become_null_and_the_json_is_strict():
    r = evaluation.make_report("sdmetrics", "1", {"a": float("nan"), "b": [float("inf"), 1.0]})
    assert r["results"] == {"a": None, "b": [None, 1.0]}
    json.loads(evaluation.to_json(r))  # strict JSON: no NaN token


def test_numpy_scalars_are_plain_numbers():
    import numpy as np

    r = evaluation.make_report("anonymeter", "1", {"x": np.float64(0.5), "n": np.int64(3)})
    assert r["results"] == {"x": 0.5, "n": 3}
    assert type(r["results"]["n"]) is int


def test_the_json_is_deterministic():
    r = evaluation.make_report("sdmetrics", "1", {"b": 1, "a": 2})
    assert evaluation.to_json(r) == evaluation.to_json(dict(reversed(list(r.items()))))


def test_an_unknown_tool_cannot_be_written():
    with pytest.raises(evaluation.EvaluationReportError):
        evaluation.make_report("other", "1", {})


@pytest.mark.parametrize("name", ["v1-sdmetrics.json", "v1-anonymeter.json"])
def test_version_1_reports_still_read(name):
    r = evaluation.read_report(DATA / name)
    assert (r["format"], r["version"]) == ("shape-evaluation", 1)
    assert r["tool"] in evaluation.TOOLS and r["tool_version"] and r["results"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"format": "other"}, "is not a shape-evaluation report"),
        ({"version": "1"}, "must be an integer"),
        ({"version": True}, "must be an integer"),
        ({"tool": "nope"}, "unknown tool"),
        ({"results": []}, "required"),
        ({"tool_version": 3}, "required"),
    ],
)
def test_a_report_that_is_not_a_version_1_report_is_refused(tmp_path, change, message):
    raw = json.loads((DATA / "v1-sdmetrics.json").read_text())
    raw.update(change)
    p = tmp_path / "r.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(evaluation.EvaluationReportError, match=message):
        evaluation.read_report(p)


def test_a_newer_version_is_refused_with_an_upgrade_message(tmp_path):
    raw = json.loads((DATA / "v1-sdmetrics.json").read_text())
    raw["version"] = 2
    p = tmp_path / "r.json"
    p.write_text(json.dumps(raw))
    with pytest.raises(evaluation.EvaluationVersionError, match="Upgrade Shape"):
        evaluation.read_report(p)


def test_unreadable_files_are_refused(tmp_path):
    p = tmp_path / "r.json"
    with pytest.raises(evaluation.EvaluationReportError):
        evaluation.read_report(p)
    p.write_text("[1]")
    with pytest.raises(evaluation.EvaluationReportError):
        evaluation.read_report(p)


# -- table selection ------------------------------------------------------------------------


def _t(**cols):
    return pa.table({k: v for k, v in cols.items()})


def test_every_real_table_is_evaluated_by_default():
    real = {"a": _t(x=[1]), "b": _t(y=[1])}
    assert evaluate.select_tables(real, {"synthetic": dict(real)}, None) == ["a", "b"]


def test_named_tables_are_taken_in_the_order_given():
    real = {"a": _t(x=[1]), "b": _t(y=[1])}
    assert evaluate.select_tables(real, {"synthetic": dict(real)}, ["b", "a"]) == ["b", "a"]


@pytest.mark.parametrize(
    ("wanted", "message"),
    [(["zzz"], "not in the real"), (["a", "a"], "twice")],
)
def test_bad_table_names_are_refused(wanted, message):
    real = {"a": _t(x=[1])}
    with pytest.raises(evaluate.EvaluateInputError, match=message):
        evaluate.select_tables(real, {"synthetic": dict(real)}, wanted)


def test_a_table_missing_from_the_other_side_is_refused():
    real = {"a": _t(x=[1]), "b": _t(x=[1])}
    with pytest.raises(evaluate.EvaluateInputError, match="'b' is not in the synthetic"):
        evaluate.select_tables(real, {"synthetic": {"a": real["a"]}}, None)


def test_different_columns_are_refused_but_a_different_column_order_is_not():
    real = {"a": _t(x=[1], y=[2])}
    with pytest.raises(evaluate.EvaluateInputError, match="columns differ"):
        evaluate.select_tables(real, {"synthetic": {"a": _t(x=[1], z=[2])}}, None)
    assert evaluate.select_tables(real, {"synthetic": {"a": _t(y=[2], x=[1])}}, None) == ["a"]


@pytest.mark.parametrize("text", ["a,,b", ",", " "])
def test_empty_names_in_a_list_are_refused(text):
    with pytest.raises(evaluate.EvaluateInputError):
        evaluate.split_names(text, "--tables")


def test_a_list_is_split_and_trimmed():
    assert evaluate.split_names(" a , b ", "--tables") == ["a", "b"]
    assert evaluate.split_names(None, "--tables") is None


# -- exit codes -----------------------------------------------------------------------------


def test_a_missing_directory_exits_2(dirs, tmp_path, capsys):
    real, synth, _ = dirs
    assert run("sdmetrics", str(tmp_path / "nope"), synth) == 2
    assert "real directory not found" in capsys.readouterr().err
    assert run("sdmetrics", real, str(tmp_path / "nope")) == 2
    assert "synthetic directory not found" in capsys.readouterr().err


def test_a_directory_without_tables_exits_2(dirs, tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert run("sdmetrics", str(tmp_path / "empty"), dirs[1]) == 2
    assert "no Parquet, CSV or JSONL tables" in capsys.readouterr().err


def test_a_file_where_a_directory_is_expected_exits_2(dirs, capsys):
    assert run("sdmetrics", str(Path(dirs[0]) / "people.parquet"), dirs[1]) == 2
    assert "directory not found" in capsys.readouterr().err


def test_an_unknown_table_exits_2(dirs, capsys):
    assert run("sdmetrics", dirs[0], dirs[1], "--tables", "nope") == 2
    assert "'nope'" in capsys.readouterr().err


def test_anonymeter_needs_a_control_directory(dirs, capsys):
    assert run("anonymeter", dirs[0], dirs[1]) == 2
    assert "--control" in capsys.readouterr().err


def test_an_unknown_or_repeated_attack_exits_2(dirs, capsys):
    base = ["anonymeter", dirs[0], dirs[1], "--control", dirs[2]]
    assert run(*base, "--attacks", "hacking") == 2
    assert "unknown attack 'hacking'" in capsys.readouterr().err
    assert run(*base, "--attacks", "inference,inference") == 2
    assert "twice" in capsys.readouterr().err


def test_a_missing_control_table_exits_2(dirs, tmp_path, capsys):
    only = write_tables(tmp_path / "c2", seed=4, extra=False)
    assert run("anonymeter", dirs[0], dirs[1], "--control", str(only)) == 2
    assert "not in the control" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("tool", "library", "extra_args"),
    [("sdmetrics", "sdmetrics", []), ("anonymeter", "anonymeter", ["--control", "C"])],
)
def test_without_the_library_the_command_exits_2_with_the_pip_command(
    dirs, capsys, hide_library, tool, library, extra_args
):
    hide_library(library)
    args = [a if a != "C" else dirs[2] for a in extra_args]
    assert run(tool, dirs[0], dirs[1], *args) == 2
    name = {"sdmetrics": "SDMetrics", "anonymeter": "Anonymeter"}[tool]
    assert capsys.readouterr().err.strip() == (
        f"shape: error: {name} needs the '{library}' extra: "
        f"pip install 'sqllocks-shape-integrations[{library}]'"
    )
