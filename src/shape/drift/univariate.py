"""Drift of the univariate depth fields (W3-07, issue #103): zero inflation, heaping, Benford
conformity and the tail index, read from the ``zero_share``, ``zero_inflation``, ``heaping``,
``benford`` and ``tail_index`` entries of a column profile.

A profile written before these fields existed has nothing to compare on that side, and yields no
change here. Thresholds are in ``shape.drift.engine.DEFAULT_THRESHOLDS`` (``docs/DRIFT.md``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import View

_CLASS_RANK = {"close": 0, "acceptable": 1, "marginal": 2, "nonconformity": 3}
BENFORD_SAMPLE_CAP = 50_000  # the profile's cap on the values its first digits are counted on
_NOISE = 4.0  # standard errors of a move in a share or a ratio
_MAD_NOISE = 3.0  # standard errors of the difference of two Benford MADs
_TAIL_NOISE = 3.0  # standard errors of the difference of two Hill estimates
_BENFORD_P = (0.301, 0.176, 0.125, 0.097, 0.079, 0.067, 0.058, 0.051, 0.046)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _zero(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    from .engine import _change

    b, c = _num(base.univariate.get("zero_share")), _num(cur.univariate.get("zero_share"))
    if b is None or c is None or not enough:
        return []
    b_inf = base.univariate.get("zero_inflation") or {}
    c_inf = cur.univariate.get("zero_inflation") or {}
    appeared = bool(c_inf.get("inflated")) and not b_inf.get("inflated")
    p = 0.5 * (b + c)
    se = math.sqrt(p * (1.0 - p) * (1.0 / base.non_null + 1.0 / cur.non_null))
    beyond_noise = abs(c - b) > _NOISE * se  # a flag flipping on the same share is not a change
    moved = abs(c - b) > max(th["zero_share"], _NOISE * se)
    if not (moved or (appeared and beyond_noise)):
        return []
    excess = 0.0
    if appeared:
        expected = max(
            _num(c_inf.get("poisson_expected")) or 0.0, _num(c_inf.get("nb_expected")) or 0.0
        )
        excess = c - expected
    return [_change(name, "zero_inflation_change", b, c, max(abs(c - b), excess))]


def _ratio_se(side: View, entry: Mapping[str, Any], ratio: float) -> float:
    """The standard error of a heaping ratio: that of the observed share, over the expected one."""
    observed, expected = _num(entry.get("observed_share")), _num(entry.get("expected_share"))
    if observed is None or not expected or not side.non_null:
        return 0.0
    return math.sqrt(max(observed * (1.0 - observed), 0.0) / side.non_null) / expected


def _ratio_moved(
    base: View, cur: View, b: Mapping[str, Any], c: Mapping[str, Any], rb: float, rc: float
) -> bool:
    se = math.hypot(_ratio_se(base, b, rb), _ratio_se(cur, c, rc))
    return rc - rb > _NOISE * se


def _heaping(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    from .engine import _change, _ratio_score

    b, c = base.univariate.get("heaping"), cur.univariate.get("heaping")
    if not isinstance(b, Mapping) or not isinstance(c, Mapping) or not enough:
        return []
    if not c.get("heaped"):
        return []
    c_ratio, b_ratio = _num(c.get("ratio")), _num(b.get("ratio"))
    if c_ratio is None:
        return []
    if not b.get("heaped"):
        if b_ratio is not None and not _ratio_moved(base, cur, b, c, b_ratio, c_ratio):
            return []  # the flag flipped on a ratio that did not move beyond sampling noise
        return [_change(name, "heaping_change", b_ratio, c_ratio, 1.0 - 1.0 / c_ratio)]
    if b_ratio and c_ratio >= th["heaping_ratio"] * b_ratio:
        if not _ratio_moved(base, cur, b, c, b_ratio, c_ratio):
            return []
        return [_change(name, "heaping_change", b_ratio, c_ratio, _ratio_score(b_ratio / c_ratio))]
    return []


def _mad_se(view: View) -> float:
    """An upper bound on the standard error of a first-digit MAD: the root of the summed variances
    of the nine shares (under Benford's law), over 9 and over the root of the sample size."""
    n = min(view.non_null, BENFORD_SAMPLE_CAP)
    if n <= 0:
        return math.inf
    return math.sqrt(sum(p * (1.0 - p) for p in _BENFORD_P) / n) / 9.0


def _benford(name: str, base: View, cur: View, th: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .engine import _change

    b, c = base.univariate.get("benford"), cur.univariate.get("benford")
    if not isinstance(b, Mapping) or not isinstance(c, Mapping):
        return []
    if not (b.get("applicable") and c.get("applicable")):
        return []
    rb, rc = _CLASS_RANK.get(str(b.get("conformity"))), _CLASS_RANK.get(str(c.get("conformity")))
    if rb is None or rc is None or rc - rb < th["benford_class_steps"]:
        return []
    # A class is a fixed band of the MAD, and a sample's MAD carries noise (about 0.003 at 1,000
    # values): a distribution near a band edge lands two classes apart in two samples. The MAD
    # must have risen by more than three standard errors of the difference.
    mb, mc = _num(b.get("mad")), _num(c.get("mad"))
    if mb is None or mc is None:
        return []
    if mc - mb <= _MAD_NOISE * math.hypot(_mad_se(base), _mad_se(cur)):
        return []
    return [_change(name, "benford_change", b["conformity"], c["conformity"], (rc - rb) / 3.0)]


def _tail(name: str, base: View, cur: View, th: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .engine import _change

    b, c = base.univariate.get("tail_index"), cur.univariate.get("tail_index")
    if not isinstance(b, Mapping) or not isinstance(c, Mapping):
        return []
    ab, ac = _num(b.get("alpha")), _num(c.get("alpha"))
    if ab is None or ac is None or ab <= 0.0:
        return []
    if not (ac < (1.0 - th["tail_alpha_drop"]) * ab and ac < th["tail_alpha_max"]):
        return []
    # the estimate is noisy (its standard error is alpha / sqrt(k)): the drop must beat it
    se = math.hypot(_num(b.get("se")) or 0.0, _num(c.get("se")) or 0.0)
    if ab - ac <= _TAIL_NOISE * se:
        return []
    return [_change(name, "tail_change", ab, ac, 1.0 - ac / ab)]


_MIXTURE_SAMPLE_CAP = 4_000  # the profile fits a mixture on at most this many values
_KS_ALPHA_001 = 1.95  # the KS critical coefficient at alpha = 0.001 (as in the family check)
_FLIP_MIN = 0.1  # a seasonal flag flipping needs the strength to have moved at least this
_GRID = 512


def _mixture_cdf(m: Mapping[str, Any], xs: Any) -> Any:
    import numpy as np

    out = np.zeros(len(xs))
    for c in m["components"]:
        sd = float(c["sd"]) or 1e-12
        z = (xs - float(c["mean"])) / (sd * math.sqrt(2.0))
        out += float(c["weight"]) * 0.5 * (1.0 + np.array([math.erf(v) for v in z]))
    return out


def _valid_mixture(m: Any) -> bool:
    if not isinstance(m, Mapping) or not isinstance(m.get("components"), list):
        return False
    return bool(m["components"]) and all(
        _num(c.get("weight")) is not None
        and _num(c.get("mean")) is not None
        and _num(c.get("sd")) is not None
        for c in m["components"]
        if isinstance(c, Mapping)
    )


def _mixture_evidence(b: Mapping[str, Any], c: Mapping[str, Any], nb: int, nc: int) -> bool:
    """True when the two fitted mixtures differ by more than two samples of one distribution
    would: the KS distance between their CDFs beats the critical value of the sample sizes."""
    import numpy as np

    lo = min(float(x["mean"]) - 5.0 * float(x["sd"]) for m in (b, c) for x in m["components"])
    hi = max(float(x["mean"]) + 5.0 * float(x["sd"]) for m in (b, c) for x in m["components"])
    xs = np.linspace(lo, hi, _GRID)
    ks = float(np.max(np.abs(_mixture_cdf(b, xs) - _mixture_cdf(c, xs))))
    nb, nc = min(nb, _MIXTURE_SAMPLE_CAP), min(nc, _MIXTURE_SAMPLE_CAP)
    return ks > _KS_ALPHA_001 * math.sqrt((nb + nc) / (nb * nc))


def _mixture(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    from .engine import _change

    b, c = base.univariate.get("mixture"), cur.univariate.get("mixture")
    if not (_valid_mixture(b) and _valid_mixture(c)) or not enough:
        return []
    assert isinstance(b, Mapping) and isinstance(c, Mapping)
    kb, kc = int(b["k"]), int(c["k"])
    structural = kb != kc or bool(b.get("multimodal")) != bool(c.get("multimodal"))
    moved = 0.0
    if kb == kc:  # equal counts: the components pair up in order of mean
        moved = max(
            abs(float(x["weight"]) - float(y["weight"]))
            for x, y in zip(b["components"], c["components"], strict=True)
        )
        structural = structural or moved > th["mixture_weight"]
    if not structural or not _mixture_evidence(b, c, base.non_null, cur.non_null):
        return []
    score = (
        abs(kc - kb) / 3.0
        if kb != kc
        else max(moved, 0.5 if b.get("multimodal") != c.get("multimodal") else 0.0)
    )
    return [_change(name, "mixture_change", {"k": kb}, {"k": kc}, score)]


def _seasonality(name: str, base: View, cur: View, th: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .engine import _change

    b, c = base.univariate.get("seasonality"), cur.univariate.get("seasonality")
    if not isinstance(b, Mapping) or not isinstance(c, Mapping):
        return []
    if not (b.get("applicable") and c.get("applicable")):
        return []  # a side with no measurement (too short, no time column) is not a change
    if b.get("time_column") != c.get("time_column"):
        return []
    sb, sc = _num(b.get("strength")), _num(c.get("strength"))
    if sb is None or sc is None:
        return []
    delta = round(abs(sc - sb), 9)  # a move of exactly the threshold is not a change
    flipped = bool(b.get("seasonal")) != bool(c.get("seasonal")) and delta >= _FLIP_MIN
    # the period of a series that is not seasonal is whichever candidate fits the noise best
    repinned = (
        b.get("period") != c.get("period") and bool(b.get("seasonal")) and bool(c.get("seasonal"))
    )
    if not (flipped or repinned or delta > th["seasonality_strength"]):
        return []
    return [
        _change(
            name,
            "seasonality_change",
            {"period": b.get("period"), "strength": sb},
            {"period": c.get("period"), "strength": sc},
            max(delta, 0.5 if flipped or repinned else 0.0),
        )
    ]


def diff_univariate(
    name: str, base: View, cur: View, th: Mapping[str, Any], enough: bool
) -> list[dict[str, Any]]:
    """The univariate changes of one numeric column. ``enough`` is true when both sides have at
    least ``min_rows`` non-null values (the share and ratio comparisons need it)."""
    if not base.univariate or not cur.univariate:
        return []
    return [
        *_zero(name, base, cur, th, enough),
        *_heaping(name, base, cur, th, enough),
        *_benford(name, base, cur, th),
        *_tail(name, base, cur, th),
        *_mixture(name, base, cur, th, enough),
        *_seasonality(name, base, cur, th),
    ]
