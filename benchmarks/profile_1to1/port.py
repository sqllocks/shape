"""1:1 vectorised port of Spindle's DataProfiler (sqllocks_spindle/inference/profiler.py).

numpy + pyarrow + stdlib only.  Produces the same TableProfile / ColumnProfile
fields with the same semantics as Spindle, including:

* pandas-equivalent reading semantics (read_csv / read_parquet dtype mapping,
  NA tokens, int-with-nulls -> float64, bool-with-nulls -> object, ...)
* _infer_spindle_type (bool-like / numeric / datetime coercion of strings)
* null/cardinality/uniqueness/enum rules, enum + top-500 value proportions
  with pandas' key formatting and stable-descending tie order
* min/max with pandas' Python types
* mean/std (ddof=1), numpy 'linear' quantiles (incl. p0_5/p99_5), 1.5 IQR outliers
* distribution fitting on the same 2000-row Generator(42) sample, with numpy
  re-implementations of scipy's norm/uniform/expon closed-form MLE, scipy's
  lognorm MLE (bracketed brentq on dL/dloc, falling back to the generic
  Nelder-Mead penalised-NLL fit exactly as scipy does), KS statistic and the
  exact kstwo survival function used for the p > 0.05 gate
* fit_score: re-fit + KS on the *full* column, as Spindle does
* pattern detection on the same RandomState(42) 1000-row sample with the same
  RE2 patterns pandas' Arrow string backend uses
* string length stats, 24-bin hour / 7-bin dow / year+month histograms
* primary key heuristic, cross-table FK detection (profile_dataset)
* Pearson correlation matrix (pairwise-complete, like DataFrame.corr)

Entry points: profile_csv(path), profile_parquet(path), profile_table(pa.Table),
profile_dataset({name: pa.Table | path}).

Threading: columns are profiled in a thread pool (pyarrow/numpy release the
GIL).  PROFILE_THREADS=1 (or threads=1) forces a single thread everywhere
(pyarrow cpu + io pools, CSV/Parquet readers, column pool).  Set
OPENBLAS_NUM_THREADS=1 before import to make the BLAS in the correlation step
single-threaded too (bench.py does this).
"""
from __future__ import annotations

import datetime as _dt
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

# ---------------------------------------------------------------------------
# Output dataclasses (same field names as Spindle; raw-bearing fields are
# plain attributes here -- Spindle hides them behind InitVar/properties
# (ADR-007) but exposes the same values through .enum_values/.min_value/...)
# ---------------------------------------------------------------------------


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    null_count: int
    null_rate: float
    cardinality: int
    cardinality_ratio: float
    is_unique: bool
    is_enum: bool
    enum_values: dict[str, float] | None
    min_value: Any
    max_value: Any
    mean: float | None
    std: float | None
    distribution: str | None
    distribution_params: dict[str, float] | None
    pattern: str | None
    is_primary_key: bool
    is_foreign_key: bool
    fk_ref_table: str | None
    quantiles: dict[str, float] | None = None
    hour_histogram: list[float] | None = None
    dow_histogram: list[float] | None = None
    temporal_histogram: dict[str, Any] | None = None
    string_length: dict[str, float] | None = None
    outlier_rate: float | None = None
    value_counts_ext: dict[str, float] | None = None
    fit_score: float | None = None


@dataclass
class TableProfile:
    name: str
    row_count: int
    columns: dict[str, ColumnProfile]
    primary_key: list[str]
    detected_fks: dict[str, str]
    correlation_matrix: dict[str, dict[str, float]] | None = None


@dataclass
class DatasetProfile:
    tables: dict[str, TableProfile]
    relationships: list[dict] = field(default_factory=list)


class Timestamp(_dt.datetime):
    """Stand-in for pandas.Timestamp (same str()/type name) for min/max of datetime64 columns."""

    def __str__(self):  # pandas prints 'YYYY-MM-DD HH:MM:SS[.ffffff]' -- same as datetime
        return _dt.datetime.__str__(self)


# ---------------------------------------------------------------------------
# threading
# ---------------------------------------------------------------------------

def _n_threads(threads: int | None) -> int:
    if threads is None:
        threads = int(os.environ.get("PROFILE_THREADS", "0")) or (os.cpu_count() or 1)
    if threads == 1:
        pa.set_cpu_count(1)
        pa.set_io_thread_count(1)
    return threads


# ---------------------------------------------------------------------------
# Readers -> list of _Col with pandas-equivalent "kind"
#   int      numpy int64 (no nulls)
#   float    numpy float64 (incl. int-with-nulls, all-empty CSV column)
#   bool     numpy bool (no nulls)
#   objbool  object array of Python bools + NaN (bool with nulls)
#   str      pandas 'str' (Arrow-backed) strings
#   dt64     datetime64
#   objdate  object array of datetime.date (Parquet date32)
#   nullobj  object column of all None (Parquet null type)
# ---------------------------------------------------------------------------

# pandas' default NA tokens (pandas._libs.parsers.STR_NA_VALUES)
PANDAS_NA = ["", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND",
             "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"]


@dataclass
class _Col:
    name: str
    kind: str
    arr: Any  # pa.ChunkedArray (or numpy for float)


def _csv_cols(t: pa.Table) -> list[_Col]:
    out = []
    for name, col in zip(t.column_names, t.columns):
        typ = col.type
        if pa.types.is_integer(typ):
            out.append(_Col(name, "int" if col.null_count == 0 else "float",
                            col if col.null_count == 0 else pc.cast(col, pa.float64())))
        elif pa.types.is_floating(typ):
            out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_boolean(typ):
            out.append(_Col(name, "bool" if col.null_count == 0 else "objbool", col))
        elif pa.types.is_null(typ):
            out.append(_Col(name, "float", pa.chunked_array([pa.nulls(len(col), pa.float64())])))
        elif pa.types.is_date(typ) or pa.types.is_time(typ):
            # pandas keeps these as text; Arrow's inference only accepts the canonical
            # ISO forms, so casting back reproduces the original text exactly.
            out.append(_Col(name, "str", pc.cast(col, pa.string())))
        else:
            out.append(_Col(name, "str", pc.cast(col, pa.string()) if typ != pa.string() else col))
    return out


def _arrow_cols(t: pa.Table) -> list[_Col]:
    """pa.Table -> pandas semantics of Table.to_pandas() / pd.read_parquet()."""
    out = []
    for name, col in zip(t.column_names, t.columns):
        typ = col.type
        if pa.types.is_dictionary(typ):
            col = pc.cast(col, typ.value_type)
            typ = col.type
        if pa.types.is_integer(typ):
            if col.null_count == 0:
                out.append(_Col(name, "int", pc.cast(col, pa.int64())))
            else:
                out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_floating(typ):
            out.append(_Col(name, "float", pc.cast(col, pa.float64())))
        elif pa.types.is_boolean(typ):
            out.append(_Col(name, "bool" if col.null_count == 0 else "objbool", col))
        elif pa.types.is_timestamp(typ):
            out.append(_Col(name, "dt64", col))
        elif pa.types.is_date(typ):
            out.append(_Col(name, "objdate", pc.cast(col, pa.date32())))
        elif pa.types.is_null(typ):
            out.append(_Col(name, "nullobj", col))
        elif pa.types.is_large_string(typ) or pa.types.is_string(typ):
            out.append(_Col(name, "str", pc.cast(col, pa.string())))
        else:
            raise NotImplementedError(f"column {name}: arrow type {typ} not supported by the port")
    return out


def read_csv(path, threads: int | None = None) -> pa.Table:
    n = _n_threads(threads)
    ro = pacsv.ReadOptions(use_threads=n != 1, block_size=1 << 24)
    co = pacsv.ConvertOptions(
        null_values=PANDAS_NA, strings_can_be_null=True, quoted_strings_can_be_null=True,
        true_values=["True", "TRUE", "true"], false_values=["False", "FALSE", "false"],
        timestamp_parsers=["@@never%Y"],  # pandas.read_csv does not parse datetimes
    )
    return pacsv.read_csv(path, read_options=ro, convert_options=co)


