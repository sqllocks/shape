"""``shape behave run PRIMITIVE --params FILE.json``: the parameters document, exit codes,
the manifest, and byte-identical output across runs and across a checkpoint resume."""

import json
import shutil
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from shape_behavior.cli import BehaveCommand
from shape_behavior.primitives import PARAMS_FORMAT, PARAMS_VERSION, PRIMITIVES, build

FIXTURES = Path(__file__).parent / "fixtures"
RUN = ("--population", "60", "--years", "1.5", "--seed", "4")


def _main(*argv: str) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="shape behave")
    cmd = BehaveCommand()
    cmd.configure(parser)
    return cmd.run(parser.parse_args(list(argv)))


def _params(tmp_path, **doc):
    base = {"format": PARAMS_FORMAT, "version": 1, "primitive": "telemetry_series", "params": {}}
    path = tmp_path / "p.json"
    path.write_text(json.dumps({**base, **doc}), encoding="utf-8")
    return str(path)


def _manifest(out):
    return json.loads((Path(out) / "run.json").read_text(encoding="utf-8"))


def test_params_file_builds_the_primitive_and_run_json_records_the_parameters(tmp_path):
    out = tmp_path / "o"
    params = _params(tmp_path, params={"interval": "12 hours", "level": 3, "missing_rate": 0})
    assert _main("run", "telemetry_series", "--params", params, *RUN, "-o", str(out)) == 0
    (mod,) = _manifest(out)["modules"]
    assert mod["name"] == "telemetry_series" and mod["primitive"] == "telemetry_series"
    assert mod["parameters"]["interval"] == "12 hours" and mod["parameters"]["level"] == 3
    assert mod["parameters"]["noise"] == 0.5  # a default, filled in
    events = pq.read_table(next((out / "events").glob("part-0000.parquet")))
    assert set(events.column("kind").to_pylist()) == {"reading"}
    # 1.5 years from 2020-01-01 is 548.625 days: 12-hour readings, the first at the start
    assert _manifest(out)["events"] == 60 * (int(548.625 * 2) + 1)


def test_a_primitive_named_without_params_runs_with_defaults_and_records_them(tmp_path):
    out = tmp_path / "o"
    assert _main("run", "entity_lifecycle", *RUN, "-o", str(out)) == 0
    (mod,) = _manifest(out)["modules"]
    assert mod["parameters"] == PRIMITIVES["entity_lifecycle"]().doc["parameters"]


def test_params_apply_to_the_named_primitive_among_several_modules(tmp_path):
    out = tmp_path / "o"
    params = _params(tmp_path, primitive="file_arrival", params={"schedule": "weekly"})
    assert (
        _main("run", "subscription", "file_arrival", "--params", params, *RUN, "-o", str(out)) == 0
    )
    mods = {m["name"]: m for m in _manifest(out)["modules"]}
    assert mods["file_arrival"]["parameters"]["schedule"] == "weekly"
    assert "parameters" not in mods["subscription"]


@pytest.mark.parametrize(
    ("doc", "key"),
    [
        ({"format": "shape-params"}, "format"),
        ({"format": None}, "format"),
        ({"version": 2}, "version"),
        ({"version": 99}, "version"),
        ({"version": 0}, "version"),
        ({"version": "1"}, "version"),
        ({"version": True}, "version"),
        ({"version": 1.0}, "version"),
        ({"primitive": "nope"}, "primitive"),
        ({"primitive": 5}, "primitive"),
        ({"params": {"speed": 3}}, "speed"),
        ({"params": [1]}, "params"),
        ({"extra": 1}, "extra"),
    ],
)
def test_bad_params_documents_exit_2_naming_the_key(tmp_path, capsys, doc, key):
    out = tmp_path / "o"
    assert (
        _main("run", "telemetry_series", "--params", _params(tmp_path, **doc), *RUN, "-o", str(out))
        == 2
    )
    err = capsys.readouterr().err
    assert repr(key) in err or f"'{key}'" in err or key in err
    assert key in err
    assert not (out / "events").exists() or not list((out / "events").iterdir())


def test_newer_version_message_says_what_to_do(tmp_path, capsys):
    assert (
        _main(
            "run",
            "telemetry_series",
            "--params",
            _params(tmp_path, version=2),
            *RUN,
            "-o",
            str(tmp_path / "o"),
        )
        == 2
    )
    err = capsys.readouterr().err
    assert "version 2 is newer than 1" in err and "upgrade" in err


def test_missing_keys_and_unreadable_files_exit_2(tmp_path, capsys):
    for doc, key in (({"format": PARAMS_FORMAT, "version": 1, "params": {}}, "primitive"),
                     ({"primitive": "x"}, "format"), ([], "document")):  # fmt: skip
        p = tmp_path / "q.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        assert (
            _main("run", "telemetry_series", "--params", str(p), *RUN, "-o", str(tmp_path / "o"))
            == 2
        )
        assert key in capsys.readouterr().err
    p.write_text("{not json", encoding="utf-8")
    assert (
        _main("run", "telemetry_series", "--params", str(p), *RUN, "-o", str(tmp_path / "o")) == 2
    )
    assert (
        _main(
            "run",
            "telemetry_series",
            "--params",
            str(tmp_path / "gone.json"),
            *RUN,
            "-o",
            str(tmp_path / "o"),
        )
        == 2
    )


