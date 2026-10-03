"""W7-04 item 8: ``verify`` with ``source`` (the memorization and utility gates) and the
``details`` of every gate."""

from __future__ import annotations

import json
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

CLASSES = {
    "people.name": "CONFIDENTIAL",
    "people.email": "SECRET",
    "people.age": "INTERNAL",
    "people.city": "PUBLIC",
}


def source_table(n: int = 200) -> pa.Table:
    rng = np.random.default_rng(1)
    return pa.table(
        {
            "name": [f"Person Number {i}" for i in range(n)],
            "email": [f"user{i}@source.example" for i in range(n)],
            "age": rng.integers(18, 90, n).tolist(),
            "income": rng.normal(50_000, 9_000, n).tolist(),
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], n).tolist(),
        }
    )


def independent_table(n: int = 200) -> pa.Table:
    rng = np.random.default_rng(99)
    return pa.table(
        {
            "name": [f"Synthetic {chr(97 + i % 26)}{i}" for i in range(n)],
            "email": [f"gen{i}@synthetic.example" for i in range(n)],
            "age": rng.integers(18, 90, n).tolist(),
            "income": rng.normal(50_000, 9_000, n).tolist(),
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], n).tolist(),
        }
    )


def copying_table(source: pa.Table, n: int = 100) -> pa.Table:
    idx = np.random.default_rng(5).choice(source.num_rows, n, replace=False)
    return source.take(pa.array(np.sort(idx)))


RAW = [
    *source_table(1200).column("name").to_pylist(),
    *source_table(1200).column("email").to_pylist(),
]


def config(**kw) -> dict:
    return {"format": "shape-verify-config", "version": 1, **kw}


def write_dirs(tmp_path, generated, cfg=None, name="people", rows=200):
    src, gen = tmp_path / "src", tmp_path / "gen"
    src.mkdir(exist_ok=True)
    gen.mkdir(exist_ok=True)
    pq.write_table(source_table(rows), src / f"{name}.parquet")
    pq.write_table(generated, gen / f"{name}.parquet")
    path = tmp_path / "verify.json"
    path.write_text(json.dumps(cfg if cfg is not None else config(classifications=CLASSES)))
    return src, gen, path


@pytest.fixture(autouse=True)
def _verify_source_is_data(monkeypatch):
    """On this branch the CLI's project lookup (W1-04) reads ``shape verify --source DATA`` (W1-03)
    as a project source name and refuses with "no shape.yml found". The bridge has no such
    clash: its ``verify`` never reads ``source`` as a source name. So that the CLI can be the
    reference for the results below, the lookup is told that ``verify`` names no project source
    (a lane-status note records the clash for the integration)."""
    from shape.cli import project as project_cli

    real = project_cli.context

    def context(a, hint=None):
        if getattr(a, "cmd", None) == "verify" and not getattr(a, "project", None):
            return None
        return real(a, hint)

    monkeypatch.setattr(project_cli, "context", context)