# ---------------------------------------------------------------------------
# scipy re-implementations (numpy only)
# ---------------------------------------------------------------------------

# --- special.ndtr (cephes ndtr.c coefficients) -----------------------------
_P = np.array([2.46196981473530512524E-10, 5.64189564831068821977E-1, 7.46321056442269912687E0,
               4.86371970985681366614E1, 1.96520832956077098242E2, 5.26445194995477358631E2,
               9.34528527171957607540E2, 1.02755188689515710272E3, 5.57535335369399327526E2])
_Q = np.array([1.32281951154744992508E1, 8.67072140885989742329E1, 3.54937778887819891062E2,
               9.75708501743205489753E2, 1.82390916687909736289E3, 2.24633760818710981792E3,
               1.65666309194161350182E3, 5.57535340817727675546E2])
_R = np.array([5.64189583547755073984E-1, 1.27536670759978104416E0, 5.01905042251180477414E0,
               6.16021097993053585195E0, 7.40974269950448939160E0, 2.97886665372100240670E0])
_S = np.array([2.26052863220117276590E0, 9.39603524938001434673E0, 1.20489539808096656605E1,
               1.70814450747565897222E1, 9.60896809063285878198E0, 3.36907645100081516050E0])
_T = np.array([9.60497373987051638749E0, 9.00260197203842689217E1, 2.23200534594684319226E3,
               7.00332514112805075473E3, 5.55923013010394962768E4])
_U = np.array([3.35617141647503099647E1, 5.21357949780152679795E2, 4.59432382970980127987E3,
               2.26290000613890934246E4, 4.92673942608635921086E4])
_SQRTH = 7.07106781186547524401E-1


def _polevl(x, c):
    y = np.full_like(x, c[0])
    for ci in c[1:]:
        y = y * x + ci
    return y


def _p1evl(x, c):
    y = x + c[0]
    for ci in c[1:]:
        y = y * x + ci
    return y


def _erf_small(x):
    z = x * x
    return x * _polevl(z, _T) / _p1evl(z, _U)


def _erfc_big(a):
    x = np.abs(a)
    z = np.exp(-a * a)
    lt8 = x < 8.0
    p = np.where(lt8, _polevl(x, _P), _polevl(x, _R))
    q = np.where(lt8, _p1evl(x, _Q), _p1evl(x, _S))
    y = (z * p) / q
    return np.where(a < 0, 2.0 - y, y)


def ndtr(a):
    a = np.asarray(a, dtype=np.float64)
    x = a * _SQRTH
    z = np.abs(x)
    out = np.empty_like(x)
    small = z < _SQRTH
    out[small] = 0.5 + 0.5 * _erf_small(x[small])
    big = ~small
    zb, xb = z[big], x[big]
    e = np.empty_like(zb)
    lt1 = zb < 1.0
    e[lt1] = 1.0 - _erf_small(zb[lt1])
    e[~lt1] = _erfc_big(zb[~lt1])
    y = 0.5 * e
    out[big] = np.where(xb > 0, 1.0 - y, y)
    out[np.isnan(a)] = np.nan
    return out


# --- distributions: cdf (rv_continuous.cdf semantics) ----------------------

def _cdf(name: str, x: np.ndarray, params: tuple) -> np.ndarray:
    if name == "lognormal":
        s, loc, scale = params
    else:
        s = None
        loc, scale = params
    z = (x - loc) / scale
    cond0 = scale > 0 and (s is None or s > 0)
    out = np.zeros(z.shape)
    if not cond0:
        out[:] = np.nan
        return out
    out[np.isnan(z)] = np.nan
    if name == "normal":
        a, b = -np.inf, np.inf
    elif name == "uniform":
        a, b = 0.0, 1.0
    else:
        a, b = 0.0, np.inf
    out[z >= b] = 1.0
    cond = (a < z) & (z < b)
    zc = z[cond]
    if name == "normal":
        v = ndtr(zc)
    elif name == "uniform":
        v = zc
    elif name == "exponential":
        v = -np.expm1(-zc)
    else:
        v = ndtr(np.log(zc) / s)
    out[cond] = v
    return out


# --- KS ---------------------------------------------------------------------

def _ks_stat_sorted(xs: np.ndarray, name: str, params: tuple) -> float:
    n = xs.shape[0]
    cdfvals = _cdf(name, xs, params)
    dplus = (np.arange(1.0, n + 1) / n - cdfvals).max()
    dminus = (cdfvals - np.arange(0.0, n) / n).max()
    return float(dplus if dplus > dminus else dminus)


_E128 = 128
_EP128 = np.ldexp(np.longdouble(1), _E128)
_EM128 = np.ldexp(np.longdouble(1), -_E128)
_SQRT2PI = np.sqrt(2 * np.pi)
_LOG_2PI = np.log(2 * np.pi)
_MIN_LOG = -708
_SQRT3 = np.sqrt(3)
_PI_SQUARED = np.pi ** 2
_PI_FOUR = np.pi ** 4
_PI_SIX = np.pi ** 6
_STIRLING = [-2.955065359477124183e-2, 6.4102564102564102564e-3, -1.9175269175269175269e-3,
             8.4175084175084175084e-4, -5.952380952380952381e-4, 7.9365079365079365079e-4,
             -2.7777777777777777778e-3, 8.3333333333333333333e-2]


def _clip(p):
    return float(min(max(p, 0.0), 1.0))


def _kolmogn_DMTW(n, d):
    if d >= 1.0:
        return 1.0
    nd = n * d
    if nd <= 0.5:
        return 0.0
    k = int(np.ceil(nd))
    h = k - nd
    m = 2 * k - 1
    H = np.zeros([m, m])
    intm = np.arange(1, m + 1)
    v = 1.0 - h ** intm
    w = np.empty(m)
    fac = 1.0
    for j in intm:
        w[j - 1] = fac
        fac /= j
        v[j - 1] *= fac
    tt = max(2 * h - 1.0, 0) ** m - 2 * h ** m
    v[-1] = (1.0 + tt) * fac
    for i in range(1, m):
        H[i - 1:, i] = w[:m - i + 1]
    H[:, 0] = v
    H[-1, :] = np.flip(v, axis=0)
    Hpwr = np.eye(np.shape(H)[0])
    nn = n
    expnt = 0
    Hexpnt = 0
    while nn > 0:
        if nn % 2:
            Hpwr = np.matmul(Hpwr, H)
            expnt += Hexpnt
        H = np.matmul(H, H)
        Hexpnt *= 2
        if np.abs(H[k - 1, k - 1]) > _EP128:
            H /= _EP128
            Hexpnt += _E128
        nn = nn // 2
    p = Hpwr[k - 1, k - 1]
    for i in range(1, n + 1):
        p = i * p / n
        if np.abs(p) < _EM128:
            p *= _EP128
            expnt -= _E128
    if expnt != 0:
        p = np.ldexp(p, expnt)
    return _clip(p)


def _pomeranz_j1j2(i, n, ll, ceilf, roundf):
    if i == 0:
        j1, j2 = -ll - ceilf - 1, ll + ceilf - 1
    else:
        ip1div2, ip1mod2 = divmod(i + 1, 2)
        if ip1mod2 == 0:
            if ip1div2 == n + 1:
                j1, j2 = n - ll - ceilf - 1, n + ll + ceilf - 1
            else:
                j1, j2 = ip1div2 - 1 - ll - roundf - 1, ip1div2 + ll - 1 + ceilf - 1
        else:
            j1, j2 = ip1div2 - 1 - ll - 1, ip1div2 + ll + roundf - 1
    return max(j1 + 2, 0), min(j2, n)


