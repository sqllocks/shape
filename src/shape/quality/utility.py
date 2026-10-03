"""The utility gate: train on synthetic, test on real.

``UtilityGate`` splits the real table into a training part and a held-out test part, trains one
model on the real training part and one on the generated table, scores both on the held-out real
rows and fails when the generated-data model keeps less than ``min_retention`` (default 0.8) of the
real-data model's score. It needs scikit-learn (``pip install "sqllocks-shape[advanced]"``).

Fixed choices, so that two runs agree:

* The task is ``classification`` when the target is text or boolean, or an integer column with at
  most 10 distinct values; otherwise ``regression`` (``task`` overrides this).
* The metric is balanced accuracy for classification (chance is ``1 / classes`` whatever the class
  balance) and R squared for regression. ``retention = synthetic_score / real_score``; a synthetic
  R squared below 0 counts as 0.
* If the real-data model does not beat chance by 0.05 (classification) or has an R squared of 0.05
  or less (regression), the target cannot be predicted from the other columns and the gate fails
  with that message: a retention would mean nothing.
* The model is a random forest of 100 trees, seeded with ``seed`` (default 0). The real table is
  shuffled with the same seed and ``test_fraction`` (default 0.3) of it is held out. The generated
  table never sees held-out rows, and is subsampled evenly to at most the number of real training
  rows, so both models learn from the same amount of data. ``max_rows`` (default 20,000) caps the
  real training rows too.
* Features are the columns both tables share, except the target, constant columns, and text columns
  that are close to a row identifier (more than 50 distinct values, over half of the rows).
  Numbers are filled with the median of the model's own training data; text takes the code of its
  20 most frequent values, with one code for null and one for anything unseen; timestamps become
  seconds.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from .gates import GateResult, ValidationContext, ValidationGate

DEFAULT_MIN_RETENTION = 0.8
DEFAULT_TEST_FRACTION = 0.3
DEFAULT_MAX_ROWS = 20_000
MIN_REAL_ADVANTAGE = 0.05
_MIN_ROWS = 20
_TOP_CATEGORIES = 20
_FEW_VALUES = 10
_TREES = 100

Array = npt.NDArray[Any]


def _sklearn() -> Any:
    try:
        import sklearn
    except ImportError as exc:
        raise ImportError(
            'the utility gate needs scikit-learn: pip install "sqllocks-shape[advanced]"'
        ) from exc
    return sklearn


def _numeric(t: pa.DataType) -> bool:
    return bool(pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t))


def _temporal(t: pa.DataType) -> bool:
    return bool(pa.types.is_timestamp(t) or pa.types.is_date(t))


def _floats(col: pa.ChunkedArray) -> Array:
    if _temporal(col.type):
        col = col.cast(pa.timestamp("s")).cast(pa.int64())
    return np.asarray(col.cast(pa.float64()).to_numpy(zero_copy_only=False), dtype=np.float64)


def _strings(col: pa.ChunkedArray) -> list[str | None]:
    return [None if v is None else str(v) for v in col.to_pylist()]


class _Encoder:
    """Turns the feature columns into one float matrix, fitted on one table's training rows."""

    def __init__(self, table: pa.Table, columns: list[str]) -> None:
        self.columns = columns
        self.kinds: dict[str, str] = {}
        self.fill: dict[str, float] = {}
        self.codes: dict[str, dict[str, int]] = {}
        for name in columns:
            col = table.column(name)
            if _numeric(col.type) or _temporal(col.type):
                self.kinds[name] = "num"
                values = _floats(col)
                ok = ~np.isnan(values)
                self.fill[name] = float(np.median(values[ok])) if ok.any() else 0.0
            else:
                self.kinds[name] = "cat"
                counts: dict[str, int] = {}
                for v in _strings(col):
                    if v is not None:
                        counts[v] = counts.get(v, 0) + 1
                top = sorted(counts, key=lambda k: (-counts[k], k))[:_TOP_CATEGORIES]
                self.codes[name] = {k: i for i, k in enumerate(top)}

    def transform(self, table: pa.Table) -> Array:
        parts: list[Array] = []
        for name in self.columns:
            col = table.column(name)
            if self.kinds[name] == "num":
                values = _floats(col)
                parts.append(np.where(np.isnan(values), self.fill[name], values))
            else:
                codes = self.codes[name]
                null, unseen = float(len(codes)), float(len(codes) + 1)
                parts.append(
                    np.array(
                        [null if v is None else codes.get(v, unseen) for v in _strings(col)],
                        dtype=np.float64,
                    )
                )
        return np.column_stack(parts) if parts else np.empty((table.num_rows, 0))


