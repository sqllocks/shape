"""Tier 1 fidelity: what the base comparison does not see.

For one table (or a reference/synthetic pair) :class:`Tier1Profiler` computes

* **mixture fits** of every numeric column (1 to ``max_gmm_components`` Gaussians, chosen by BIC);
* **conditional profiles**: per-category mean, spread, count and quartiles of the numeric columns
  for each low-cardinality categorical column;
* the **adversarial score**: how well a gradient-boosted classifier tells reference rows from
  synthetic ones (AUC 0.5 means it cannot);
* **temporal profiles** of timestamp columns (gaps, autocorrelation, best-fit gap distribution);
* **periodicity** of numeric columns (FFT, dominant period).

Mixture fits and the adversarial score need scikit-learn (``pip install
sqllocks-shape[advanced]``), and the gap distribution needs SciPy, which scikit-learn brings.
Without them those parts are left out and named in ``notes``; the rest still runs.

This is a port of the reference implementation's tier 1 (checked by the parity harness under
``benchmarks/`` in the repository). Differences, all on purpose:

* the adversarial features are the columns the two tables share *in reference order* (the
  reference takes them in the iteration order of a Python ``set``, which changes between
  processes);
* periodicity uses numpy's FFT, so it does not need SciPy;
* every part left out says why in ``notes`` (the reference leaves it out silently).
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from types import ModuleType
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from ._frame import Column, Frame, as_frame, sample_positions

INSTALL_HINT = "pip install 'sqllocks-shape[advanced]'"


def _import(name: str) -> ModuleType | None:
    try:
        return __import__(name, fromlist=["_"])
    except ImportError:
        return None


def clean(value: Any) -> Any:
    """``value`` with every NaN or infinity replaced by ``None`` (JSON has neither)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


@dataclass
class GMMFit:
    """A Gaussian mixture fit of a numeric column."""

    column: str
    n_components: int
    means: list[float]
    weights: list[float]
    stds: list[float]
    bic: float
    aic: float


@dataclass
class ConditionalProfile:
    """Statistics of ``primary_col`` for each value of ``conditioned_on``."""

    primary_col: str
    conditioned_on: str
    stats_by_value: dict[str, dict[str, float]]


@dataclass
class AdversarialResult:
    """How well a classifier separates reference rows from synthetic rows."""

    auc_roc: float
    accuracy: float
    top_features: list[tuple[str, float]]
    n_samples: int
    passed: bool

    @property
    def distinguishability_score(self) -> float:
        """0: indistinguishable, 100: perfectly distinguishable."""
        return round((self.auc_roc - 0.5) * 200, 2)


@dataclass
class TemporalProfile:
    """Gaps, autocorrelation and gap distribution of a timestamp column."""

    column: str
    mean_gap_seconds: float | None
    std_gap_seconds: float | None
    min_gap_seconds: float | None
    max_gap_seconds: float | None
    autocorrelation_lag1: float | None
    autocorrelation_lag7: float | None
    gap_distribution: str | None


@dataclass
class PeriodicityResult:
    """The strongest periods of a numeric column, by FFT power."""

    column: str
    dominant_period: float | None
    dominant_frequency: float | None
    dominant_power: float | None
    top_periods: list[tuple[float, float]]
    is_periodic: bool