def _kolmogn_Pomeranz(n, x):
    t = n * x
    ll = int(np.floor(t))
    f = 1.0 * (t - ll)
    g = min(f, 1.0 - f)
    ceilf = 1 if f > 0 else 0
    roundf = 1 if f > 0.5 else 0
    npwrs = 2 * (ll + 1)
    gpower = np.empty(npwrs)
    twogpower = np.empty(npwrs)
    onem2gpower = np.empty(npwrs)
    gpower[0] = twogpower[0] = onem2gpower[0] = 1.0
    expnt = 0
    g_over_n, two_g_over_n, one_minus_two_g_over_n = g / n, 2 * g / n, (1 - 2 * g) / n
    for m in range(1, npwrs):
        gpower[m] = gpower[m - 1] * g_over_n / m
        twogpower[m] = twogpower[m - 1] * two_g_over_n / m
        onem2gpower[m] = onem2gpower[m - 1] * one_minus_two_g_over_n / m
    V0 = np.zeros([npwrs])
    V1 = np.zeros([npwrs])
    V1[0] = 1
    V0s, V1s = 0, 0
    j1, j2 = _pomeranz_j1j2(0, n, ll, ceilf, roundf)
    for i in range(1, 2 * n + 2):
        k1 = j1
        V0, V1 = V1, V0
        V0s, V1s = V1s, V0s
        V1.fill(0.0)
        j1, j2 = _pomeranz_j1j2(i, n, ll, ceilf, roundf)
        if i == 1 or i == 2 * n + 1:
            pwrs = gpower
        else:
            pwrs = twogpower if i % 2 else onem2gpower
        ln2 = j2 - k1 + 1
        if ln2 > 0:
            conv = np.convolve(V0[k1 - V0s:k1 - V0s + ln2], pwrs[:ln2])
            conv_start = j1 - k1
            conv_len = j2 - j1 + 1
            V1[:conv_len] = conv[conv_start:conv_start + conv_len]
            if 0 < np.max(V1) < _EM128:
                V1 *= _EP128
                expnt -= _E128
            V1s = V0s + j1 - k1
    ans = V1[n - V1s]
    for m in range(1, n + 1):
        if np.abs(ans) > _EP128:
            ans *= _EM128
            expnt += _E128
        ans *= m
    if expnt != 0:
        ans = np.ldexp(ans, expnt)
    return _clip(ans)


