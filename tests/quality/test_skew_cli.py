"""``shape skew`` (W3-11, item 4)."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main


def table(seed, shift=0.0, null_rate=0.0, n=1500):
    rng = np.random.default_rng(seed)
    nulls = rng.random(n) < null_rate
    return pa.table(
        {
            "x": rng.normal(0, 1, n) + shift,
            "c": rng.choice(["a", "b", "c"], n),
            "opt": pa.array([None if m else 1 for m in nulls]),
            "y": rng.random(n) < 0.4,
            "grp": rng.choice(["g1", "g2"], n),
        }
    )


@pytest.fixture
def files(tmp_path):
    out = {}
    for name, t in {
        "train": table(1),
        "same": table(2),
        "drifted": table(3, shift=1.2),
    }.items():
        p = tmp_path / f"{name}.parquet"
        pq.write_table(t, p)
        out[name] = p
    return out


def test_exit_0_when_nothing_is_flagged(files, capsys):
    assert main(["skew", str(files["train"]), str(files["same"])]) == 0
    out = capsys.readouterr().out
    assert "# Shape training-serving skew" in out and "no skew flagged" in out


def test_exit_1_when_a_feature_is_flagged_and_json(files, capsys):
    assert main(["skew", str(files["train"]), str(files["drifted"]), "--json"]) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-skew-report" and doc["version"] == 1
    feats = doc["tables"]["train"]["features"]
    assert feats[0]["feature"] == "x" and feats[0]["flagged"]


def test_output_file(files, tmp_path, capsys):
    out = tmp_path / "report.json"
    assert main(["skew", str(files["train"]), str(files["drifted"]), "-o", str(out)]) == 1
    assert json.loads(out.read_text())["flagged"] is True
    assert "FLAGGED" in capsys.readouterr().out


def test_features_label_and_slice_by(files, capsys):
    argv = ["skew", str(files["train"]), str(files["drifted"]), "--json", "--label", "y"]
    assert main([*argv, "--features", "x,c", "--slice-by", "grp"]) == 1
    t = json.loads(capsys.readouterr().out)["tables"]["train"]
    assert [f["feature"] for f in t["features"]] == ["x", "c"]
    assert t["label"]["column"] == "y"
    assert [s["slice"] for s in t["slices"]["slices"]] == ["g1", "g2"] or {
        s["slice"] for s in t["slices"]["slices"]
    } == {"g1", "g2"}


def test_threshold_option(files, capsys):
    # a very high PSI threshold leaves the shifted feature unflagged
    assert (
        main(
            [
                "skew",
                str(files["train"]),
                str(files["drifted"]),
                "--threshold",
                "psi=50",
                "--threshold",
                "out_of_range_share=1",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(["skew", str(files["train"]), str(files["same"]), "--threshold", "psi=0", "--json"])
        == 1
    )


def test_profiles(tmp_path, capsys):
    a = tmp_path / "a.shape"
    b = tmp_path / "b.shape"
    for path, t in ((a, table(1)), (b, table(2, null_rate=0.5))):
        data = tmp_path / f"{path.stem}.parquet"
        pq.write_table(t, data)
        assert main(["profile", str(data), "-o", str(path), "--name", "t"]) == 0
    capsys.readouterr()
    assert main(["skew", str(a), str(b), "--json"]) == 1
    f = {f["feature"]: f for f in json.loads(capsys.readouterr().out)["tables"]["t"]["features"]}
    assert f["opt"]["flagged"] and f["x"]["psi"] is None


def test_csv_inputs(tmp_path, capsys):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    pacsv.write_csv(table(1), a)
    pacsv.write_csv(table(2), b)
    assert main(["skew", str(a), str(b), "--format", "csv"]) == 0


@pytest.mark.parametrize(
    "argv, message",
    [
        (["nope.parquet"], "not found"),
        (["--threshold", "psi"], "KEY=VALUE"),
        (["--threshold", "bogus=1"], "unknown threshold"),
        (["--features", "zzz"], "not a column"),
        (["--features", "x,"], "COL"),
        (["--label", "zzz"], "not a column"),
        (["--label", "c"], "two values"),
        (["--slice-by", "zzz"], "slice column"),
        (["--source", "s"], "--project"),
        (["--project", "missing.yml"], "not found"),
    ],
)
def test_unusable_input_is_exit_2(files, capsys, argv, message):
    serving = argv[0] if argv[0].endswith(".parquet") else str(files["same"])
    rest = argv[1:] if argv[0].endswith(".parquet") else argv
    assert main(["skew", str(files["train"]), serving, *rest]) == 2
    assert message in capsys.readouterr().err


def test_a_json_file_that_is_not_a_profile_is_exit_2(files, tmp_path, capsys):
    bad = tmp_path / "x.json"
    bad.write_text("[1, 2]")
    assert main(["skew", str(files["train"]), str(bad)]) == 2
    assert "not a profile" in capsys.readouterr().err


# -- thresholds from shape.yml ------------------------------------------------------------------


def project(tmp_path, body):
    p = tmp_path / "shape.yml"
    p.write_text(body)
    return p


def test_thresholds_from_the_project_file_and_flags_override(files, tmp_path, capsys):
    p = project(
        tmp_path,
        "format: shape-project\nversion: 1\nsources:\n  feed:\n    path: data\n"
        "    thresholds:\n      psi: 50\n      out_of_range_share: 1\n      mean_shift_std: 1.0\n",
    )
    # the file raises the thresholds: not flagged (and its drift-only keys are ignored)
    assert main(["skew", str(files["train"]), str(files["drifted"]), "--project", str(p)]) == 0
    capsys.readouterr()
    # a folder works, and --source picks one
    assert (
        main(
            [
                "skew",
                str(files["train"]),
                str(files["drifted"]),
                "--project",
                str(tmp_path),
                "--source",
                "feed",
            ]
        )
        == 0
    )
    capsys.readouterr()
    # a flag wins over the file
    assert (
        main(
            [
                "skew",
                str(files["train"]),
                str(files["drifted"]),
                "--project",
                str(p),
                "--threshold",
                "psi=0.2",
            ]
        )
        == 1
    )


def test_project_with_several_sources_needs_a_choice(files, tmp_path, capsys):
    p = project(
        tmp_path,
        "sources:\n  a:\n    path: x\n    thresholds: {psi: 50, out_of_range_share: 1}\n"
        "  b:\n    path: y\n",
    )
    assert main(["skew", str(files["train"]), str(files["drifted"]), "--project", str(p)]) == 1
    assert "several sources" in capsys.readouterr().err
    assert (
        main(
            [
                "skew",
                str(files["train"]),
                str(files["drifted"]),
                "--project",
                str(p),
                "--source",
                "a",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "skew",
                str(files["train"]),
                str(files["drifted"]),
                "--project",
                str(p),
                "--source",
                "zzz",
            ]
        )
        == 2
    )
    assert "no source" in capsys.readouterr().err


def test_a_bad_threshold_in_the_project_file_is_exit_2(files, tmp_path, capsys):
    p = project(tmp_path, "sources:\n  a:\n    path: x\n    thresholds: {psi: -1}\n")
    assert main(["skew", str(files["train"]), str(files["same"]), "--project", str(p)]) == 2
    assert "zero or more" in capsys.readouterr().err


def test_a_project_file_without_sources_applies_nothing(files, tmp_path, capsys):
    p = project(tmp_path, "format: shape-project\nversion: 1\n")
    assert main(["skew", str(files["train"]), str(files["same"]), "--project", str(p)]) == 0
