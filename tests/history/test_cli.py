"""W3-03: ``shape bisect``, ``shape bisect layers`` and ``shape timelapse`` on the command line,
and the JSON each prints equals ``to_dict()`` of the Python API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from history_helpers import History, layered, total_step, value_history

from shape.cli.main import main
from shape.history import bisect, bisect_layers, timelapse
from shape.registry.local import LocalRegistry


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def ref(h: History, i: int) -> str:
    return str(LocalRegistry(h.registry).log(h.name)[i]["content_id"])


# ---- shape bisect -------------------------------------------------------------------------------


def test_bisect_prints_json_equal_to_the_api(step_history: History, capsys: Any):
    h = step_history
    code, out, _ = run(
        capsys,
        "bisect",
        str(h.registry),
        "orders",
        "--good",
        ref(h, 0),
        "--bad",
        ref(h, 30),
        "--json",
        "--no-project",
    )
    assert code == 0
    api = bisect(h.registry, "orders", good=ref(h, 0), bad=ref(h, 30)).to_dict()
    assert json.loads(out) == api
    assert api["first_bad"]["business_date"] == h.event["start"]


def test_bisect_text_names_the_day_and_the_column(step_history: History, capsys: Any):
    h = step_history
    code, out, _ = run(
        capsys,
        "bisect",
        str(h.registry),
        "orders",
        "--good",
        ref(h, 0),
        "--bad",
        "latest",
        "--no-project",
    )
    assert code == 0
    assert f"first bad version: {h.event['start']}" in out
    assert "last good version: 2026-03-14" in out
    assert "total: " in out and "tested " in out


def test_bisect_exit_2_when_bad_tests_good(step_history: History, capsys: Any):
    h = step_history
    code, out, err = run(
        capsys,
        "bisect",
        str(h.registry),
        "orders",
        "--good",
        ref(h, 0),
        "--bad",
        ref(h, 10),
        "--no-project",
    )
    assert code == 2 and out == ""
    assert "shape: error:" in err and "tests good" in err


def test_bisect_exit_2_when_good_tests_bad(tmp_path: Path, capsys: Any):
    h = value_history(tmp_path, 20, {"v": 5})
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"columns": {"v": {"max": 3}}}))
    code, _, err = run(
        capsys,
        "bisect",
        str(h.registry),
        "feed",
        "--good",
        ref(h, 10),
        "--bad",
        ref(h, 19),
        "--contract",
        str(contract),
        "--no-project",
    )
    assert code == 2 and "itself tests bad" in err
    code, out, _ = run(
        capsys,
        "bisect",
        str(h.registry),
        "feed",
        "--good",
        ref(h, 0),
        "--bad",
        ref(h, 19),
        "--contract",
        str(contract),
        "--no-project",
        "--json",
    )
    assert code == 0 and json.loads(out)["first_bad"]["business_date"] == h.dates[5]


def test_bisect_exit_2_for_a_share_safe_version(tmp_path: Path, capsys: Any):
    from history_helpers import value_table

    import shape
    from shape.privacy.safe_profile import to_safe_profile

    h = value_history(tmp_path, 3, {"v": 1})
    safe = to_safe_profile(shape.profile(value_table(0, {}), name="feed")).to_json()
    LocalRegistry(h.registry).commit("feed", safe, {"business_date": h.dates[0]})
    code, _, err = run(
        capsys,
        "bisect",
        str(h.registry),
        "feed",
        "--good",
        ref(h, 0),
        "--bad",
        ref(h, 2),
        "--no-project",
    )
    assert code == 2 and "--allow-raw" in err and "share-safe" in err


def test_bisect_exit_2_for_unknown_input(step_history: History, tmp_path: Path, capsys: Any):
    h = step_history
    for argv in (
        ["bisect", str(h.registry), "nope", "--good", "a", "--bad", "b"],
        ["bisect", str(tmp_path / "missing"), "orders", "--good", "a", "--bad", "b"],
        ["bisect", str(h.registry), "orders", "--good", "zzz", "--bad", "latest"],
    ):
        code, _, err = run(capsys, *argv, "--no-project")
        assert code == 2 and err.startswith("shape: error:")
    assert not (tmp_path / "missing").exists()


def test_bisect_flags_verify_all_and_coarse(tmp_path: Path, capsys: Any):
    h = value_history(tmp_path, 60, {"v": 37})
    base = [
        "bisect",
        str(h.registry),
        "feed",
        "--good",
        ref(h, 0),
        "--bad",
        ref(h, 59),
        "--json",
        "--no-project",
    ]
    plain = json.loads(run(capsys, *base)[1])
    every = json.loads(run(capsys, *base, "--verify-all")[1])
    coarse = json.loads(run(capsys, *base, "--coarse", "week")[1])
    assert every["mode"] == "verify-all" and coarse["mode"] == "coarse-week"
    assert plain["first_bad"] == every["first_bad"] == coarse["first_bad"]
    assert every["evaluated"] == 59
    code, _, err = run(capsys, *base, "--coarse", "week", "--verify-all")
    assert code == 2 and "--verify-all" in err


def test_bisect_reads_shape_yml_and_flags_override(tmp_path: Path, capsys: Any):
    h = value_history(tmp_path, 30, {"v": 10, "w": 20})
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  feed:\n    path: data\n"
        "    ignore: [v]\n"
        f"    baseline:\n      kind: previous_run\n      registry: {h.registry}\n"
    )
    base = ["bisect", str(h.registry), "feed", "--good", ref(h, 0), "--bad", ref(h, 29), "--json"]
    from_project = json.loads(run(capsys, *base, "--project", str(tmp_path / "shape.yml"))[1])
    assert from_project["first_bad"]["business_date"] == h.dates[20]
    assert from_project["test"]["source"] == "feed"
    flagged = json.loads(
        run(capsys, *base, "--project", str(tmp_path / "shape.yml"), "--ignore", "w")[1]
    )
    assert flagged["first_bad"]["business_date"] == h.dates[10]
    code, _, err = run(capsys, *base, "--project", str(tmp_path / "shape.yml"), "--source", "zzz")
    assert code == 2 and "no source 'zzz'" in err


# ---- shape bisect layers ------------------------------------------------------------------------


def layers_argv(project: Path, layers: str = "raw,clean,pub", *extra: str) -> list[str]:
    return [
        "bisect",
        "layers",
        "--layers",
        layers,
        "--good-date",
        "2026-03-01",
        "--bad-date",
        "2026-03-03",
        "--project",
        str(project),
        *extra,
    ]


def test_layers_exit_codes(tmp_path: Path, capsys: Any):
    step = [total_step(2)]
    found = layered(tmp_path / "a", {"raw": {}, "clean": {"events": step}, "pub": {"events": step}})
    code, out, _ = run(capsys, *layers_argv(found))
    assert code == 0
    assert "the change first appears in layer clean" in out and "persists in: pub" in out
    none = layered(tmp_path / "b", {"raw": {}, "clean": {}, "pub": {}})
    code, out, _ = run(capsys, *layers_argv(none))
    assert code == 1 and "no layer shows a change" in out
    code, _, err = run(capsys, *layers_argv(found, "raw,nope"))
    assert code == 2 and "no source 'nope'" in err
    code, _, err = run(
        capsys,
        "bisect",
        "layers",
        "--layers",
        "raw",
        "--good-date",
        "2026-01-01",
        "--bad-date",
        "2026-03-03",
        "--project",
        str(found),
    )
    assert code == 2 and "no version" in err


def test_layers_json_equals_the_api_and_map_works(tmp_path: Path, capsys: Any):
    step = [total_step(2)]
    rename = {"total": "total_usd"}
    project = layered(
        tmp_path,
        {
            "raw": {},
            "clean": {"events": step, "rename": rename},
            "pub": {"events": step, "rename": rename},
        },
    )
    code, out, _ = run(
        capsys,
        *layers_argv(
            project,
            "raw,clean,pub",
            "--column",
            "total",
            "--map",
            "clean.total_usd=total",
            "--map",
            "pub.total_usd=total",
            "--json",
        ),
    )
    assert code == 0
    api = bisect_layers(
        ["raw", "clean", "pub"],
        good_date="2026-03-01",
        bad_date="2026-03-03",
        column="total",
        mapping={"clean.total_usd": "total", "pub.total_usd": "total"},
        project=project,
    ).to_dict()
    assert json.loads(out) == api
    assert api["first_layer"] == "clean" and api["persists"] == ["pub"]
    code, _, err = run(capsys, *layers_argv(project, "raw,clean", "--map", "nonsense"))
    assert code == 2 and "--map" in err


def test_layers_help_is_reachable(capsys: Any):
    with pytest.raises(SystemExit) as e:
        main(["bisect", "layers", "--help"])
    assert e.value.code == 0
    assert "Find the layer of a pipeline" in capsys.readouterr().out


# ---- shape timelapse ----------------------------------------------------------------------------


def test_timelapse_json_to_stdout_equals_the_api(amount_history: History, capsys: Any):
    h = amount_history
    code, out, _ = run(
        capsys, "timelapse", str(h.registry), "orders", "--column", "amount", "--no-project"
    )
    assert code == 0
    api = timelapse(h.registry, "orders", column="amount").to_dict()
    assert json.loads(out) == api
    assert api["change_points"] == [h.event["start"]]


def test_timelapse_text_format(amount_history: History, capsys: Any):
    h = amount_history
    code, out, _ = run(
        capsys,
        "timelapse",
        str(h.registry),
        "orders",
        "--column",
        "amount",
        "--format",
        "text",
        "--no-project",
        "--since",
        "2026-03-10",
    )
    assert code == 0
    assert out.startswith("orders.amount  2026-03-10 .. 2026-03-31")
    assert "mean" in out and "change" in out and "2026-03-15" in out


def test_timelapse_writes_json_and_html(amount_history: History, tmp_path: Path, capsys: Any):
    h = amount_history
    base = ["timelapse", str(h.registry), "orders", "--column", "amount", "--no-project"]
    code, out, _ = run(capsys, *base, "-o", str(tmp_path / "t.json"))
    assert code == 0 and json.loads(out)["written"] == str(tmp_path / "t.json")
    doc = json.loads((tmp_path / "t.json").read_text())
    assert doc == timelapse(h.registry, "orders", column="amount").to_dict()
    code, out, _ = run(capsys, *base, "-o", str(tmp_path / "t.html"), "--window", "week")
    assert code == 0 and json.loads(out)["frames"] == 6
    page = (tmp_path / "t.html").read_text()
    assert page.startswith("<!doctype html>") and "shape-timelapse-data" in page
    code, _, err = run(capsys, *base, "-o", str(tmp_path / "t.png"))
    assert code == 2 and ".json or .html" in err
    code, _, err = run(capsys, *base, "--window", "day", "--since", "nope")
    assert code == 2 and "YYYY-MM-DD" in err


def test_timelapse_unknown_column_and_name(amount_history: History, capsys: Any):
    h = amount_history
    code, _, err = run(
        capsys, "timelapse", str(h.registry), "orders", "--column", "zzz", "--no-project"
    )
    assert code == 2 and "zzz" in err
    code, _, err = run(
        capsys, "timelapse", str(h.registry), "nope", "--column", "amount", "--no-project"
    )
    assert code == 2 and "no versions of 'nope'" in err
