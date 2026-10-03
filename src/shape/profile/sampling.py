"""Sampling controls and the sampling record of a profile (W2-07).

A profile says how much of the data it saw. :class:`SampleSpec` is what the caller asks for
(``shape profile --sample 10%``); :func:`select` picks the rows; :func:`record` is the
``sampling`` entry every table profile carries; and :func:`adequacy_level` and the helpers beside
it say whether that many rows can support the statistics. Nothing here is applied unless the
caller asks: without a spec the whole table is read and the record says so.

Selection depends on numpy's legacy ``RandomState`` only (a frozen, version-independent stream),
never on the kernel, so the same rows are chosen in both kernel modes and on every run.

The statistics also sample inside themselves (pattern detection, distribution fitting, the joint
analysis). Their parameters live here, the code paths read them from here, and the table's record
lists the ones its statistics used (:data:`INTERNAL`); a test fails when a path samples without
being listed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

METHODS = ("random", "systematic", "head")
NONE = "none"  # the method of a table that was read whole
DEFAULT_SEED = 42
MAX_SEED = 2**32 - 1  # numpy's RandomState seeds are 32-bit

MIN_ROWS = 30  # fewer sampled rows are "insufficient": the drift engine's ``min_rows``
LIMITED_SHARE = 0.01  # a value must be at least 1% of the rows to be seen: above that is "limited"
DETECTION_CONFIDENCE = 0.95  # "seen at least once with 95% probability"

ADEQUATE, LIMITED, INSUFFICIENT = "adequate", "limited", "insufficient"
ADEQUACY_LEVELS = (ADEQUATE, LIMITED, INSUFFICIENT)

# The samples the statistics take of their own: (rows, method, seed). ``seed`` is ``None`` for a
# deterministic spread with no random draw. The code paths read the numbers from here.
PATTERN_SAMPLE_ROWS = 1000
PATTERN_SAMPLE_SEED = 42
FIT_SAMPLE_ROWS = 2000
FIT_SAMPLE_SEED = 42
JOINT_JITTER_SEED = 7
RATES_SAMPLE_ROWS = 50_000  # distinct values; the pattern rates of a column with more
KENDALL_SAMPLE_ROWS = 500  # rows of a pair of numeric columns, for Kendall's tau
REFERENCE_PAIRS_ROWS = 1_000_000

INTERNAL: dict[str, dict[str, Any]] = {
    # a string column of more than 1000 values: the share of values matching each pattern
    "pattern_detection": {"rows": PATTERN_SAMPLE_ROWS, "method": "random", "seed": 42},
    # a string column of more than 50,000 distinct values: the personal-data pattern rates are
    # measured on evenly spread distinct values (each weighted by its count)
    "pattern_rates": {"rows": RATES_SAMPLE_ROWS, "method": "systematic", "seed": None},
    # a numeric column of more than 2000 values: the distribution fit and its test
    "distribution_fit": {"rows": FIT_SAMPLE_ROWS, "method": "random", "seed": 42},
    # a table of more than 20,000 rows: evenly spread rows with a seeded offset in each stride
    "joint": {"rows": None, "method": "systematic", "seed": JOINT_JITTER_SEED},
    # a numeric association of more than 500 rows: Kendall's tau on evenly spread rows
    "kendall_tau": {"rows": KENDALL_SAMPLE_ROWS, "method": "systematic", "seed": None},
    # a reference pair check on more than a million rows: evenly spread rows
    "reference_pairs": {"rows": REFERENCE_PAIRS_ROWS, "method": "systematic", "seed": None},
}


@dataclass(frozen=True)
class SampleSpec:
    """What the caller asked for: ``rows`` or ``fraction`` (exactly one), a ``method`` and a
    ``seed`` (``head`` is not random and ignores it)."""

    rows: int | None = None
    fraction: float | None = None
    method: str = "random"
    seed: int = DEFAULT_SEED

    def __post_init__(self) -> None:
        if (self.rows is None) == (self.fraction is None):
            raise ValueError("a sample is a number of rows or a fraction, one of them")
        if self.rows is not None and (
            isinstance(self.rows, bool) or not isinstance(self.rows, int) or self.rows < 1
        ):
            raise ValueError(
                f"a sample of rows must be a whole number of 1 or more, got {self.rows!r}"
            )
        if self.fraction is not None and (
            isinstance(self.fraction, bool)
            or not isinstance(self.fraction, float)
            or not 0.0 < self.fraction <= 1.0
        ):
            raise ValueError(
                f"a sample fraction must be above 0 and at most 1 (a share of the rows), "
                f"got {self.fraction!r}"
            )
        if self.method not in METHODS:
            raise ValueError(
                f"unknown sample method {self.method!r}: choose from {', '.join(METHODS)}"
            )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError(f"the sample seed must be an integer, got {self.seed!r}")
        if not 0 <= self.seed <= MAX_SEED:
            raise ValueError(f"the sample seed must be from 0 to {MAX_SEED}, got {self.seed}")

    @property
    def requested(self) -> dict[str, Any]:
        return {"rows": self.rows} if self.rows is not None else {"fraction": self.fraction}

    def target(self, population: int) -> int:
        """How many of ``population`` rows this asks for (at least 1 of a non-empty table)."""
        if self.rows is not None:
            return min(self.rows, population)
        assert self.fraction is not None
        return min(population, max(1, round(self.fraction * population))) if population else 0


def parse_sample(text: str) -> tuple[int | None, float | None]:
    """``"500"`` is 500 rows and ``"10%"`` a tenth of the rows: ``(rows, fraction)``."""
    raw = text.strip()
    try:
        if raw.endswith("%"):
            fraction = float(raw[:-1]) / 100.0
            if not math.isfinite(fraction) or not 0.0 < fraction <= 1.0:
                raise ValueError
            return None, fraction
        rows = int(raw)
    except ValueError:
        raise ValueError(
            f"--sample takes a number of rows (500) or a percentage above 0 and up to 100 "
            f"(10%), got {text!r}"
        ) from None
    if rows < 1:
        raise ValueError(f"--sample must ask for at least 1 row, got {text!r}")
    return rows, None


def make_spec(
    sample: Any = None, method: str = "random", seed: int | None = None
) -> SampleSpec | None:
    """The spec behind ``shape.profile(sample=, sample_method=, sample_seed=)``: ``sample`` is a
    number of rows (an ``int``), a fraction (a ``float`` above 0 and up to 1), a string such as
    ``"10%"``, or ``None`` for no sampling."""
    if sample is None:
        if method != "random" or seed is not None:
            raise ValueError("sample_method and sample_seed need sample=")
        return None
    use_seed = DEFAULT_SEED if seed is None else seed
    if isinstance(sample, bool):
        raise ValueError(f"sample must be a number of rows or a fraction, got {sample!r}")
    if isinstance(sample, str):
        rows, fraction = parse_sample(sample)
    elif isinstance(sample, (int, np.integer)):
        rows, fraction = int(sample), None
    elif isinstance(sample, (float, np.floating)):
        rows, fraction = None, float(sample)
        if not math.isfinite(fraction) or not 0.0 < fraction <= 1.0:
            raise ValueError(
                f"a float sample is a fraction above 0 and at most 1, got {sample!r} "
                "(pass an int for a number of rows)"
            )
    else:
        raise ValueError(f"sample must be a number of rows or a fraction, got {sample!r}")
    return SampleSpec(rows, fraction, method, use_seed)


def select(spec: SampleSpec, population: int) -> np.ndarray[Any, np.dtype[np.int64]] | None:
    """The sorted row positions the spec picks from ``population`` rows, or ``None`` when it
    asks for every row (the table is then read whole).

    ``random``: a uniform sample without replacement. ``systematic``: ``k`` rows evenly spread by
    a step of ``population / k`` from a seeded start (every step-th row; the step need not be
    whole). ``head``: the first ``k`` rows."""
    k = spec.target(population)
    if k >= population:
        return None
    if spec.method == "head":
        return np.arange(k, dtype=np.int64)
    rng = np.random.RandomState(spec.seed)
    if spec.method == "random":
        return np.sort(rng.choice(population, size=k, replace=False)).astype(np.int64)
    step = population / k
    start = rng.random_sample() * step
    pos = np.floor(start + np.arange(k) * step).astype(np.int64)
    return np.asarray(np.minimum(pos, population - 1), dtype=np.int64)


# --- adequacy ---------------------------------------------------------------------------------


def min_detectable_share(n: int) -> float | None:
    """The smallest share of the values that is seen at least once in ``n`` values with 95%
    probability: ``1 - 0.05 ** (1 / n)``. ``None`` for no values."""
    if n <= 0:
        return None
    return float(1.0 - (1.0 - DETECTION_CONFIDENCE) ** (1.0 / n))


def null_rate_se(rate: float | None, n: int, population: int | None) -> float | None:
    """The standard error of a null rate seen in ``n`` rows: ``sqrt(p (1 - p) / n)``, times the
    finite population correction ``sqrt((N - n) / (N - 1))`` when the source's ``N`` rows are
    known (zero when every row was read). ``None`` for no rows or an unknown rate."""
    if rate is None or n <= 0:
        return None
    if population is not None and n >= population:
        return 0.0
    se = math.sqrt(rate * (1.0 - rate) / n)
    if population is not None and population > 1:
        se *= math.sqrt((population - n) / (population - 1))
    return se


def column_adequacy(
    rows: int, null_count: int, null_rate: float | None, population: int | None
) -> dict[str, Any]:
    """The ``adequacy`` entry of a column of a table of ``rows`` profiled rows."""
    non_null = max(rows - null_count, 0)
    return {
        "non_null": non_null,
        "null_rate_se": null_rate_se(null_rate, rows, population),
        "min_detectable_share": min_detectable_share(non_null),
    }


def adequacy_level(rows: int) -> tuple[str, str]:
    """``(level, reason)`` for a table profiled on ``rows`` rows (the rule is in
    ``docs/PROFILING_NOTES.md``): ``insufficient`` below 30 rows; ``limited`` when a value must be
    more than 1% of the rows to be seen with 95% probability (fewer than 299 rows); else
    ``adequate``."""
    if rows < MIN_ROWS:
        return INSUFFICIENT, (
            f"{rows} rows profiled, fewer than {MIN_ROWS} (the drift engine's minimum): "
            "rates, enums and fits are not stable"
        )
    share = min_detectable_share(rows)
    assert share is not None
    pct = f"{share * 100:.2f}%"
    if share > LIMITED_SHARE:
        return LIMITED, (
            f"a value must be at least {pct} of the rows to be seen with 95% probability, "
            f"above {LIMITED_SHARE * 100:g}%: rarer values may be missing"
        )
    return ADEQUATE, f"a value of at least {pct} of the rows is seen with 95% probability"


# --- the record -------------------------------------------------------------------------------


def internal_entry(
    analysis: str, rows: int | None = None, columns: list[str] | None = None
) -> dict[str, Any]:
    """One ``internal`` entry of the record, from the registry (``rows`` overrides it)."""
    spec = INTERNAL[analysis]
    entry: dict[str, Any] = {
        "analysis": analysis,
        "rows": spec["rows"] if rows is None else rows,
        "method": spec["method"],
        "seed": spec["seed"],
    }
    if columns:
        entry["columns"] = sorted(columns)
    return entry


def record(
    spec: SampleSpec | None,
    population: int | None,
    sampled: int,
    taken: bool,
    internal: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``sampling`` entry of a table profile.

    ``spec`` is what was asked for (``None``: nothing), ``taken`` whether rows were left out (the
    seed is recorded then, even for ``head``, which does not use it), ``population`` the source's
    row count when it is known, ``sampled`` the rows profiled."""
    level, reason = adequacy_level(sampled)
    method = spec.method if spec is not None and taken else NONE
    seed = spec.seed if spec is not None and taken else None
    return {
        "method": method,
        "seed": seed,
        "requested": spec.requested if spec is not None else None,
        "population_rows": population,
        "sampled_rows": sampled,
        "internal": list(internal or []),
        "adequacy": level,
        "adequacy_reason": reason,
    }