def _kolmogn_PelzGood(n, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    z = np.sqrt(n) * x
    zsquared, zthree, zfour, zsix = z ** 2, z ** 3, z ** 4, z ** 6
    qlog = -_PI_SQUARED / 8 / zsquared
    if qlog < _MIN_LOG:
        return 0.0
    q = np.exp(qlog)
    k1a = -zsquared
    k1b = _PI_SQUARED / 4
    k2a = 6 * zsix + 2 * zfour
    k2b = (2 * zfour - 5 * zsquared) * _PI_SQUARED / 4
    k2c = _PI_FOUR * (1 - 2 * zsquared) / 16
    k3d = _PI_SIX * (5 - 30 * zsquared) / 64
    k3c = _PI_FOUR * (-60 * zsquared + 212 * zfour) / 16
    k3b = _PI_SQUARED * (135 * zfour - 96 * zsix) / 4
    k3a = -30 * zsix - 90 * z ** 8
    K0to3 = np.zeros(4)
    maxk = int(np.ceil(16 * z / np.pi))
    for k in range(maxk, 0, -1):
        m = 2 * k - 1
        msquared, mfour, msix = m ** 2, m ** 4, m ** 6
        qpower = np.power(q, 8 * k)
        coeffs = np.array([1.0, k1a + k1b * msquared, k2a + k2b * msquared + k2c * mfour,
                           k3a + k3b * msquared + k3c * mfour + k3d * msix])
        K0to3 *= qpower
        K0to3 += coeffs
    K0to3 *= q
    K0to3 *= _SQRT2PI
    K0to3 /= np.array([z, 6 * zfour, 72 * z ** 7, 6480 * z ** 10])
    q = np.exp(-_PI_SQUARED / 2 / zsquared)
    ks = np.arange(maxk, 0, -1)
    ksquared = ks ** 2
    sqrt3z = _SQRT3 * z
    kspi = np.pi * ks
    qpwers = q ** ksquared
    k2extra = np.sum(ksquared * qpwers)
    k2extra *= _PI_SQUARED * _SQRT2PI / (-36 * zthree)
    K0to3[2] += k2extra
    k3extra = np.sum((sqrt3z + kspi) * (sqrt3z - kspi) * ksquared * qpwers)
    k3extra *= _PI_SQUARED * _SQRT2PI / (216 * zsix)
    K0to3[3] += k3extra
    powers_of_n = np.power(n * 1.0, np.arange(len(K0to3)) / 2.0)
    K0to3 /= powers_of_n
    return float(sum(K0to3))


def _smirnov(n, d):
    """One-sided exact Smirnov sf (Birnbaum & Tingey).  Only used in branches where the
    two-sided p-value is far below Spindle's 0.05 gate, so only its magnitude matters."""
    if d <= 0:
        return 1.0
    if d >= 1:
        return 0.0
    tot = 0.0
    lg_n1 = math.lgamma(n + 1)
    for j in range(int(math.floor(n * (1 - d))) + 1):
        a = 1 - d - j / n
        b = d + j / n
        if a <= 0:
            continue
        lt = lg_n1 - math.lgamma(j + 1) - math.lgamma(n - j + 1) + (n - j) * math.log(a) + (j - 1) * math.log(b)
        tot += math.exp(lt)
    return min(max(d * tot, 0.0), 1.0)


def kstwo_sf(d: float, n: int) -> float:
    """scipy.stats.kstwo.sf(d, n) (scipy/stats/_ksstats.py::_kolmogn, cdf=False)."""
    if np.isnan(d):
        return np.nan
    # rv_continuous.sf support handling: support is (0.5/n, 1)
    if d <= 0.5 / n:
        return 1.0
    if d >= 1.0:
        return 0.0
    x = d
    t = n * x
    if t <= 1.0:
        if t <= 0.5:
            return 1.0
        if n <= 140:
            prob = np.prod(np.arange(1, n + 1) * (1.0 / n) * (2 * t - 1))
        else:
            rn = 1.0 / n
            lf = np.log(n) / 2 - n + _LOG_2PI / 2 + rn * np.polyval(_STIRLING, rn / n)
            prob = np.exp(lf + n * np.log(2 * t - 1))
        return _clip(1.0 - prob)
    if t >= n - 1:
        return _clip(2 * (1.0 - x) ** n)
    if x >= 0.5:
        return _clip(2 * _smirnov(n, x))
    nxsquared = t * x
    if n <= 140:
        if nxsquared <= 0.754693:
            return _clip(1.0 - _kolmogn_DMTW(n, x))
        if nxsquared <= 4:
            return _clip(1.0 - _kolmogn_Pomeranz(n, x))
        return _clip(2 * _smirnov(n, x))
    if nxsquared >= 370.0:
        return 0.0
    if nxsquared >= 2.2:
        return _clip(2 * _smirnov(n, x))
    if nxsquared >= 18.0:
        cdfprob = 1.0
    elif n <= 100000 and n * x ** 1.5 <= 1.4:
        cdfprob = _kolmogn_DMTW(n, x)
    else:
        cdfprob = _kolmogn_PelzGood(n, x)
    return _clip(1.0 - cdfprob)


# --- fits ------------------------------------------------------------------

class _FitError(Exception):
    pass


_LOGXMAX = np.log(np.finfo(float).max)
_RTOL = 4 * np.finfo(float).eps


def _lognorm_logpdf(x, s):
    return -np.log(x) ** 2 / (2 * s ** 2) - np.log(s * x * np.sqrt(2 * np.pi))


def _lognorm_nnlf(theta, data):
    """rv_continuous.nnlf for lognorm (non-penalised)."""
    s, loc, scale = theta
    if not (s > 0) or scale <= 0:
        return np.inf
    x = (np.asarray(data) - loc) / scale
    n_log_scale = len(x) * np.log(scale)
    if np.any(~((0 < x) & (x < np.inf))):
        return np.inf
    return -np.sum(_lognorm_logpdf(x, s), axis=0) + n_log_scale


def _lognorm_penalized_nnlf(theta, data):
    s, loc, scale = theta
    if not (s > 0) or scale <= 0:
        return np.inf
    x = np.asarray((data - loc) / scale)
    n_log_scale = len(x) * np.log(scale)
    with np.errstate(invalid="ignore"):
        cond0 = ~((0 < x) & (x < np.inf))
    n_bad = np.count_nonzero(cond0, axis=0)
    if n_bad > 0:
        x = x[~cond0]
    with np.errstate(divide="ignore", invalid="ignore"):
        logff = _lognorm_logpdf(x, s)
    finite = np.isfinite(logff)
    n_bad += np.sum(~finite, axis=0)
    if n_bad > 0:
        return -np.sum(logff[finite], axis=0) + n_bad * np.log(np.finfo(float).max) * 100 + n_log_scale
    return -np.sum(logff, axis=0) + n_log_scale


class _MaxFun(Exception):
    pass


def _nelder_mead(func, x0, data, xatol=1e-4, fatol=1e-4):
    """scipy.optimize.fmin (_minimize_neldermead, non-adaptive, no bounds)."""
    x0 = np.asarray(np.atleast_1d(x0).flatten(), dtype=np.float64)
    rho, chi, psi, sigma = 1, 2, 0.5, 0.5
    nonzdelt, zdelt = 0.05, 0.00025
    N = len(x0)
    sim = np.empty((N + 1, N), dtype=x0.dtype)
    sim[0] = x0
    for k in range(N):
        y = np.array(x0, copy=True)
        y[k] = (1 + nonzdelt) * y[k] if y[k] != 0 else zdelt
        sim[k + 1] = y
    maxiter = maxfun = N * 200
    ncalls = [0]

    def f(x):
        if ncalls[0] >= maxfun:
            raise _MaxFun
        ncalls[0] += 1
        fx = func(np.copy(x), data)
        if not np.isscalar(fx):
            fx = np.asarray(fx).item()
        return fx

    one2np1 = list(range(1, N + 1))
    fsim = np.full((N + 1,), np.inf, dtype=float)
    try:
        for k in range(N + 1):
            fsim[k] = f(sim[k])
    except _MaxFun:
        pass
    finally:
        ind = np.argsort(fsim)
        sim = np.take(sim, ind, 0)
        fsim = np.take(fsim, ind, 0)
    ind = np.argsort(fsim)
    fsim = np.take(fsim, ind, 0)
    sim = np.take(sim, ind, 0)
    iterations = 1
    while ncalls[0] < maxfun and iterations < maxiter:
        try:
            if (np.max(np.ravel(np.abs(sim[1:] - sim[0]))) <= xatol and
                    np.max(np.abs(fsim[0] - fsim[1:])) <= fatol):
                break
            xbar = np.add.reduce(sim[:-1], 0) / N
            xr = (1 + rho) * xbar - rho * sim[-1]
            fxr = f(xr)
            doshrink = 0
            if fxr < fsim[0]:
                xe = (1 + rho * chi) * xbar - rho * chi * sim[-1]
                fxe = f(xe)
                if fxe < fxr:
                    sim[-1] = xe
                    fsim[-1] = fxe
                else:
                    sim[-1] = xr
                    fsim[-1] = fxr
            else:
                if fxr < fsim[-2]:
                    sim[-1] = xr
                    fsim[-1] = fxr
                else:
                    if fxr < fsim[-1]:
                        xc = (1 + psi * rho) * xbar - psi * rho * sim[-1]
                        fxc = f(xc)
                        if fxc <= fxr:
                            sim[-1] = xc
                            fsim[-1] = fxc
                        else:
                            doshrink = 1
                    else:
                        xcc = (1 - psi) * xbar + psi * sim[-1]
                        fxcc = f(xcc)
                        if fxcc < fsim[-1]:
                            sim[-1] = xcc
                            fsim[-1] = fxcc
                        else:
                            doshrink = 1
                    if doshrink:
                        for j in one2np1:
                            sim[j] = sim[0] + sigma * (sim[j] - sim[0])
                            fsim[j] = f(sim[j])
            iterations += 1
        except _MaxFun:
            pass
        ind = np.argsort(fsim)
        sim = np.take(sim, ind, 0)
        fsim = np.take(fsim, ind, 0)
    return sim[0]


def _lognorm_generic_fit(data):
    """rv_continuous.fit (MLE, fmin) for lognorm, incl. _fitstart."""
    # _fitstart -> _fit_loc_scale_support(data, 1.0)
    p = np.exp(1.0 * 1.0)
    mu, mu2 = np.sqrt(p), p * (p - 1)
    muhat = data.mean()
    mu2hat = data.var()
    Shat = np.sqrt(mu2hat / mu2)
    with np.errstate(invalid="ignore"):
        Lhat = muhat - Shat * mu
    if not np.isfinite(Lhat):
        Lhat = 0
    if not (np.isfinite(Shat) and (0 < Shat)):
        Shat = 1
    loc_hat, scale_hat = Lhat, Shat
    data_a, data_b = np.min(data), np.max(data)
    a_hat = loc_hat + 0.0 * scale_hat
    b_hat = loc_hat + np.inf * scale_hat
    if not (a_hat < data_a and data_b < b_hat):
        margin = (data_b - data_a) * 0.1
        loc_hat, scale_hat = (data_a - 0.0) - margin, 1
    x0 = [1.0, loc_hat, scale_hat]
    with np.errstate(all="ignore"):
        vals = _nelder_mead(_lognorm_penalized_nnlf, x0, data)
        _lognorm_penalized_nnlf(vals, data)  # scipy evaluates obj once more
    vals = tuple(vals)
    s, loc, scale = vals
    if not (s > 0 and scale > 0):
        raise _FitError("outside range")
    return vals


def _brentq(f, xa, xb, xtol=2e-12, rtol=_RTOL, maxiter=100):
    """scipy.optimize.brentq (zeros.c) incl. the NaN-raising wrapper."""
    def fw(x):
        fx = f(x)
        if np.isnan(fx):
            raise ValueError("NaN")
        return fx

    xpre, xcur = xa, xb
    xblk = fblk = spre = scur = 0.0
    fpre = fw(xpre)
    fcur = fw(xcur)
    if fpre == 0:
        return xpre, True
    if fcur == 0:
        return xcur, True
    if math.copysign(1, fpre) == math.copysign(1, fcur):
        raise ValueError("f(a) and f(b) must have different signs")
    for _ in range(maxiter):
        if fpre != 0 and fcur != 0 and math.copysign(1, fpre) != math.copysign(1, fcur):
            xblk, fblk = xpre, fpre
            spre = scur = xcur - xpre
        if abs(fblk) < abs(fcur):
            xpre, xcur, xblk = xcur, xblk, xcur
            fpre, fcur, fblk = fcur, fblk, fcur
        delta = (xtol + rtol * abs(xcur)) / 2
        sbis = (xblk - xcur) / 2
        if fcur == 0 or abs(sbis) < delta:
            return xcur, True
        if abs(spre) > delta and abs(fcur) < abs(fpre):
            if xpre == xblk:
                stry = -fcur * (xcur - xpre) / (fcur - fpre)
            else:
                dpre = (fpre - fcur) / (xpre - xcur)
                dblk = (fblk - fcur) / (xblk - xcur)
                stry = -fcur * (fblk * dblk - fpre * dpre) / (dblk * dpre * (fblk - fpre))
            if 2 * abs(stry) < min(abs(spre), 3 * abs(sbis) - delta):
                spre, scur = scur, stry
            else:
                spre = scur = sbis
        else:
            spre = scur = sbis
        xpre, fpre = xcur, fcur
        if abs(scur) > delta:
            xcur += scur
        else:
            xcur += delta if sbis > 0 else -delta
        fcur = fw(xcur)
    return xcur, False


def _lognorm_fit(data):
    """scipy.stats.lognorm.fit(data) (scipy 1.17 override, loc free)."""
    if not np.isfinite(data).all():
        raise _FitError("non-finite")
    data_min = np.min(data)

    def get_shape_scale(loc):
        lndata = np.log(data - loc)
        scale = np.exp(lndata.mean())
        shape = np.sqrt(np.mean((lndata - np.log(scale)) ** 2))
        return shape, scale

    def dL_dLoc(loc):
        shape, scale = get_shape_scale(loc)
        shifted = data - loc
        return np.sum((1 + np.log(shifted / scale) / shape ** 2) / shifted)

    def ll(loc):
        shape, scale = get_shape_scale(loc)
        return -_lognorm_nnlf((shape, loc, scale), data)

    with np.errstate(all="ignore"):
        spacing = np.spacing(data_min)
        rbrack = data_min - spacing
        dL_dLoc_rbrack = dL_dLoc(rbrack)
        ll_rbrack = ll(rbrack)
        delta = 2 * spacing
        while dL_dLoc_rbrack >= -1e-6:
            rbrack = data_min - delta
            dL_dLoc_rbrack = dL_dLoc(rbrack)
            delta *= 2
        if not np.isfinite(rbrack) or not np.isfinite(dL_dLoc_rbrack):
            return _lognorm_generic_fit(data)
        lbrack = np.minimum(np.nextafter(rbrack, -np.inf), rbrack - 1)
        dL_dLoc_lbrack = dL_dLoc(lbrack)
        delta = 2 * (rbrack - lbrack)
        while (np.isfinite(lbrack) and np.isfinite(dL_dLoc_lbrack)
               and np.sign(dL_dLoc_lbrack) == np.sign(dL_dLoc_rbrack)):
            lbrack = rbrack - delta
            dL_dLoc_lbrack = dL_dLoc(lbrack)
            delta *= 2
        if not np.isfinite(lbrack) or not np.isfinite(dL_dLoc_lbrack):
            return _lognorm_generic_fit(data)
        root, converged = _brentq(dL_dLoc, lbrack, rbrack)
        if not converged:
            return _lognorm_generic_fit(data)
        ll_root = ll(root)
        loc = root if ll_root > ll_rbrack else data_min - spacing
        shape, scale = get_shape_scale(loc)
    if not (shape > 0 and scale > 0):
        return _lognorm_generic_fit(data)
    return shape, loc, scale


def fit(name: str, data: np.ndarray) -> tuple:
    if name == "normal":
        if not np.isfinite(data).all():
            raise _FitError("non-finite")
        loc = data.mean()
        return loc, np.sqrt(((data - loc) ** 2).mean())
    if name == "uniform":
        if not np.isfinite(data).all():
            raise _FitError("non-finite")
        return float(data.min()), float(np.ptp(data))
    if name == "exponential":
        if not np.isfinite(data).all():
            raise _FitError("non-finite")
        loc = data.min()
        return float(loc), float(data.mean() - loc)
    return _lognorm_fit(data)


_CANDIDATES = ("normal", "uniform", "exponential", "lognormal")


def _param_dict(name, params):
    if name == "lognormal":
        return {"s": float(params[0]), "loc": float(params[1]), "scale": float(params[2])}
    return {"loc": float(params[0]), "scale": float(params[1])}


def detect_distribution(values: np.ndarray):
    """DataProfiler._detect_distribution."""
    if len(values) > 2000:
        values = np.random.default_rng(42).choice(values, size=2000, replace=False)
    if len(values) < 20:
        return None, None
    xs = np.sort(values)
    best_name, best_stat, best_params = None, float("inf"), None
    for name in _CANDIDATES:
        try:
            with np.errstate(all="ignore"):
                params = fit(name, values)
                d = _ks_stat_sorted(xs, name, params)
                p = kstwo_sf(d, len(values))
            if p > 0.05 and d < best_stat:
                best_name, best_stat, best_params = name, d, _param_dict(name, params)
        except (_FitError, ValueError, FloatingPointError, ZeroDivisionError):
            continue
    return best_name, best_params


# ---------------------------------------------------------------------------
# helpers mirroring pandas behaviour
# ---------------------------------------------------------------------------

_EMAIL_RE = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
_PHONE_RE = r"^[\+]?[\d\s\-\(\)\.]{7,20}$"
_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_DATE_RE = r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$"
_SSN_RE = r"^\d{3}-\d{2}-\d{4}$"
_OCT = r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
_IP_V4_RE = rf"^{_OCT}\.{_OCT}\.{_OCT}\.{_OCT}$"
_IP_V6_RE = (r"^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$"
             r"|^(?:[0-9a-fA-F]{1,4}:){1,7}:$"
             r"|^:(?::[0-9a-fA-F]{1,4}){1,7}$"
             r"|^(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$"
             r"|^::(?:[fF]{4}:){0,1}\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
             r"|^::$")
_MAC_RE = r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$|^([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}$"
_CURRENCY_CODE_RE = r"^[A-Z]{3}$"
_LANGUAGE_CODE_RE = r"^[a-z]{2}(-[A-Z]{2})?$"
_IBAN_RE = r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$"
_POSTAL_US_RE = r"^\d{5}(-\d{4})?$"


def _pandas_fullmatch_pattern(pat: str) -> str:
    """ArrowStringArrayMixin._str_fullmatch -> _str_match pattern rewriting (pandas 3.0)."""
    if (not pat.endswith("$") or pat.endswith("\\$")) and not pat.startswith("^"):
        pat = f"^({pat})$"
    elif not pat.endswith("$") or pat.endswith("\\$"):
        pat = f"^({pat[1:]})$"
    elif not pat.startswith("^"):
        pat = f"^({pat[0:-1]})$"
    if pat.startswith("^"):
        pat = pat[1:]
    return f"^({pat})"


_PATTERNS = {k: _pandas_fullmatch_pattern(v) for k, v in {
    "email": _EMAIL_RE, "uuid": _UUID_RE, "ssn": _SSN_RE, "mac": _MAC_RE, "ipv4": _IP_V4_RE,
    "ipv6": _IP_V6_RE, "iban": _IBAN_RE, "postal": _POSTAL_US_RE, "date": _DATE_RE,
    "phone": _PHONE_RE, "currency": _CURRENCY_CODE_RE, "language": _LANGUAGE_CODE_RE}.items()}


def detect_pattern(non_null: pa.Array, cardinality: int) -> str | None:
    """DataProfiler._detect_pattern on a non-null string array."""
    n = len(non_null)
    if n == 0:
        return None
    sample = non_null
    if n > 1000:
        idx = np.random.RandomState(42).choice(n, size=1000, replace=False)
        sample = non_null.take(pa.array(idx))
    total = len(sample)
    thr = 0.9

    def rate(key):
        return pc.sum(pc.match_substring_regex(sample, _PATTERNS[key])).as_py() / total

    if rate("email") >= thr:
        return "email"
    if rate("uuid") >= thr:
        return "uuid"
    if rate("ssn") >= thr:
        return "ssn"
    if rate("mac") >= thr:
        return "mac_address"
    v4, v6 = rate("ipv4"), rate("ipv6")
    if v4 >= thr or v6 >= thr:
        return "ip_address"
    if rate("iban") >= thr:
        return "iban"
    if rate("postal") >= thr:
        return "postal_code"
    if rate("date") >= thr:
        return "date"
    if rate("phone") >= thr:
        return "phone"
    if rate("currency") >= thr and cardinality <= 200:
        return "currency_code"
    if rate("language") >= thr and cardinality <= 200:
        return "language_code"
    return None


def _np_percentile(a, q):
    return np.percentile(a, q)


def _linear_index(n: int, qs):
    """numpy 'linear' method: virtual index (n-1)*q, _get_indexes bounds handling."""
    q = np.true_divide(np.asarray(qs, dtype=np.float64), 100)
    vi = (n - 1) * q
    prev = np.floor(vi)
    nxt = prev + 1
    above = vi >= n - 1
    prev[above] = -1
    nxt[above] = -1
    below = vi < 0
    prev[below] = 0
    nxt[below] = 0
    prev = prev.astype(np.intp)
    nxt = nxt.astype(np.intp)
    gamma = np.asarray(vi - prev, dtype=vi.dtype)
    return prev, nxt, gamma


def _lerp(a, b, t):
    diff = b - a
    res = np.add(a, diff * t)
    np.subtract(b, diff * (1 - t), out=res, where=t >= 0.5, casting="unsafe", dtype=res.dtype)
    return res


def _percentile_sorted(sorted_a: np.ndarray, qs) -> np.ndarray:
    """np.percentile(data, qs) (linear) evaluated on the already-sorted data: identical
    virtual indices, neighbours and _lerp, hence bitwise-identical results."""
    prev, nxt, gamma = _linear_index(sorted_a.shape[0], qs)
    return _lerp(sorted_a[prev], sorted_a[nxt], gamma)


_PCTS = [1, 5, 10, 25, 50, 75, 90, 95, 99]


def _keys_py(values: pa.Array, kind: str) -> list[str]:
    """str(k) for the keys of pandas' value_counts index."""
    if kind in ("bool", "objbool"):
        return ["True" if v else "False" for v in values.to_pylist()]
    if kind == "int":
        return [str(v) for v in values.to_pylist()]
    if kind == "float":
        return [str(float(v)) for v in values.to_numpy(zero_copy_only=False).tolist()]
    if kind == "dt64":
        return pc.strftime(pc.cast(values, pa.timestamp("s")), format="%Y-%m-%d %H:%M:%S").to_pylist() \
            if _no_subsecond(values) \
            else [str(_to_timestamp(v)) for v in values.to_pylist()]
    if kind == "objdate":
        return pc.strftime(values, format="%Y-%m-%d").to_pylist()
    return values.to_pylist()


def _no_subsecond(ts: pa.Array) -> bool:
    unit = ts.type.unit
    if unit == "s":
        return True
    mult = {"ms": 1000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    ints = pc.cast(ts, pa.int64()).to_numpy(zero_copy_only=False)
    return not np.any(ints % mult)


def _to_timestamp(v) -> Timestamp:
    return Timestamp(v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond)


def _round6(arr: np.ndarray) -> list[float]:
    return [round(float(v), 6) for v in arr.tolist()]


# ---------------------------------------------------------------------------
# datetime handling for strings / objects
# ---------------------------------------------------------------------------

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}([ T])\d{2}:\d{2}:\d{2}$")
_ISO_DT_FRAC = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d+$")
_EXTRA_FORMATS = ["%Y/%m/%d", "%m/%d/%Y", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d-%b-%Y",
                  "%b %d %Y", "%d %b %Y"]


def _all_parse_datetime(uniques: pa.Array) -> bool:
    """Port of `pd.to_datetime(values, format="mixed")` succeeding.  Supported:
    ISO-8601 (anything Arrow's string->timestamp cast accepts) plus a list of common
    explicit formats.  dateutil-only spellings are NOT recognised (documented)."""
    try:
        pc.cast(uniques, pa.timestamp("ns"))
        return True
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
        pass
    for fmt in _EXTRA_FORMATS:
        try:
            pc.strptime(uniques, format=fmt, unit="ns")
            return True
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            continue
    return False


def _coerce_datetime_strings(arr: pa.Array) -> pa.Array:
    """pd.to_datetime(series, errors="coerce"): format guessed from the first element,
    non-matching elements become NaT (dropped by the callers)."""
    if len(arr) == 0:
        return pa.array([], pa.timestamp("ns"))
    first = arr[0].as_py()
    if _ISO_DATE.match(first):
        fmt = "%Y-%m-%d"
    elif (m := _ISO_DT.match(first)):
        fmt = f"%Y-%m-%d{m.group(1)}%H:%M:%S"
    elif _ISO_DT_FRAC.match(first):
        return pc.drop_null(pc.cast(arr, pa.timestamp("ns"), safe=False)) if True else None
    else:
        for f in _EXTRA_FORMATS:
            try:
                pc.strptime(pa.array([first]), format=f, unit="ns")
                fmt = f
                break
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                continue
        else:
            try:
                return pc.drop_null(pc.cast(arr, pa.timestamp("ns")))
            except pa.ArrowInvalid:
                return pa.array([], pa.timestamp("ns"))
    return pc.drop_null(pc.strptime(arr, format=fmt, unit="s", error_is_null=True))


# ---------------------------------------------------------------------------
# per-column profiling
# ---------------------------------------------------------------------------

@dataclass
class _Work:
    """Intermediate per-column state kept for PK/FK/correlation."""
    col: _Col
    prof: ColumnProfile
    uniques: Any = None  # pa.Array of distinct non-null values


def _combine(arr):
    if isinstance(arr, pa.ChunkedArray):
        return arr.combine_chunks() if arr.num_chunks != 1 else arr.chunk(0)
    return arr


def _profile_column(c: _Col, row_count: int, top_n: int = 500, iqr_factor: float = 1.5) -> _Work:
    kind = c.kind
    arr = c.arr

    # ---- non-null values ---------------------------------------------------
    if kind == "float":
        a = _combine(arr)
        fvals = a.to_numpy(zero_copy_only=False)  # nulls -> NaN
        nan_mask = np.isnan(fvals)
        null_count = int(nan_mask.sum())
        nn_np = fvals[~nan_mask] if null_count else fvals
        nn_np = nn_np + 0.0  # pandas hashes -0.0 == 0.0
        non_null = pa.array(nn_np)
    elif kind == "nullobj":
        null_count = len(arr)
        non_null = pa.array([], pa.string())
        nn_np = None
    else:
        null_count = arr.null_count
        non_null = _combine(pc.drop_null(arr) if null_count else arr)
        nn_np = None
    n_nn = len(non_null)
    null_rate = null_count / row_count if row_count > 0 else 0.0

    # ---- value counts (pandas: hashtable in first-appearance order, stable desc sort)
    if n_nn:
        vc = pc.value_counts(non_null)
        uniq = vc.field("values")
        counts = vc.field("counts").to_numpy()
    else:
        uniq = non_null
        counts = np.zeros(0, np.int64)
    cardinality = len(uniq)
    cardinality_ratio = cardinality / row_count if row_count > 0 else 0.0
    is_unique = cardinality == row_count and null_count == 0
    is_enum = (cardinality < 200 or (cardinality_ratio < 0.30 and cardinality < 50_000)) and cardinality > 0

    # ---- spindle type -------------------------------------------------------
    numeric = None  # float64 numpy array of numeric values (row order)
    dt_values = None  # pa timestamp array of parsed datetimes (row order, NaT dropped)
    if kind == "bool" or kind == "objbool":
        stype = "boolean"
    elif kind == "int":
        stype = "integer"
        numeric = non_null.to_numpy().astype(np.float64)
    elif kind == "float":
        if n_nn and np.all(nn_np == nn_np.astype(np.int64)):
            stype = "integer"
        else:
            stype = "float"
        numeric = nn_np
    elif kind == "dt64":
        if n_nn:
            ints = pc.cast(non_null, pa.int64()).to_numpy()
            per_day = {"s": 86400, "ms": 86400_000, "us": 86400_000_000, "ns": 86400_000_000_000}[non_null.type.unit]
            stype = "date" if not np.any(ints % per_day) else "datetime"
        else:
            stype = "datetime"
        dt_values = non_null
    elif kind == "objdate":
        stype = "string" if n_nn == 0 else "datetime"
        dt_values = pc.cast(non_null, pa.timestamp("s")) if n_nn else None
    elif kind == "nullobj":
        stype = "string"
    else:  # str
        stype = "string"
        if n_nn:
            lower = pc.utf8_lower(uniq)
            if pc.all(pc.is_in(lower, value_set=pa.array(["true", "false", "0", "1", "yes", "no"]))).as_py():
                stype = "boolean"
            else:
                try:
                    uvals = pc.cast(uniq, pa.float64())
                    ok = True
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                    ok = False
                if ok:
                    u = uvals.to_numpy()
                    stype = "integer" if np.all(u == u.astype(np.int64)) else "float"
                    numeric = pc.cast(non_null, pa.float64()).to_numpy()
                elif _all_parse_datetime(uniq):
                    stype = "datetime"
                    dt_values = _coerce_datetime_strings(non_null)

    # ---- enum + value_counts_ext ------------------------------------------
    enum_values = None
    value_counts_ext = None
    if n_nn:
        order = np.argsort(-counts, kind="stable")
        need = cardinality if is_enum else min(top_n, cardinality)
        top = order[:need]
        keys = _keys_py(uniq.take(pa.array(top)), kind)
        props = counts[top] / n_nn
        rounded = _round6(props)
        if is_enum:
            enum_values = dict(zip(keys, rounded))
        value_counts_ext = dict(zip(keys[:top_n], rounded[:top_n]))

    # ---- min / max (pandas types) ------------------------------------------
    min_value = max_value = None
    if n_nn:
        if kind == "float":
            min_value, max_value = float(nn_np.min()), float(nn_np.max())
        else:
            mm = pc.min_max(non_null)
            lo, hi = mm["min"].as_py(), mm["max"].as_py()
            if kind == "dt64":
                lo, hi = _to_timestamp(lo), _to_timestamp(hi)
            min_value, max_value = lo, hi

    # ---- numeric stats / distribution / quantiles ---------------------------
    mean_val = std_val = None
    dist_name = dist_params = None
    quantiles = None
    outlier_rate_val = None
    fit_score_val = None
    if stype in ("integer", "float") and numeric is not None and len(numeric) > 0:
        numeric = numeric.astype(np.float64, copy=False)
        cnt = len(numeric)
        s = numeric.sum(dtype=np.float64)
        mean_val = float(s / cnt)
        if cnt > 1:
            avg = s / cnt
            std_val = float(np.sqrt(((numeric - avg) ** 2).sum() / (cnt - 1)))
        else:
            std_val = float("nan")
        dist_name, dist_params = detect_distribution(numeric)
        xs = None
        if cnt >= 4:
            xs = np.sort(numeric)
            vals = _percentile_sorted(xs, _PCTS + [0.5, 99.5])
            quantiles = {f"p{p}": round(float(v), 6) for p, v in zip(_PCTS, vals[:9])}
            quantiles["p0_5"] = round(float(vals[9]), 6)
            quantiles["p99_5"] = round(float(vals[10]), 6)
            q1, q3 = vals[3], vals[5]
            iqr = q3 - q1
            if iqr == 0:
                outlier_rate_val = 0.0
            else:
                lo_f = q1 - iqr_factor * iqr
                hi_f = q3 + iqr_factor * iqr
                n_out = int(np.searchsorted(xs, lo_f, "left") + (cnt - np.searchsorted(xs, hi_f, "right")))
                outlier_rate_val = round(n_out / cnt, 6)
        if dist_name is not None and cnt >= 20:
            try:
                with np.errstate(all="ignore"):
                    params = fit(dist_name, numeric)
                    if xs is None:
                        xs = np.sort(numeric)
                    d = _ks_stat_sorted(xs, dist_name, params)
                fit_score_val = round(1.0 - d, 4)
            except (_FitError, ValueError, FloatingPointError, ZeroDivisionError):
                pass

    # ---- strings -------------------------------------------------------------
    pattern = None
    string_length = None
    if stype == "string" and n_nn:
        pattern = detect_pattern(non_null, cardinality)
        lens = pc.utf8_length(non_null).to_numpy()
        string_length = {
            "min": float(lens.min()),
            "mean": round(float(lens.sum(dtype=np.float64) / len(lens)), 2),
            "max": float(lens.max()),
            "p95": float(np.percentile(lens, 95)),
        }

    # ---- temporal ------------------------------------------------------------
    hour_h = dow_h = temporal = None
    if stype in ("date", "datetime") and n_nn:
        ts = dt_values
        if ts is None or len(ts) == 0:
            hour_h = [1.0 / 24] * 24
            dow_h = [1.0 / 7] * 7
        else:
            hours = pc.hour(ts).to_numpy()
            hc = np.bincount(hours, minlength=24).astype(float)
            hour_h = _round6(hc / hc.sum())
            dows = pc.day_of_week(ts).to_numpy()
            dc = np.bincount(dows, minlength=7).astype(float)
            dow_h = _round6(dc / dc.sum())
            years = pc.year(ts).to_numpy().astype(int)
            months = pc.month(ts).to_numpy().astype(int)
            # np.percentile(years, [1, 99]) from the year histogram (exact same values)
            y0 = years.min()
            ycounts = np.bincount(years - y0)
            lo_year = int(_pct_from_counts(ycounts, y0, 1))
            hi_year = int(_pct_from_counts(ycounts, y0, 99))
            if hi_year < lo_year:
                hi_year = lo_year
            span = hi_year - lo_year + 1
            yr = np.bincount(np.clip(years - lo_year, 0, span - 1), minlength=span).astype(float)
            mc = np.bincount(months - 1, minlength=12).astype(float)
            temporal = {
                "lo_year": lo_year, "hi_year": hi_year,
                "year_weights": _round6(yr / yr.sum()),
                "month_weights": _round6(mc / mc.sum()),
            } or None

    prof = ColumnProfile(
        name=c.name, dtype=stype, null_count=null_count, null_rate=round(null_rate, 6),
        cardinality=cardinality, cardinality_ratio=round(cardinality_ratio, 6), is_unique=is_unique,
        is_enum=is_enum, enum_values=enum_values, min_value=min_value, max_value=max_value,
        mean=mean_val, std=std_val, distribution=dist_name, distribution_params=dist_params,
        pattern=pattern, is_primary_key=False, is_foreign_key=False, fk_ref_table=None,
        quantiles=quantiles, hour_histogram=hour_h, dow_histogram=dow_h, temporal_histogram=temporal,
        string_length=string_length, outlier_rate=outlier_rate_val, value_counts_ext=value_counts_ext,
        fit_score=fit_score_val,
    )
    return _Work(col=c, prof=prof, uniques=uniq)


def _pct_from_counts(counts: np.ndarray, offset: int, q: float) -> float:
    """np.percentile(int_values, q) computed from a histogram of the integer values (same
    virtual index arithmetic and _lerp as numpy, so the result is identical)."""
    n = int(counts.sum())
    prev, nxt, gamma = _linear_index(n, [q])
    cum = np.cumsum(counts)
    idx = np.where(np.concatenate([prev, nxt]) < 0, n - 1, np.concatenate([prev, nxt]))
    vals = np.searchsorted(cum, idx, side="right") + offset
    return float(_lerp(vals[:1], vals[1:], gamma)[0])


# ---------------------------------------------------------------------------
# table-level: PK, FK, correlation
# ---------------------------------------------------------------------------

_PK_NAMES = ("id", "_id", "pk", "key")


def _detect_primary_key(works: list[_Work], row_count: int) -> list[str]:
    cands = []
    for w in works:
        p = w.prof
        if p.null_count > 0 or p.cardinality != row_count:
            continue
        if p.dtype == "integer":
            cands.append(p.name)
        elif p.dtype == "string" and p.pattern == "uuid":
            cands.append(p.name)
    for c in cands:
        lower = c.lower()
        if any(lower == pn or lower.endswith(pn) for pn in _PK_NAMES):
            return [c]
    return cands[:1]


def _correlation(works: list[_Work], row_count: int) -> dict:
    """DataFrame.select_dtypes(np.number).corr('pearson') -> nested dict (round 4)."""
    cols = [w for w in works if w.col.kind in ("int", "float")]
    if len(cols) < 2:
        return {}
    k = len(cols)
    X = np.empty((row_count, k), dtype=np.float64)
    for j, w in enumerate(cols):
        a = _combine(w.col.arr)
        X[:, j] = a.to_numpy(zero_copy_only=False).astype(np.float64, copy=False)
    M = ~np.isnan(X)
    any_null = not M.all()
    Mf = M.astype(np.float64)
    cnt = Mf.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        gmean = np.where(cnt > 0, np.nansum(X, axis=0) / np.where(cnt > 0, cnt, 1), 0.0)
    X0 = np.where(M, X - gmean, 0.0)
    Sxy = X0.T @ X0
    if any_null:
        N = Mf.T @ Mf                      # pair counts
        Sx = X0.T @ Mf                     # Sx[a,b] = sum x_a over rows where b present (and a)
        Sxx = (X0 * X0).T @ Mf
    else:
        N = np.full((k, k), float(row_count))
        s = X0.sum(axis=0)
        Sx = np.repeat(s[:, None], k, axis=1)
        sq = (X0 * X0).sum(axis=0)
        Sxx = np.repeat(sq[:, None], k, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cov = Sxy - Sx * Sx.T / N
        va = Sxx - Sx * Sx / N
        vb = va.T
        va = np.maximum(va, 0.0)
        vb = np.maximum(vb, 0.0)
        div = np.sqrt(va * vb)
        r = np.where((div != 0) & (N >= 1), cov / div, np.nan)
    # pandas' Welford gives exactly 0 variance for constant data; guard tiny residue
    names = [w.col.name for w in cols]
    out: dict[str, dict[str, float]] = {}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i != j:
                v = r[i, j]
                if not np.isnan(v):
                    out.setdefault(a, {})[b] = round(float(v), 4)
    return out


def _fk_values(w: _Work) -> pa.Array:
    u = w.uniques
    if w.col.kind in ("int", "float", "bool", "objbool"):
        return pc.cast(u, pa.float64())
    return u


def _detect_fks(tname: str, works: list[_Work], all_works: dict[str, list[_Work]],
                pks: dict[str, list[str]], threshold: float = 0.9) -> dict[str, str]:
    if not all_works:
        return {}
    out = {}
    for w in works:
        col = w.col.name
        lower = col.lower()
        if not lower.endswith("_id"):
            continue
        cand = lower.rsplit("_id", 1)[0]
        parent = None
        for t in all_works:
            if t.lower() == cand:
                parent = t
                break
        if parent is None or parent == tname:
            continue
        ppk = pks[parent]
        if not ppk:
            continue
        pw = next(x for x in all_works[parent] if x.col.name == ppk[0])
        child_vals = _fk_values(w)
        if len(child_vals) == 0:
            continue
        parent_vals = _fk_values(pw)
        if child_vals.type != parent_vals.type:
            overlap = 0.0
        else:
            overlap = pc.sum(pc.is_in(child_vals, value_set=parent_vals)).as_py() / len(child_vals)
        if overlap >= threshold:
            out[col] = parent
    return out


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def _profile_cols(cols: list[_Col], row_count: int, threads: int | None) -> list[_Work]:
    n = _n_threads(threads)
    if n == 1:
        return [_profile_column(c, row_count) for c in cols]
    # biggest work first (strings / numerics with fitting) for better packing
    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(lambda c: _profile_column(c, row_count), cols))


def _sample_rows(cols: list[_Col], row_count: int, sample_rows: int | None):
    if sample_rows is None or row_count <= sample_rows:
        return cols, row_count
    idx = pa.array(np.random.RandomState(42).choice(row_count, size=sample_rows, replace=False))
    return [_Col(c.name, c.kind, c.arr.take(idx)) for c in cols], sample_rows


def _finish_table(name: str, works: list[_Work], row_count: int, pk: list[str],
                  fks: dict[str, str]) -> TableProfile:
    columns = {}
    for w in works:
        p = w.prof
        p.is_primary_key = p.name in pk
        p.is_foreign_key = p.name in fks
        p.fk_ref_table = fks.get(p.name)
        columns[p.name] = p
    corr = _correlation(works, row_count)
    return TableProfile(name=name, row_count=row_count, columns=columns, primary_key=pk,
                        detected_fks=fks, correlation_matrix=corr if corr else None)


def _profile_cols_table(name, cols, row_count, threads, sample_rows=None) -> TableProfile:
    cols, row_count = _sample_rows(cols, row_count, sample_rows)
    works = _profile_cols(cols, row_count, threads)
    pk = _detect_primary_key(works, row_count)
    return _finish_table(name, works, row_count, pk, {})


def profile_csv(path, table_name: str | None = None, threads: int | None = None,
                sample_rows: int | None = None) -> TableProfile:
    """Equivalent of DataProfiler.from_csv(path)."""
    t = read_csv(path, threads)
    return _profile_cols_table(table_name or Path(path).stem, _csv_cols(t), t.num_rows, threads, sample_rows)


def profile_parquet(path, table_name: str | None = None, threads: int | None = None,
                    sample_rows: int | None = None) -> TableProfile:
    """Equivalent of DataProfiler().profile(pd.read_parquet(path), stem)."""
    n = _n_threads(threads)
    t = pq.read_table(path, use_threads=n != 1)
    return _profile_cols_table(table_name or Path(path).stem, _arrow_cols(t), t.num_rows, threads, sample_rows)


def profile_table(table: pa.Table, table_name: str = "table", threads: int | None = None,
                  sample_rows: int | None = None) -> TableProfile:
    """Equivalent of DataProfiler().profile(table.to_pandas(), table_name)."""
    return _profile_cols_table(table_name, _arrow_cols(table), table.num_rows, threads, sample_rows)


def profile_dataset(tables: dict[str, Any], threads: int | None = None) -> DatasetProfile:
    """Equivalent of DataProfiler().profile_dataset({name: df}).  Values may be pa.Table,
    or a path to a .csv / .parquet file (read with the matching pandas semantics)."""
    cols_by_t: dict[str, tuple[list[_Col], int]] = {}
    for name, t in tables.items():
        if isinstance(t, (str, Path)):
            if str(t).endswith(".csv"):
                tt = read_csv(t, threads)
                cols_by_t[name] = (_csv_cols(tt), tt.num_rows)
            else:
                tt = pq.read_table(t, use_threads=_n_threads(threads) != 1)
                cols_by_t[name] = (_arrow_cols(tt), tt.num_rows)
        else:
            cols_by_t[name] = (_arrow_cols(t), t.num_rows)
    works = {n: _profile_cols(c, rc, threads) for n, (c, rc) in cols_by_t.items()}
    pks = {n: _detect_primary_key(w, cols_by_t[n][1]) for n, w in works.items()}
    profiles = {}
    for n, w in works.items():
        fks = _detect_fks(n, w, works, pks)
        profiles[n] = _finish_table(n, w, cols_by_t[n][1], pks[n], fks)
    rels = []
    for n, tp in profiles.items():
        for col, parent in tp.detected_fks.items():
            rels.append({"name": f"fk_{n}_{col}", "parent": parent, "child": n,
                         "parent_columns": profiles[parent].primary_key, "child_columns": [col],
                         "type": "one_to_many"})
    return DatasetProfile(tables=profiles, relationships=rels)
