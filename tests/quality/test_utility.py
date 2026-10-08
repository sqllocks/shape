"""W1-03 (#58): the utility gate - train on synthetic, test on held-out real, compare with a model
trained on real data, fail below a minimum retention."""

from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("sklearn")

from shape.cli.main import main  # noqa: E402
from shape.quality import (  # noqa: E402
    UtilityGate,
    ValidationContext,
    VerifyConfig,
    VerifyConfigError,
    VerifyRunner,
)


def make(n: int, seed: int, *, signal: bool = True, task: str = "classification") -> pa.Table:
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    seg = rng.choice(["a", "b", "c"], n)
    if task == "classification":
        score = 2.0 * x1 - 1.0 * x2 + (seg == "a") * 1.0 + rng.normal(0, 0.5, n)
        target = (score > 0).astype(int)
        if not signal:
            target = rng.integers(0, 2, n)
        label = np.where(target == 1, "yes", "no").tolist()
        return pa.table({"x1": x1, "x2": x2, "seg": seg.tolist(), "label": label})
    y = 3.0 * x1 - 2.0 * x2 + rng.normal(0, 0.5, n)
    if not signal:
        y = rng.normal(0, 3, n)
    return pa.table({"x1": x1, "x2": x2, "seg": seg.tolist(), "y": y})


def run(generated, real, **options):
    options = {"table": "t", "target": "label", **options}
    ctx = ValidationContext(
        tables={"t": generated}, source_tables={"t": real}, config={"utility": options}
    )
    return UtilityGate().check(ctx)


REAL = make(1200, 1)


def test_synthetic_data_that_keeps_the_signal_passes_with_high_retention():
    result = run(make(1200, 2), REAL)
    assert result.gate_name == "utility"
    assert result.passed, result.errors
    d = result.details
    assert d["task"] == "classification" and d["metric"] == "balanced_accuracy"
    assert d["real_score"] > 0.85 and d["retention"] > 0.9
    assert d["retention"] == pytest.approx(d["synthetic_score"] / d["real_score"])
    assert d["test_rows"] == 360 and d["features"] == ["x1", "x2", "seg"]


def test_the_negative_control_synthetic_data_without_the_signal_fails():
    result = run(make(1200, 3, signal=False), REAL)
    assert not result.passed
    assert result.details["retention"] < 0.7
    assert "retention" in result.errors[0]


def test_min_retention_is_enforced_at_the_boundary():
    gen = make(1200, 2)
    retention = run(gen, REAL).details["retention"]
    assert run(gen, REAL, min_retention=min(1.0, retention - 1e-9)).passed
    assert (
        not run(gen, REAL, min_retention=min(1.0, retention + 1e-6)).passed
        if retention < 1
        else True
    )


def test_the_default_minimum_retention_is_documented_and_applied():
    result = run(make(1200, 3, signal=False), REAL)
    assert result.details["min_retention"] == 0.8


def test_regression_uses_r_squared():
    real = make(1200, 1, task="regression")
    good = run(make(1200, 2, task="regression"), real, target="y")
    assert good.passed, good.errors
    assert good.details["task"] == "regression" and good.details["metric"] == "r2"
    bad = run(make(1200, 3, signal=False, task="regression"), real, target="y")
    assert not bad.passed and bad.details["synthetic_score"] < 0.5


def test_the_task_can_be_forced_and_is_detected_from_the_target():
    int_target = REAL.set_column(
        3, "label", pa.array((np.array(REAL["label"].to_pylist()) == "yes").astype(int))
    )
    gen = make(1200, 2)
    gen = gen.set_column(
        3, "label", pa.array((np.array(gen["label"].to_pylist()) == "yes").astype(int))
    )
    assert run(gen, int_target).details["task"] == "classification"  # few integer values
    assert run(gen, int_target, task="regression").details["task"] == "regression"


def test_a_real_model_that_does_not_beat_chance_cannot_judge_utility():
    noise = make(1200, 1, signal=False)
    result = run(make(1200, 2, signal=False), noise)
    assert not result.passed
    assert "chance" in result.errors[0]


def test_regression_against_a_real_model_with_no_signal_is_an_error():
    noise = make(1200, 1, signal=False, task="regression")
    result = run(make(1200, 2, signal=False, task="regression"), noise, target="y")
    assert not result.passed and "R" in result.errors[0]


def test_the_gate_is_deterministic_and_seeded():
    a = run(make(1200, 2), REAL).details
    b = run(make(1200, 2), REAL).details
    assert a == b
    c = run(make(1200, 2), REAL, seed=5).details
    assert c["real_score"] != a["real_score"] or c["synthetic_score"] != a["synthetic_score"]


def test_rows_without_a_target_value_are_dropped_not_read_as_a_class():
    labels = REAL["label"].to_pylist()
    holes = REAL.set_column(
        3, "label", pa.array([None if i % 9 == 0 else v for i, v in enumerate(labels)])
    )
    result = run(make(1200, 2), holes)
    assert result.passed, result.errors
    assert result.details["test_rows"] == round(0.3 * (1200 - 134))  # 134 null labels dropped
    reg = make(1200, 1, task="regression")
    y = reg["y"].to_numpy().copy()
    y[::10] = np.nan
    nan_reg = reg.set_column(3, "y", pa.array(y))
    assert run(make(1200, 2, task="regression"), nan_reg, target="y").passed


