"""Differential-privacy noise for numeric columns (experimental).

:class:`DifferentialPrivacy` adds Laplace or Gaussian noise to every integer and float column of a
table, with a scale set by the column's range (its sensitivity) and ``epsilon``:

* Laplace: scale = range / epsilon;
* Gaussian: sigma = range * sqrt(2 ln(1.25 / delta)) / epsilon.

Noised values are clipped back to the column's original minimum and maximum (``clip_to_range``),
and missing values stay missing.

**Randomness.** The noise comes from the operating system's entropy unless a ``seed`` (or an
``rng``) is passed explicitly, so two calls without one never repeat; the same ``seed`` gives the
same noise. There is no fixed default seed (D-07).

**What this is not.** It perturbs each value by noise calibrated to the column's range, as a
building block. It does not track a privacy budget across calls or columns, and Shape makes no
claim that a table it returns satisfies (epsilon, delta)-differential privacy as a whole. Treat
it as experimental.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]


@dataclass
class DPResult:
    """What :meth:`DifferentialPrivacy.apply` did."""

    epsilon: float
    mechanism: str
    columns_noised: list[str] = field(default_factory=list)
    actual_sensitivity: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DifferentialPrivacy:
    """Laplace or Gaussian noise for the numeric columns of a table. See the module docstring."""

    def __init__(
        self,
        epsilon: float = 1.0,
        delta: float = 1e-5,
        mechanism: str = "laplace",
        clip_to_range: bool = True,
    ) -> None:
        if mechanism not in ("laplace", "gaussian"):
            raise ValueError("mechanism must be 'laplace' or 'gaussian'")
        if not epsilon > 0:
            raise ValueError("epsilon must be positive")
        if mechanism == "gaussian" and not 0 < delta < 1:
            raise ValueError("delta must be between 0 and 1 for the gaussian mechanism")
        self.epsilon = epsilon
        self.delta = delta
        self.mechanism = mechanism
        self.clip_to_range = clip_to_range

    def apply(
        self,
        table: pa.Table,
        *,
        seed: int | None = None,
        rng: np.random.Generator | None = None,
    ) -> tuple[pa.Table, DPResult]:
        """``table`` with noise added to its integer and float columns, and a :class:`DPResult`.

        Without ``seed`` or ``rng`` the noise is drawn from OS entropy. ``seed`` gives a
        reproducible generator (``numpy.random.default_rng(seed)``); ``rng`` is used as given.
        Noised columns are float64; boolean, text, temporal and decimal columns are returned
        unchanged. Columns are visited in table order, and a column with no values or a single
        value is skipped without drawing."""
        if seed is not None and rng is not None:
            raise ValueError("pass a seed or an rng, not both")
        gen = rng if rng is not None else np.random.default_rng(seed)
        out = table
        noised: list[str] = []
        sensitivity: dict[str, float] = {}
        for pos, name in enumerate(table.column_names):
            chunked = table.column(name)
            t = chunked.type
            if not (pa.types.is_integer(t) or pa.types.is_floating(t)):
                continue
            arr = chunked.combine_chunks() if chunked.num_chunks != 1 else chunked.chunk(0)
            is_null = np.asarray(pc.is_null(arr).to_numpy(zero_copy_only=False), dtype=bool)
            values = arr.cast(pa.float64()).fill_null(np.nan).to_numpy(zero_copy_only=False)
            values = np.asarray(values, dtype=np.float64)
            present = values[~np.isnan(values)]
            if present.size == 0:
                continue
            lo, hi = float(present.min()), float(present.max())
            span = hi - lo
            if span == 0:
                continue
            sensitivity[name] = span
            n = len(values)
            if self.mechanism == "laplace":
                noise = gen.laplace(0, span / self.epsilon, size=n)
            else:
                sigma = span * np.sqrt(2 * np.log(1.25 / self.delta)) / self.epsilon
                noise = gen.normal(0, sigma, size=n)
            result = values + noise
            if self.clip_to_range:
                result = np.clip(result, lo, hi)
            out = out.set_column(pos, name, pa.array(result, mask=is_null))
            noised.append(name)
        return out, DPResult(self.epsilon, self.mechanism, noised, sensitivity)