def cli(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


def gate(result, name):
    return next(g for g in result["gates"] if g["name"] == name)


# ---- the memorization gate -------------------------------------------------------------------


def test_an_independent_generator_passes_like_the_cli(tmp_path, api11, capsys):
    src, gen, cfg = write_dirs(tmp_path, independent_table())
    result = api11.ok("verify", path=str(gen), source=str(src), config=str(cfg))
    code, out, _ = cli(capsys, "verify", gen, "--source", src, "--config", cfg)
    assert code == 0 and result["passed"] is True
    memo = gate(result, "memorization")
    assert memo["passed"] is True and "memorization" in out
    assert memo["details"]["tables"]["people"]["reproduced_rows"] == 0
    assert memo["details"]["tables"]["people"]["exact_match_rate"] == 0.0


def test_a_copying_generator_fails_like_the_cli(tmp_path, api11, capsys):
    src, gen, cfg = write_dirs(tmp_path, copying_table(source_table()))
    result = api11.ok("verify", path=str(gen), source=str(src), config=str(cfg))
    code, _, _ = cli(capsys, "verify", gen, "--source", src, "--config", cfg)
    assert code == 1 and result["passed"] is False
    memo = gate(result, "memorization")
    assert memo["passed"] is False and memo["errors"]
    table = memo["details"]["tables"]["people"]
    assert table["reproduced_rows"] == 100 and table["exact_match_rate"] == 1.0
    assert table["restricted"] is True and table["columns"]
    assert result["row_counts"] == {"people": 100}


def test_the_response_holds_no_value_of_the_classified_columns(tmp_path, api11):
    big = source_table(1200)
    src, gen, cfg = write_dirs(tmp_path, copying_table(big, 800), rows=1200)
    for options in (None, {"include_raw_values": True}):
        response = api11.call("verify", options, path=str(gen), source=str(src), config=str(cfg))
        assert response["ok"]
        text = json.dumps(response)
        assert not [v for v in RAW if v in text]  # gate details are never values
    spilled = api11.call(
        "verify", {"max_inline_bytes": 1024}, path=str(gen), source=str(src), config=str(cfg)
    )
    gates = spilled["result"]["gates"]
    assert isinstance(gates, dict) and gates["spilled"] is True
    on_disk = open(gates["path"]).read()
    assert not [v for v in RAW if v in on_disk] and "details" in on_disk


def test_strict_and_unclassified_warnings_follow_the_cli(tmp_path, api11, capsys):
    src, gen, cfg = write_dirs(tmp_path, copying_table(source_table()), cfg=config())
    result = api11.ok("verify", path=str(gen), source=str(src))
    assert result["passed"] is True  # no classification: reported, cannot fail
    assert gate(result, "memorization")["warnings"]
    code, _, _ = cli(capsys, "verify", gen, "--source", src)
    assert code == 0
    strict = api11.ok("verify", path=str(gen), source=str(src), strict=True)
    code, _, _ = cli(capsys, "verify", gen, "--source", src, "--strict")
    assert strict["passed"] is False and code == 1


def test_min_nn_distance_is_applied(tmp_path, api11, capsys):
    cfg = config(classifications=CLASSES, memorization={"min_nn_distance": 1e9})
    src, gen, path = write_dirs(tmp_path, independent_table(), cfg=cfg)
    result = api11.ok("verify", path=str(gen), source=str(src), config=str(path))
    code, _, _ = cli(capsys, "verify", gen, "--source", src, "--config", path)
    assert result["passed"] is False and code == 1
    assert gate(result, "memorization")["details"]["tables"]["people"]["nn_distance"]["min"] >= 0


def test_a_configuration_that_needs_the_source_without_one_is_refused_with_the_clis_message(
    tmp_path, api11, capsys
):
    _, gen, cfg = write_dirs(
        tmp_path, independent_table(), cfg=config(classifications=CLASSES, memorization={})
    )
    error = api11.fail("verify", "input.invalid_value", path=str(gen), config=str(cfg))
    code, _, err = cli(capsys, "verify", gen, "--config", cfg)
    assert code == 2 and error["message"] in err
    assert (
        "memorization or utility gate" in error["message"] and "give --source" in error["message"]
    )
    cfg.write_text(json.dumps(config(utility={"table": "people", "target": "age"})))
    api11.fail("verify", "input.invalid_value", path=str(gen), config=str(cfg))


def test_a_missing_or_empty_source_is_refused(tmp_path, api11, capsys):
    src, gen, cfg = write_dirs(tmp_path, independent_table())
    api11.fail(
        "verify", "input.not_found", path=str(gen), source=str(tmp_path / "nope"), config=str(cfg)
    )
    code, _, _ = cli(capsys, "verify", gen, "--source", tmp_path / "nope", "--config", cfg)
    assert code == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    error = api11.fail(
        "verify", "input.invalid_value", path=str(gen), source=str(empty), config=str(cfg)
    )
    assert "no auto data files found" in error["message"]


def test_the_source_is_read_with_the_same_format_as_the_path(tmp_path, api11):
    import pyarrow.csv as pacsv

    src, gen = tmp_path / "src", tmp_path / "gen"
    src.mkdir()
    gen.mkdir()
    pacsv.write_csv(source_table(), src / "people.csv")
    pacsv.write_csv(copying_table(source_table()), gen / "people.csv")
    cfg = tmp_path / "v.json"
    cfg.write_text(json.dumps(config(classifications=CLASSES)))
    csv_result = api11.ok("verify", path=str(gen), source=str(src), config=str(cfg), format="csv")
    assert gate(csv_result, "memorization")["passed"] is False
    api11.fail(
        "verify", "input.invalid_value", path=str(gen), source=str(src), config=str(cfg),
        format="parquet",
    )  # fmt: skip
    # a source in another format than the path's cannot be read with `format`
    (tmp_path / "pq").mkdir()
    pq.write_table(source_table(), tmp_path / "pq" / "people.parquet")
    api11.fail(
        "verify", "input.invalid_value", path=str(gen), source=str(tmp_path / "pq"),
        config=str(cfg), format="csv",
    )  # fmt: skip


def test_without_a_source_no_comparison_gate_runs_and_the_result_is_the_1_0_result_plus_details(
    tmp_path, api11, api
):
    src, gen, cfg = write_dirs(
        tmp_path, independent_table(), cfg=config(ranges={"people.age": {"min": 0}})
    )
    one_one = api11.ok("verify", path=str(gen), config=str(cfg))
    one_zero = api.ok("verify", path=str(gen), config=str(cfg))
    assert [g["name"] for g in one_one["gates"]] == ["range_constraint"]
    stripped = {
        **one_one,
        "gates": [{k: v for k, v in g.items() if k != "details"} for g in one_one["gates"]],
    }
    assert stripped == one_zero


# ---- the utility gate ------------------------------------------------------------------------


def utility_table(n: int, seed: int, *, signal: bool = True) -> pa.Table:
    rng = np.random.default_rng(seed)
    x1, x2 = rng.normal(0, 1, n), rng.normal(0, 1, n)
    seg = rng.choice(["a", "b", "c"], n)
    score = 2.0 * x1 - 1.0 * x2 + (seg == "a") * 1.0 + rng.normal(0, 0.5, n)
    target = (score > 0).astype(int) if signal else rng.integers(0, 2, n)
    label = np.where(target == 1, "yes", "no").tolist()
    return pa.table({"x1": x1, "x2": x2, "seg": seg.tolist(), "label": label})


def utility_dirs(tmp_path, generated):
    src, gen = tmp_path / "src", tmp_path / "gen"
    src.mkdir()
    gen.mkdir()
    pq.write_table(utility_table(1200, 1), src / "t.parquet")
    pq.write_table(generated, gen / "t.parquet")
    cfg = tmp_path / "v.json"
    cfg.write_text(json.dumps(config(utility={"table": "t", "target": "label"})))
    return src, gen, cfg


def test_the_utility_gate_runs_when_the_configuration_has_a_utility_section(
    tmp_path, api11, capsys
):
    pytest.importorskip("sklearn")
    src, gen, cfg = utility_dirs(tmp_path, utility_table(1200, 2))
    result = api11.ok("verify", path=str(gen), source=str(src), config=str(cfg))
    code, _, _ = cli(capsys, "verify", gen, "--source", src, "--config", cfg)
    assert code == 0 and result["passed"] is True
    assert [g["name"] for g in result["gates"]] == ["memorization", "utility"]
    details = gate(result, "utility")["details"]
    assert details["task"] == "classification" and details["metric"] == "balanced_accuracy"
    assert details["retention"] > 0.9 and details["features"] == ["x1", "x2", "seg"]
    assert details["test_rows"] == 360 and details["real_score"] > details["synthetic_score"] * 0.5
    assert details["table"] == "t" and details["target"] == "label"


def test_a_generator_without_the_signal_fails_the_utility_gate(tmp_path, api11, capsys):
    pytest.importorskip("sklearn")
    src, gen, cfg = utility_dirs(tmp_path, utility_table(1200, 3, signal=False))
    result = api11.ok("verify", path=str(gen), source=str(src), config=str(cfg))
    code, _, _ = cli(capsys, "verify", gen, "--source", src, "--config", cfg)
    assert code == 1 and result["passed"] is False
    utility = gate(result, "utility")
    assert utility["passed"] is False and "retention" in utility["errors"][0]
    assert utility["details"]["retention"] < 0.7


# ---- details for every gate ------------------------------------------------------------------


def test_every_gate_carries_details_and_they_hold_no_data_value(tmp_path, api11):
    src, gen, cfg = write_dirs(
        tmp_path,
        independent_table(),
        cfg=config(
            ranges={"people.age": {"min": 30, "max": 40}},
            baseline={"people": {"columns": {"age": "int64"}}},
        ),
    )
    result = api11.ok("verify", path=str(gen), config=str(cfg), statistical=True)
    assert result["gates"] and all(isinstance(g["details"], dict) for g in result["gates"])
    rng = gate(result, "range_constraint")
    assert rng["passed"] is False
    assert rng["details"]["people.age"].keys() >= {"below_min", "above_max"}
    assert "actual_min" not in json.dumps(result["gates"]) and "actual_max" not in json.dumps(
        result["gates"]
    )
    ages = pq.read_table(gen / "people.parquet").column("age").to_pylist()
    assert min(ages) < 30 and max(ages) > 40  # the extremes exist in the data...
    for key in ("actual_min", "actual_max"):
        assert key not in json.dumps(rng["details"])  # ...and are not in the response


def test_safe_details_keeps_counts_and_drops_the_extremes():
    from shape.bridge.handlers.workflow11 import safe_details

    details = {"a.b": {"actual_min": 3.5, "actual_max": 9, "below_min": 2}, "n": 4}
    assert safe_details("range_constraint", details) == {"a.b": {"below_min": 2}, "n": 4}
    assert safe_details("memorization", {"nn_distance": {"min": 0.1}}) == {
        "nn_distance": {"min": 0.1}
    }
    assert safe_details("x", {"v": float("nan"), "d": {"t": (1, 2)}}) == {
        "v": None,
        "d": {"t": [1, 2]},
    }


def test_a_verify_job_has_the_same_result(tmp_path, api11):
    src, gen, cfg = write_dirs(tmp_path, copying_table(source_table()))
    args = {"path": str(gen), "source": str(src), "config": str(cfg)}
    direct = api11.ok("verify", **args)
    started = api11.ok("verify", {"async": True}, **args)
    deadline = time.time() + 60
    while time.time() < deadline:
        job = api11.ok("job_status", job_id=started["job_id"])
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded" and job["result"] == direct


def test_a_verify_job_with_a_missing_source_fails_and_says_why(tmp_path, api11):
    # `verify` has no prepare step: the job fails and says why
    src, gen, cfg = write_dirs(tmp_path, independent_table())
    started = api11.ok(
        "verify", {"async": True}, path=str(gen), source=str(tmp_path / "nope"), config=str(cfg)
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        job = api11.ok("job_status", job_id=started["job_id"])
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "failed" and job["error"]["code"] == "input.not_found"


def test_the_source_argument_is_a_read_path_since_1_1():
    from shape.bridge.registry import COMMANDS

    source = COMMANDS["verify"].args["source"]
    assert (source.since, source.path, source.type) == ("1.1", "read", "string")
    assert COMMANDS["verify"].job is True