def test_timestamp_features_are_used_as_numbers():
    base = dt.datetime(2020, 1, 1)
    t = [base + dt.timedelta(days=i) for i in range(1200)]
    real = REAL.append_column("when", pa.array(t, pa.timestamp("us")))
    gen = make(1200, 2).append_column("when", pa.array(t, pa.timestamp("us")))
    assert "when" in run(gen, real).details["features"]


def test_identifier_like_text_columns_are_not_features():
    real = REAL.append_column("uid", pa.array([f"id{i}" for i in range(REAL.num_rows)]))
    gen = make(1200, 2).append_column("uid", pa.array([f"g{i}" for i in range(1200)]))
    assert "uid" not in run(gen, real).details["features"]


def test_nulls_and_unseen_categories_do_not_break_the_models():
    real = REAL.set_column(
        0,
        "x1",
        pa.array([None if i % 17 == 0 else v for i, v in enumerate(REAL["x1"].to_pylist())]),
    )
    gen = make(1200, 2)
    gen = gen.set_column(
        2,
        "seg",
        pa.array(["zzz" if i % 11 == 0 else v for i, v in enumerate(gen["seg"].to_pylist())]),
    )
    assert run(gen, real).details["retention"] > 0.7


@pytest.mark.parametrize(
    ("gen", "real", "options", "needle"),
    [
        (make(100, 2).drop(["label"]), REAL, {}, "label"),
        (make(100, 2), REAL.drop(["label"]), {}, "label"),
        (make(10, 2), make(10, 1), {}, "rows"),
        (make(100, 2), pa.table({"x1": [1.0] * 100, "label": ["a"] * 100}), {}, "one value"),
    ],
)
def test_unusable_input_fails_the_gate_with_a_message(gen, real, options, needle):
    result = run(gen, real, **options)
    assert not result.passed and needle in result.errors[0]


def test_a_missing_table_fails_the_gate():
    ctx = ValidationContext(
        tables={"u": make(100, 2)},
        source_tables={"t": REAL},
        config={"utility": {"table": "t", "target": "label"}},
    )
    result = UtilityGate().check(ctx)
    assert not result.passed and "'t'" in result.errors[0]


def test_without_scikit_learn_the_gate_names_the_extra(monkeypatch):
    import shape.quality.utility as utility

    def boom():
        raise ImportError(
            'the utility gate needs scikit-learn: pip install "sqllocks-shape[advanced]"'
        )

    monkeypatch.setattr(utility, "_sklearn", boom)
    with pytest.raises(ImportError, match=r"\[advanced\]"):
        run(make(100, 2), REAL)


def test_the_held_out_real_rows_never_train_the_synthetic_model(monkeypatch):
    import shape.quality.utility as utility

    seen = []
    original = utility._fit_score

    def spy(train, test, *a, **k):
        seen.append((train, test))
        return original(train, test, *a, **k)

    monkeypatch.setattr(utility, "_fit_score", spy)
    run(make(1200, 2), REAL)
    (real_train, real_test), (syn_train, syn_test) = seen
    assert np.array_equal(real_test[1], syn_test[1])  # one held-out real set judges both models
    assert len(syn_train[0]) <= len(real_train[0])


# ---- configuration and the command -----------------------------------------------------------


def doc(**kw):
    return {"format": "shape-verify-config", "version": 1, **kw}


@pytest.mark.parametrize(
    "bad",
    [
        {"utility": []},
        {"utility": {"target": "y"}},
        {"utility": {"table": "t"}},
        {"utility": {"table": "t", "target": "y", "min_retention": 0}},
        {"utility": {"table": "t", "target": "y", "min_retention": 1.5}},
        {"utility": {"table": "t", "target": "y", "test_fraction": 1}},
        {"utility": {"table": "t", "target": "y", "task": "cluster"}},
        {"utility": {"table": "t", "target": "y", "max_rows": 0}},
        {"utility": {"table": "t", "target": "y", "seed": 1.5}},
        {"utility": {"table": "t", "target": "y", "bogus": 1}},
    ],
)
def test_a_bad_utility_configuration_is_refused(bad):
    with pytest.raises(VerifyConfigError):
        VerifyConfig.from_dict(doc(**bad))


def test_verify_runner_runs_the_utility_gate_when_configured():
    cfg = VerifyConfig.from_dict(doc(utility={"table": "t", "target": "label"}))
    runner = VerifyRunner(None, False, "g", None, cfg, None, None, source={"t": REAL})
    names = [g.gate_name for g in runner.run({"t": make(1200, 2)}).gate_results]
    assert names == ["memorization", "utility"]


def write_dirs(tmp_path, generated):
    src, gen = tmp_path / "src", tmp_path / "gen"
    src.mkdir()
    gen.mkdir()
    pq.write_table(REAL, src / "t.parquet")
    pq.write_table(generated, gen / "t.parquet")
    cfg = tmp_path / "v.json"
    cfg.write_text(json.dumps(doc(utility={"table": "t", "target": "label"})))
    return src, gen, cfg


def test_cli_utility_gate_exit_codes(tmp_path, capsys):
    src, gen, cfg = write_dirs(tmp_path, make(1200, 2))
    assert main(["verify", str(gen), "--source", str(src), "--config", str(cfg)]) == 0
    assert "utility" in capsys.readouterr().out
    tmp2 = tmp_path / "bad"
    tmp2.mkdir()
    src, gen, cfg = write_dirs(tmp2, make(1200, 3, signal=False))
    assert main(["verify", str(gen), "--source", str(src), "--config", str(cfg)]) == 1
    err = capsys.readouterr().err
    assert "utility" in err and "retention" in err
