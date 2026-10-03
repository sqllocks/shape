"""W1-03 (#58): the memorization gate - exact-match rate and nearest-neighbour distance between
generated rows and the source rows, as a `shape verify` gate."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.fidelity import bootstrap_table
from shape.quality import (
    MemorizationGate,
    ValidationContext,
    VerifyConfig,
    VerifyConfigError,
    VerifyRunner,
)

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
            "income": (rng.normal(50_000, 9_000, n)).tolist(),
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], n).tolist(),
        }
    )


def independent_table(n: int = 200) -> pa.Table:
    """A generator that never saw the source."""
    rng = np.random.default_rng(99)
    return pa.table(
        {
            "name": [f"Synthetic {chr(97 + i % 26)}{i}" for i in range(n)],
            "email": [f"gen{i}@synthetic.example" for i in range(n)],
            "age": rng.integers(18, 90, n).tolist(),
            "income": (rng.normal(50_000, 9_000, n)).tolist(),
            "city": rng.choice(["Oslo", "Lima", "Kyiv"], n).tolist(),
        }
    )


def copying_table(source: pa.Table, n: int = 100) -> pa.Table:
    """The negative control: a generator that copies source rows."""
    idx = np.random.default_rng(5).choice(source.num_rows, n, replace=False)
    return source.take(pa.array(np.sort(idx)))


def check(generated, source=None, classes=CLASSES, **options):
    ctx = ValidationContext(
        tables={"people": generated},
        source_tables={"people": source if source is not None else source_table()},
        config={"classifications": classes, "memorization": options},
    )
    return MemorizationGate().check(ctx)


def test_a_generator_that_copies_source_rows_fails_the_gate():
    src = source_table()
    result = check(copying_table(src), src)
    assert result.gate_name == "memorization"
    assert not result.passed
    assert result.details["tables"]["people"]["reproduced_rows"] == 100
    assert result.details["tables"]["people"]["exact_match_rate"] == 1.0
    assert "people" in result.errors[0] and "name" in result.errors[0]


def test_the_report_names_the_rows_and_never_prints_a_value():
    src = source_table()
    gen = copying_table(src, 10)
    result = check(gen, src)
    blob = json.dumps(result.details) + " ".join(result.errors + result.warnings)
    for value in gen.column("name").to_pylist() + gen.column("email").to_pylist():
        assert value not in blob
    assert result.details["tables"]["people"]["reproduced_row_indices"] == list(range(10))
    assert result.details["tables"]["people"]["columns"] == ["email", "name"]


def test_the_bootstrap_documented_row_reproduction_is_caught_at_confidential():
    src = source_table()
    boot, _ = bootstrap_table(src, n_rows=150, seed=3)  # numbers are jittered, text is copied
    assert not check(boot, src).passed
    assert not check(boot, src, {"people.name": "CONFIDENTIAL"}).passed
    assert not check(boot, src, {"people.name": "TOP_SECRET"}).passed


def test_the_bootstrap_strategy_is_caught_at_confidential():
    from shape.generation import reference
    from shape.generation.engine import Engine
    from shape.generation.schema import GenSchema

    src = source_table(120)

    def col(name, **gen):
        return {"name": name, "type": "string", "generator": {"strategy": "bootstrap", **gen}}

    doc = {
        "schema_version": 1,
        "model": {"name": "b", "seed": 7},
        "tables": {
            "people": {
                "name": "people",
                "primary_key": [],
                "columns": {
                    "name": col("name", dataset="ppl", field="name"),
                    "email": col("email", dataset="ppl", field="email"),
                },
            }
        },
        "relationships": [],
        "generation": {"scale": "small", "scales": {"small": {"people": 80}}},
    }
    reference.register_dataset("ppl", src)
    try:
        out = Engine(GenSchema.from_dict(doc), seed=7).generate_table("people")
    finally:
        reference.unregister_dataset("ppl")
    assert not check(out, src, {"people.name": "CONFIDENTIAL"}).passed


def test_an_independent_generator_passes():
    result = check(independent_table())
    assert result.passed, result.errors
    assert result.details["tables"]["people"]["reproduced_rows"] == 0
    assert result.details["tables"]["people"]["exact_match_rate"] == 0.0


@pytest.mark.parametrize(
    ("level", "fails"),
    [
        ("PUBLIC", False),
        ("INTERNAL", False),
        ("CONFIDENTIAL", True),
        ("SECRET", True),
        ("TOP_SECRET", True),
    ],
)
def test_the_default_policy_fails_from_confidential_upwards(level, fails):
    src = source_table()
    result = check(copying_table(src), src, {"people.name": level})
    assert result.passed is (not fails)


def test_pii_and_sensitive_rank_as_confidential():
    src = source_table()
    for alias in ("PII", "sensitive"):
        assert not check(copying_table(src), src, {"people.name": alias}).passed


def test_fail_at_lowers_or_raises_the_threshold():
    src = source_table()
    copy = copying_table(src)
    assert not check(
        copy, src, {"people.age": "INTERNAL", "people.name": "INTERNAL"}, fail_at="INTERNAL"
    ).passed
    assert check(copy, src, {"people.name": "CONFIDENTIAL"}, fail_at="SECRET").passed


def test_copies_on_public_columns_alone_do_not_fail_but_are_reported():
    src = source_table()
    result = check(copying_table(src), src, {"people.city": "PUBLIC"})
    assert result.passed
    assert result.details["tables"]["people"]["exact_match_rate"] > 0.9  # whole rows, informational
    assert any("CONFIDENTIAL" in w for w in result.warnings)  # nothing restricted: say so


def test_a_value_shared_by_several_source_rows_is_not_a_reproduction():
    src = pa.table({"name": ["A", "A", "B", "C"], "x": [1, 2, 3, 4]})
    gen = pa.table({"name": ["A", "A", "A"], "x": [9, 9, 9]})
    result = check(gen, src, {"people.name": "CONFIDENTIAL"})
    assert result.passed
    assert result.details["tables"]["people"]["reproduced_rows"] == 0
    gen2 = pa.table({"name": ["B", "Z"], "x": [9, 9]})
    assert not check(gen2, src, {"people.name": "CONFIDENTIAL"}).passed


def test_nulls_never_match():
    src = pa.table({"name": [None, None, "X"], "x": [1, 2, 3]})
    gen = pa.table({"name": [None, None], "x": [1, 2]})
    assert check(gen, src, {"people.name": "CONFIDENTIAL"}).passed


def test_numeric_noise_does_not_hide_a_copied_text_column_but_a_numeric_only_key_is_exact():
    src = pa.table({"salary": [10.0, 20.0, 30.0]})
    exact = pa.table({"salary": [20.0]})
    noisy = pa.table({"salary": [20.5]})
    assert not check(exact, src, {"people.salary": "SECRET"}).passed
    assert check(noisy, src, {"people.salary": "SECRET"}).passed


def test_nearest_neighbour_distance_is_zero_for_a_copy_and_positive_for_an_independent_table():
    src = source_table()
    near = check(copying_table(src), src)
    far = check(independent_table(), src)
    assert near.details["tables"]["people"]["nn_distance"]["min"] == 0.0
    assert far.details["tables"]["people"]["nn_distance"]["min"] > 0.0
    assert far.details["tables"]["people"]["nn_distance"]["columns"] == ["age", "income"]


def test_min_nn_distance_is_an_opt_in_second_criterion():
    src = source_table()
    gen = independent_table()
    floor = check(gen, src).details["tables"]["people"]["nn_distance"]["min"]
    assert check(gen, src, min_nn_distance=floor / 2).passed
    result = check(gen, src, min_nn_distance=floor * 2)
    assert not result.passed and "nearest" in result.errors[0]


def test_jittered_copies_have_a_small_nn_distance():
    src = source_table()
    boot, _ = bootstrap_table(src, n_rows=100, seed=3)
    d = check(boot, src).details["tables"]["people"]["nn_distance"]
    assert d["median"] < 0.1


def test_a_table_absent_from_the_source_or_without_shared_columns_is_a_warning():
    ctx = ValidationContext(
        tables={"people": independent_table(), "other": pa.table({"a": [1]})},
        source_tables={"people": source_table(), "zzz": pa.table({"q": [1]})},
        config={"classifications": CLASSES},
    )
    result = MemorizationGate().check(ctx)
    assert result.passed
    assert any("other" in w for w in result.warnings)


def test_the_gate_is_deterministic():
    src = source_table()
    a = check(copying_table(src), src)
    b = check(copying_table(src), src)
    assert a.details == b.details and a.errors == b.errors


def test_row_cap_keeps_exact_matching_over_every_row():
    src = source_table(300)
    gen = pa.concat_tables([independent_table(300), copying_table(src, 1)])
    result = check(gen, src, max_rows=50)  # the cap only limits the nearest-neighbour search
    assert not result.passed
    assert result.details["tables"]["people"]["reproduced_row_indices"] == [300]


def test_verify_runner_runs_the_gate_when_a_source_is_given():
    src = source_table()
    cfg = VerifyConfig.from_dict(
        {"format": "shape-verify-config", "version": 1, "classifications": CLASSES}
    )
    runner = VerifyRunner(None, False, "gen", None, cfg, None, None, source={"people": src})
    bad = runner.run({"people": copying_table(src)})
    assert [g.gate_name for g in bad.gate_results] == ["memorization"] and not bad.passed
    assert runner.run({"people": independent_table()}).passed


# ---- configuration ---------------------------------------------------------------------------


def doc(**kw):
    return {"format": "shape-verify-config", "version": 1, **kw}


@pytest.mark.parametrize(
    "bad",
    [
        {"classifications": []},
        {"classifications": {"people": "SECRET"}},
        {"classifications": {"people.name": "NOPE"}},
        {"memorization": {"fail_at": "NOPE"}},
        {"memorization": {"min_nn_distance": -1}},
        {"memorization": {"max_rows": 0}},
        {"memorization": {"bogus": 1}},
        {"memorization": 3},
    ],
)
def test_a_bad_memorization_configuration_is_refused(bad):
    with pytest.raises(VerifyConfigError):
        VerifyConfig.from_dict(doc(**bad))


# ---- the command -----------------------------------------------------------------------------


def write_dirs(tmp_path, generated):
    src = tmp_path / "src"
    gen = tmp_path / "gen"
    src.mkdir()
    gen.mkdir()
    pq.write_table(source_table(), src / "people.parquet")
    pq.write_table(generated, gen / "people.parquet")
    cfg = tmp_path / "verify.json"
    cfg.write_text(json.dumps(doc(classifications=CLASSES)))
    return src, gen, cfg


def test_cli_verify_source_fails_a_copying_generator_with_exit_1(tmp_path, capsys):
    src_t = source_table()
    src, gen, cfg = write_dirs(tmp_path, copying_table(src_t))
    code = main(
        [
            "verify",
            str(gen),
            "--source",
            str(src),
            "--config",
            str(cfg),
            "-o",
            str(tmp_path / "r.json"),
        ]
    )
    out = capsys.readouterr()
    assert code == 1
    assert "memorization" in out.out and "FAIL" in out.out
    assert "Person Number" not in out.out + out.err
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["source_path"] == str(src)
    gate = next(g for g in report["gates"] if g["gate"] == "memorization")
    assert not gate["passed"] and "Person Number" not in json.dumps(report)


def test_cli_verify_source_passes_an_independent_generator(tmp_path, capsys):
    src, gen, cfg = write_dirs(tmp_path, independent_table())
    assert main(["verify", str(gen), "--source", str(src), "--config", str(cfg)]) == 0
    assert "memorization" in capsys.readouterr().out


def test_cli_verify_source_without_classifications_warns_and_strict_fails(tmp_path, capsys):
    src, gen, _ = write_dirs(tmp_path, copying_table(source_table()))
    assert main(["verify", str(gen), "--source", str(src)]) == 0
    assert "classif" in capsys.readouterr().out
    assert main(["verify", str(gen), "--source", str(src), "--strict"]) == 1


def test_cli_verify_missing_source_is_exit_2(tmp_path, capsys):
    _, gen, cfg = write_dirs(tmp_path, independent_table())
    assert main(["verify", str(gen), "--source", str(tmp_path / "nope"), "--config", str(cfg)]) == 2


def test_cli_verify_config_asking_for_a_gate_without_source_is_exit_2(tmp_path, capsys):
    _, gen, _ = write_dirs(tmp_path, independent_table())
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps(doc(classifications=CLASSES, memorization={})))
    assert main(["verify", str(gen), "--config", str(cfg)]) == 2
    assert "--source" in capsys.readouterr().err