def describe(rec: dict[str, Any] | None) -> str:
    """One line for a record, for the text outputs; ``not recorded`` for an older profile."""
    if not rec:
        return "not recorded"
    population = rec.get("population_rows")
    of = f" of {population:,}" if population is not None else ""
    if rec.get("method", NONE) == NONE:
        base = f"read whole: {rec['sampled_rows']:,} rows"
    else:
        seed = f", seed {rec['seed']}" if rec.get("seed") is not None else ""
        base = f"{rec['method']} sample of {rec['sampled_rows']:,}{of} rows{seed}"
    return f"{base}; adequacy {rec.get('adequacy', 'not recorded')}"


def internal_samples(
    columns: list[Any], rows: int, joint: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The internal samples the statistics of a table took: which analyses sampled, on which
    columns, and how many rows. The column profiles say what their own statistics drew
    (``samples``); the joint analysis says its own sample (``sampled``, ``rows_analyzed``), and
    its association entries say which pairs of numeric columns were too long for Kendall's tau."""
    out: list[dict[str, Any]] = []
    for analysis in ("pattern_detection", "pattern_rates", "distribution_fit"):
        named = [c.name for c in columns if analysis in c.samples]
        if named:
            out.append(internal_entry(analysis, columns=named))
    if joint:
        if joint.get("sampled"):
            out.append(internal_entry("joint", rows=int(joint["rows_analyzed"])))
        if any(
            a.get("kind") == "numeric"
            and a.get("kendall") is not None
            and a.get("rows", 0) > KENDALL_SAMPLE_ROWS
            for a in joint.get("associations") or []
        ):
            out.append(internal_entry("kendall_tau"))
    return out