def _features(real: pa.Table, generated: pa.Table, target: str) -> list[str]:
    out: list[str] = []
    for name in real.column_names:
        if name == target or name not in generated.column_names:
            continue
        col = real.column(name)
        distinct = pc.count_distinct(col).as_py()
        if distinct <= 1:
            continue
        if not (_numeric(col.type) or _temporal(col.type)):
            if distinct > 50 and distinct > 0.5 * len(col):
                continue
        out.append(name)
    return out


def _labelled(table: pa.Table, target: str) -> pa.Table:
    """The rows that have a target value (a null or NaN target is no label)."""
    col = table.column(target)
    keep = pc.is_valid(col)
    if pa.types.is_floating(col.type):
        keep = pc.and_(keep, pc.invert(pc.fill_null(pc.is_nan(col), False)))
    return table.filter(keep)


def _task(real: pa.Table, target: str, forced: str) -> str:
    if forced != "auto":
        return forced
    col = real.column(target)
    if not _numeric(col.type):
        return "classification"
    if pa.types.is_integer(col.type) and pc.count_distinct(col).as_py() <= _FEW_VALUES:
        return "classification"
    return "regression"


def _labels(real: pa.Table, generated: pa.Table, target: str, task: str) -> tuple[Array, Array]:
    r, g = real.column(target), generated.column(target)
    if task == "regression":
        return _floats(r), _floats(g)
    if _numeric(r.type) and _numeric(g.type):
        return _floats(r).astype(str), _floats(g).astype(str)
    return np.array(_strings(r), dtype=object).astype(str), np.array(
        _strings(g), dtype=object
    ).astype(str)


def _fit_score(
    train: tuple[Array, Array], test: tuple[Array, Array], task: str, seed: int
) -> float:
    """Fit a random forest on ``train`` (matrix, target) and score it on ``test``."""
    _sklearn()
    from sklearn.ensemble import (
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.metrics import (
        balanced_accuracy_score,
        r2_score,
    )

    if task == "classification":
        clf = RandomForestClassifier(n_estimators=_TREES, random_state=seed, n_jobs=1)
        clf.fit(*train)
        return float(balanced_accuracy_score(test[1], clf.predict(test[0])))
    reg = RandomForestRegressor(n_estimators=_TREES, random_state=seed, n_jobs=1)
    reg.fit(*train)
    return float(r2_score(test[1], reg.predict(test[0])))


def _stride(n: int, cap: int) -> Array:
    if n <= cap:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, cap).astype(np.int64))