def test_params_primitive_must_be_named_on_the_command_line(tmp_path, capsys):
    params = _params(tmp_path, primitive="file_arrival")
    assert (
        _main("run", "telemetry_series", "--params", params, *RUN, "-o", str(tmp_path / "o")) == 2
    )
    assert "file_arrival" in capsys.readouterr().err


def test_invalid_parameter_value_is_an_invalid_module_exit_1(tmp_path, capsys):
    params = _params(tmp_path, params={"interval": "0 days"})
    assert (
        _main("run", "telemetry_series", "--params", params, *RUN, "-o", str(tmp_path / "o")) == 1
    )
    assert "interval must be a positive duration, got '0 days'" in capsys.readouterr().err


# -- persisted format: shape-behavior-params, version 1 ----------------------------------------


def test_params_v1_fixture_still_loads(tmp_path):
    """The frozen version-1 document keeps loading and keeps meaning the same thing."""
    doc = json.loads((FIXTURES / "params_v1.json").read_text(encoding="utf-8"))
    assert doc["format"] == "shape-behavior-params" and doc["version"] == 1 == PARAMS_VERSION
    out = tmp_path / "o"
    assert (
        _main(
            "run",
            "telemetry_series",
            "--params",
            str(FIXTURES / "params_v1.json"),
            *RUN,
            "-o",
            str(out),
        )
        == 0
    )
    assert _manifest(out)["modules"][0]["parameters"] == {**doc["params"]}
    assert build(doc["primitive"], doc["params"]).doc["parameters"] == doc["params"]


def test_params_document_round_trips_every_primitive(tmp_path):
    for name in PRIMITIVES:
        params = tmp_path / f"{name}.json"
        defaults = PRIMITIVES[name]().doc["parameters"]
        params.write_text(json.dumps({"format": PARAMS_FORMAT, "version": 1, "primitive": name,
                                      "params": defaults}), encoding="utf-8")  # fmt: skip
        out = tmp_path / name
        assert (
            _main(
                "run",
                name,
                "--params",
                str(params),
                "--population",
                "20",
                "--years",
                "1",
                "-o",
                str(out),
            )
            == 0
        )
        assert (
            _manifest(out)["modules"][0]["parameters"] == defaults
        )  # the recorded form is accepted


# -- byte-identical output ----------------------------------------------------------------------

CASES = {
    "event_sequence": {},
    "telemetry_series": {"interval": "12 hours"},
    "transaction_stream": {"rate": 30},
    "file_arrival": {"schedule": "daily"},
    "entity_lifecycle": {"update_rate": 12},
}


def _run_cli(tmp_path, name, params, label, extra=()):
    out = tmp_path / label
    p = tmp_path / f"{name}-{label}.json"
    p.write_text(
        json.dumps({"format": PARAMS_FORMAT, "version": 1, "primitive": name, "params": params}),
        encoding="utf-8",
    )
    argv = ["--population", "80", "--years", "2.5", "--seed", "11", "--window-years", "0.5"]
    assert _main("run", name, "--params", str(p), *argv, "-o", str(out), *extra) == 0
    return out


@pytest.mark.parametrize("name", sorted(CASES))
def test_parquet_is_byte_identical_across_runs_and_checkpoint_resume(tmp_path, name):
    a = _run_cli(tmp_path, name, CASES[name], "a")
    b = _run_cli(tmp_path, name, CASES[name], "b")
    files = sorted(p.name for p in (a / "events").iterdir())
    assert len(files) == 5 and files == sorted(p.name for p in (b / "events").iterdir())
    for f in files:
        assert (a / "events" / f).read_bytes() == (b / "events" / f).read_bytes()

    # the existing resume path: stop after two windows, save a checkpoint, resume in a "new
    # process", write the rest with the same window layout the CLI uses
    from shape_behavior.behaviors import population_settings
    from shape_behavior.population import Population
    from shape_behavior.simulator import SimConfig, Simulator
    from shape_behavior.timeutil import add_years, to_us

    module = build(name, CASES[name])
    start = to_us("2020-01-01")
    pop = Population.from_dict(population_settings([module]), size=80, start="2020-01-01")
    sim = Simulator([module], pop, SimConfig(seed=11))
    c = tmp_path / "c"
    (c / "events").mkdir(parents=True)
    for part in range(2):
        pq.write_table(
            sim.run_until(add_years(start, 0.5 * (part + 1))),
            c / "events" / f"part-{part:04d}.parquet",
            compression="snappy",
        )
    sim.checkpoint().save(tmp_path / "ck.npz")
    resumed = Simulator.resume(tmp_path / "ck.npz", [build(name, CASES[name])])
    for part in range(2, 5):
        pq.write_table(
            resumed.run_until(add_years(start, 0.5 * (part + 1))),
            c / "events" / f"part-{part:04d}.parquet",
            compression="snappy",
        )
    for f in files:
        assert (a / "events" / f).read_bytes() == (c / "events" / f).read_bytes(), f


def test_different_seed_or_parameters_change_the_bytes(tmp_path):
    a = _run_cli(tmp_path, "telemetry_series", {"interval": "12 hours"}, "a")
    b = _run_cli(tmp_path, "telemetry_series", {"interval": "12 hours", "noise": 1.5}, "b")
    assert (a / "events" / "part-0000.parquet").read_bytes() != (
        b / "events" / "part-0000.parquet"
    ).read_bytes()
    shutil.rmtree(a)