@dataclass
class Tier1Profile:
    """Everything tier 1 computes for one table."""

    table_name: str
    row_count: int
    gmm_fits: dict[str, GMMFit] = field(default_factory=dict)
    conditional_profiles: list[ConditionalProfile] = field(default_factory=list)
    adversarial: AdversarialResult | None = None
    temporal_profiles: dict[str, TemporalProfile] = field(default_factory=dict)
    periodicity: dict[str, PeriodicityResult] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready (NaN and infinity become ``None``)."""
        out = asdict(self)
        if self.adversarial is not None:
            out["adversarial"]["distinguishability_score"] = (
                self.adversarial.distinguishability_score
            )
        return dict(clean(out))


class Tier1Profiler:
    """Tier 1 profiling of one table, or of a reference/synthetic pair.

    >>> profile = Tier1Profiler().profile_pair(reference, synthetic, table_name="orders")
    >>> profile.adversarial.auc_roc          # doctest: +SKIP
    """

    def __init__(
        self,
        max_gmm_components: int = 5,
        adversarial_threshold: float = 0.75,
        max_categorical_for_conditional: int = 20,
        max_rows_adversarial: int = 5000,
    ) -> None:
        self.max_gmm_components = max_gmm_components
        self.adversarial_threshold = adversarial_threshold
        self.max_categorical_for_conditional = max_categorical_for_conditional
        self.max_rows_adversarial = max_rows_adversarial

    def profile_pair(
        self,
        real: pa.Table | Frame,
        synthetic: pa.Table | Frame,
        table_name: str = "table",
    ) -> Tier1Profile:
        """Tier 1 of ``real``, plus the adversarial test of ``real`` against ``synthetic``."""
        real_f, synth_f = as_frame(real), as_frame(synthetic)
        prof = self._describe(real_f, table_name)
        prof.adversarial = self._adversarial_test(real_f, synth_f, prof.notes)
        return prof

    def profile_single(self, data: pa.Table | Frame, table_name: str = "table") -> Tier1Profile:
        """Tier 1 of one table (no adversarial test: it needs both sides)."""
        return self._describe(as_frame(data), table_name)

    # -- parts ------------------------------------------------------------------------------

    def _describe(self, frame: Frame, table_name: str) -> Tier1Profile:
        prof = Tier1Profile(table_name=table_name, row_count=len(frame))
        prof.gmm_fits = self._fit_gmms(frame, prof.notes)
        prof.conditional_profiles = self._conditional_profiles(frame)
        prof.temporal_profiles = self._temporal_profiles(frame, prof.notes)
        prof.periodicity = self._periodicity(frame)
        return prof

    def _fit_gmms(self, frame: Frame, notes: list[str]) -> dict[str, GMMFit]:
        mixture = _import("sklearn.mixture")
        if mixture is None:
            notes.append(f"mixture fits skipped: scikit-learn is not installed ({INSTALL_HINT})")
            return {}
        results: dict[str, GMMFit] = {}
        for col in frame.numbers:
            values = col.present()  # integers stay integers: scikit-learn scores them differently
            if len(values) < 20:
                continue
            x = values.reshape(-1, 1)
            best_bic = np.inf
            best: GMMFit | None = None
            for n in range(1, self.max_gmm_components + 1):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        gm = mixture.GaussianMixture(n_components=n, random_state=0, max_iter=200)
                        gm.fit(x)
                    bic = gm.bic(x)
                    if bic < best_bic:
                        best_bic = bic
                        best = GMMFit(
                            column=col.name,
                            n_components=n,
                            means=[float(m[0]) for m in gm.means_],
                            weights=[float(w) for w in gm.weights_],
                            stds=[float(np.sqrt(c[0][0])) for c in gm.covariances_],
                            bic=float(bic),
                            aic=float(gm.aic(x)),
                        )
                except (ValueError, FloatingPointError, np.linalg.LinAlgError):
                    continue
            if best is not None:
                results[col.name] = best
        return results

    def _conditional_profiles(self, frame: Frame) -> list[ConditionalProfile]:
        numeric = frame.numbers[:10]
        cond = [c for c in frame if not c.is_numeric and not c.is_datetime]
        cond = [c for c in cond if c.nunique() <= self.max_categorical_for_conditional][:5]
        profiles: list[ConditionalProfile] = []
        for cc in cond:
            keys, codes = _group_codes(cc)
            for nc in numeric:
                stats: dict[str, dict[str, float]] = {}
                data = nc.values.astype(np.float64)
                ok = nc.valid & (codes >= 0)
                for gi, key in enumerate(keys):
                    g = data[ok & (codes == gi)]
                    if len(g) < 5:
                        continue
                    stats[str(key)] = {
                        "mean": float(g.mean()),
                        "std": float(g.std(ddof=1)),
                        "count": float(len(g)),
                        "p25": float(np.quantile(g, 0.25)),
                        "p75": float(np.quantile(g, 0.75)),
                    }
                if stats:
                    profiles.append(ConditionalProfile(nc.name, cc.name, stats))
        return profiles

    def _adversarial_test(
        self, real: Frame, synthetic: Frame, notes: list[str]
    ) -> AdversarialResult | None:
        ens = _import("sklearn.ensemble")
        sel = _import("sklearn.model_selection")
        if ens is None or sel is None:
            notes.append(
                f"adversarial test skipped: scikit-learn is not installed ({INSTALL_HINT})"
            )
            return None
        common = [n for n in real.names if n in synthetic]
        if not common:
            notes.append("adversarial test skipped: the tables share no columns")
            return None
        rx = np.column_stack([_encode(real[n]) for n in common])
        sx = np.column_stack([_encode(synthetic[n]) for n in common])
        each = self.max_rows_adversarial // 2
        if len(rx) > each:
            rx = rx[sample_positions(len(rx), each)]
        if len(sx) > each:
            sx = sx[sample_positions(len(sx), each)]
        x = np.nan_to_num(np.vstack([rx, sx]), nan=0.0)
        y = np.array([1] * len(rx) + [0] * len(sx))
        if len(x) < 20:
            notes.append("adversarial test skipped: fewer than 20 rows")
            return None
        try:
            clf = ens.GradientBoostingClassifier(n_estimators=50, max_depth=3, random_state=0)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                auc = sel.cross_val_score(clf, x, y, cv=3, scoring="roc_auc")
                acc = sel.cross_val_score(clf, x, y, cv=3, scoring="accuracy")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # all-zero importances divide 0 by 0
                clf.fit(x, y)
                importances = clf.feature_importances_
        except ValueError as exc:  # e.g. one class has fewer rows than folds
            notes.append(f"adversarial test failed: {exc}")
            return None
        top = sorted(zip(common, importances, strict=True), key=lambda t: t[1], reverse=True)[:10]
        mean_auc = float(np.mean(auc))
        return AdversarialResult(
            auc_roc=mean_auc,
            accuracy=float(np.mean(acc)),
            top_features=[(str(f), float(i)) for f, i in top],
            n_samples=len(x),
            passed=mean_auc < self.adversarial_threshold,
        )

    def _temporal_profiles(self, frame: Frame, notes: list[str]) -> dict[str, TemporalProfile]:
        stats = _import("scipy.stats")
        profiles: dict[str, TemporalProfile] = {}
        warned = False
        for col in frame:
            if not col.is_datetime:
                continue
            ts = np.sort(col.present())
            if len(ts) < 10:
                continue
            gaps = np.diff(ts) / 1e9
            secs = ts // 10**9
            ac1 = _autocorr(secs, 1) if len(secs) > 2 else None
            ac7 = _autocorr(secs, 7) if len(secs) > 8 else None
            gap_dist = None
            if len(gaps) >= 20:
                if stats is None:
                    if not warned:
                        notes.append(
                            f"gap distributions skipped: SciPy is not installed ({INSTALL_HINT})"
                        )
                        warned = True
                else:
                    gap_dist = _gap_distribution(stats, gaps)
            profiles[col.name] = TemporalProfile(
                column=col.name,
                mean_gap_seconds=float(gaps.mean()),
                std_gap_seconds=float(gaps.std(ddof=1)),
                min_gap_seconds=float(cast(Callable[[], np.float64], gaps.min)()),
                max_gap_seconds=float(cast(Callable[[], np.float64], gaps.max)()),
                autocorrelation_lag1=ac1,
                autocorrelation_lag7=ac7,
                gap_distribution=gap_dist,
            )
        return profiles

    def _periodicity(self, frame: Frame) -> dict[str, PeriodicityResult]:
        results: dict[str, PeriodicityResult] = {}
        for col in frame.numbers[:10]:
            series = col.floats()
            if len(series) < 32:
                continue
            values = series - series.mean()
            yf = np.abs(np.fft.rfft(values))
            xf = np.fft.rfftfreq(len(values), d=1.0)
            yf, xf = yf[1:], xf[1:]
            if len(yf) == 0:
                continue
            top = np.argsort(yf)[::-1][:5]
            top_periods = [(float(1.0 / xf[i]) if xf[i] > 0 else 0.0, float(yf[i])) for i in top]
            dom = top[0]
            freq = float(xf[dom])
            power = float(yf[dom])
            median = float(np.median(yf))
            results[col.name] = PeriodicityResult(
                column=col.name,
                dominant_period=float(1.0 / freq) if freq > 0 else None,
                dominant_frequency=freq,
                dominant_power=power,
                top_periods=top_periods,
                is_periodic=power > 5 * median if median > 0 else False,
            )
        return results


def _group_codes(col: Column) -> tuple[list[Any], npt.NDArray[np.intp]]:
    """The distinct present values of ``col`` in sort order, and each row's index into them
    (-1 where the value is missing)."""
    present = col.present().tolist()
    try:
        keys = sorted(set(present))
    except TypeError:  # values that do not order against each other
        keys = sorted(set(present), key=str)
    index = {k: i for i, k in enumerate(keys)}
    codes = np.full(len(col), -1, dtype=np.intp)
    codes[col.valid] = [index[v] for v in present]
    return keys, codes


def _encode(col: Column) -> npt.NDArray[np.float64]:
    """A column as classifier features: numbers with missing values at the median, timestamps as
    epoch seconds (a missing one far below every real time), anything else as the sorted-order code
    of its text (a missing value is the text ``__NULL__``). Missing values left over become 0
    downstream."""
    if col.is_numeric:
        v = col.values.astype(np.float64)
        if not col.valid.all():
            ok = v[col.valid]
            v = np.where(col.valid, v, np.median(ok) if ok.size else np.nan)
        return v
    if col.is_datetime:
        # a missing timestamp is the smallest integer (a data frame's NaT as an int64), so it falls
        # far below every real time: the classifier can see where a column is null
        ns = np.where(col.valid, col.values, np.iinfo(np.int64).min)
        return (ns // 10**9).astype(np.float64)
    return col.codes().astype(np.float64)


def _autocorr(x: npt.NDArray[Any], lag: int) -> float | None:
    a, b = x[lag:].astype(np.float64), x[:-lag].astype(np.float64)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = float(np.corrcoef(a, b)[0, 1])
    return r


def _gap_distribution(stats: Any, gaps: npt.NDArray[np.float64]) -> str | None:
    positive = gaps[gaps > 0]
    if positive.size == 0:
        return None
    try:
        with np.errstate(all="ignore"):
            p_exp = stats.kstest(
                positive,
                "expon",
                args=(
                    float(cast(Callable[[], np.float64], positive.min)()),
                    float(positive.mean()),
                ),
            )[1]
            # A frozen distribution's cdf, not the string "norm" with args: SciPy 1.18 resolves
            # the string to the bare ``ndtr`` ufunc and calls it with (x, loc, scale).
            normal = stats.norm(float(gaps.mean()), float(gaps.std(ddof=1)))
            p_norm = stats.kstest(gaps, normal.cdf)[1]
    except ValueError:
        return None
    return "exponential" if p_exp > p_norm else "normal"