class UtilityGate(ValidationGate):
    """Fails when a model trained on generated data keeps less than ``min_retention`` of the
    score of a model trained on real data, both tested on held-out real rows. Reads ``utility``
    (``table``, ``target``, ``task``, ``min_retention``, ``test_fraction``, ``seed``, ``max_rows``)
    from ``config`` and the real tables from ``source_tables``."""

    name = "utility"

    def check(self, context: ValidationContext) -> GateResult:
        _sklearn()
        opts: dict[str, Any] = dict(context.config.get("utility") or {})
        table, target = str(opts["table"]), str(opts["target"])
        min_retention = float(opts.get("min_retention", DEFAULT_MIN_RETENTION))
        details: dict[str, Any] = {"table": table, "target": target, "min_retention": min_retention}

        def fail(message: str) -> GateResult:
            return GateResult(self.name, False, [f"{table}: {message}"], [], details)

        generated, real = context.tables.get(table), context.source_tables.get(table)
        if generated is None or real is None:
            side = "generated data" if generated is None else "source data"
            return GateResult(
                self.name, False, [f"table '{table}' is not in the {side}"], [], details
            )
        for label, t in (("generated", generated), ("source", real)):
            if target not in t.column_names:
                return fail(f"target column '{target}' is not in the {label} table")
        seed = int(opts.get("seed", 0))
        fraction = float(opts.get("test_fraction", DEFAULT_TEST_FRACTION))
        max_rows = int(opts.get("max_rows", DEFAULT_MAX_ROWS))
        real, generated = _labelled(real, target), _labelled(generated, target)
        task = _task(real, target, str(opts.get("task", "auto")))
        details.update(task=task, metric="balanced_accuracy" if task == "classification" else "r2")
        r_labels, g_labels = _labels(real, generated, target, task)
        if (
            len(set(r_labels.tolist())) < 2
            if task == "classification"
            else float(np.nanstd(r_labels)) == 0.0
        ):
            return fail(f"target '{target}' has one value in the source table")
        features = _features(real, generated, target)
        details["features"] = features
        if not features:
            return fail("no usable feature column is shared by the generated and source tables")
        order = np.random.RandomState(seed).permutation(real.num_rows)
        n_test = int(round(real.num_rows * fraction))
        test_idx, train_idx = order[:n_test], order[n_test:][:max_rows]
        if (
            len(train_idx) < _MIN_ROWS
            or len(test_idx) < _MIN_ROWS
            or generated.num_rows < _MIN_ROWS
        ):
            return fail(
                f"too few rows for a model (need {_MIN_ROWS} or more of each: "
                f"{len(train_idx)} real training, {len(test_idx)} real test, "
                f"{generated.num_rows} generated rows)"
            )
        syn_idx = _stride(generated.num_rows, len(train_idx))
        real_train, real_test = real.take(pa.array(train_idx)), real.take(pa.array(test_idx))
        syn_train = generated.take(pa.array(syn_idx))
        y_test = r_labels[test_idx]
        real_enc, syn_enc = _Encoder(real_train, features), _Encoder(syn_train, features)
        real_score = _fit_score(
            (real_enc.transform(real_train), r_labels[train_idx]),
            (real_enc.transform(real_test), y_test),
            task,
            seed,
        )
        synthetic_score = _fit_score(
            (syn_enc.transform(syn_train), g_labels[syn_idx]),
            (syn_enc.transform(real_test), y_test),
            task,
            seed,
        )
        details.update(
            real_score=real_score,
            synthetic_score=synthetic_score,
            train_rows_real=len(train_idx),
            train_rows_synthetic=len(syn_idx),
            test_rows=len(test_idx),
        )
        if task == "classification":
            chance = 1.0 / len(set(r_labels.tolist()))
            floor, label = (
                chance + MIN_REAL_ADVANTAGE,
                f"balanced accuracy {real_score:.3f} vs chance {chance:.3f}",
            )
            unusable = real_score <= floor
        else:
            label = f"R squared {real_score:.3f}"
            unusable = real_score <= MIN_REAL_ADVANTAGE
        if unusable:
            return fail(
                f"the model trained on real data does not beat chance ({label}); "
                f"'{target}' cannot be predicted from the other columns, so retention is undefined"
            )
        retention = max(synthetic_score, 0.0) / real_score
        details["retention"] = retention
        if retention < min_retention:
            return fail(
                f"retention {retention:.3f} is below the minimum {min_retention:g} "
                f"(real {real_score:.3f}, generated {synthetic_score:.3f} on {len(test_idx)} "
                "held-out real rows)"
            )
        return GateResult(self.name, True, [], [], details)
