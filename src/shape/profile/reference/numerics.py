"""numpy re-implementations of the scipy routines used for distribution fitting."""

from __future__ import annotations

import math
from typing import Any, cast

import numpy as np

from ..sampling import FIT_SAMPLE_ROWS, FIT_SAMPLE_SEED

# scipy re-implementations (numpy only)
# ---------------------------------------------------------------------------

# --- special.ndtr (cephes ndtr.c coefficients) -----------------------------
_P = np.array(
    [
        2.46196981473530512524e-10,
        5.64189564831068821977e-1,
        7.46321056442269912687e0,
        4.86371970985681366614e1,
        1.96520832956077098242e2,
        5.26445194995477358631e2,
        9.34528527171957607540e2,
        1.02755188689515710272e3,
        5.57535335369399327526e2,
    ]
)
_Q = np.array(
    [
        1.32281951154744992508e1,
        8.67072140885989742329e1,
        3.54937778887819891062e2,
        9.75708501743205489753e2,
        1.82390916687909736289e3,
        2.24633760818710981792e3,
        1.65666309194161350182e3,
        5.57535340817727675546e2,
    ]
)
_R = np.array(
    [
        5.64189583547755073984e-1,
        1.27536670759978104416e0,
        5.01905042251180477414e0,
        6.16021097993053585195e0,
        7.40974269950448939160e0,
        2.97886665372100240670e0,
    ]
)
_S = np.array(
    [
        2.26052863220117276590e0,
        9.39603524938001434673e0,
        1.20489539808096656605e1,
        1.70814450747565897222e1,
        9.60896809063285878198e0,
        3.36907645100081516050e0,
    ]
)
_T = np.array(
    [
        9.60497373987051638749e0,
        9.00260197203842689217e1,
        2.23200534594684319226e3,
        7.00332514112805075473e3,
        5.55923013010394962768e4,
    ]
)
_U = np.array(
    [
        3.35617141647503099647e1,
        5.21357949780152679795e2,
        4.59432382970980127987e3,
        2.26290000613890934246e4,
        4.92673942608635921086e4,
    ]
)
_SQRTH = 7.07106781186547524401e-1


def _polevl(x: Any, c: Any) -> Any:
    y = np.full_like(x, c[0])
    for ci in c[1:]:
        y = y * x + ci
    return y


def _p1evl(x: Any, c: Any) -> Any:
    y = x + c[0]
    for ci in c[1:]:
        y = y * x + ci
    return y


def _erf_small(x: Any) -> Any:
    z = x * x
    return x * _polevl(z, _T) / _p1evl(z, _U)


def _erfc_big(a: Any) -> Any:
    x = np.abs(a)
    z = np.exp(-a * a)
    lt8 = x < 8.0
    p = np.where(lt8, _polevl(x, _P), _polevl(x, _R))
    q = np.where(lt8, _p1evl(x, _Q), _p1evl(x, _S))
    y = (z * p) / q
    return np.where(a < 0, 2.0 - y, y)


def ndtr(a: Any) -> Any:
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


def _cdf(name: str, x: np.ndarray, params: tuple[Any, ...]) -> np.ndarray:
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


def _ks_stat_sorted(xs: np.ndarray, name: str, params: tuple[Any, ...]) -> float:
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
_PI_SQUARED = np.pi**2
_PI_FOUR = np.pi**4
_PI_SIX = np.pi**6
_STIRLING = [
    -2.955065359477124183e-2,
    6.4102564102564102564e-3,
    -1.9175269175269175269e-3,
    8.4175084175084175084e-4,
    -5.952380952380952381e-4,
    7.9365079365079365079e-4,
    -2.7777777777777777778e-3,
    8.3333333333333333333e-2,
]


def _clip(p: float) -> float:
    return float(min(max(p, 0.0), 1.0))


def _kolmogn_DMTW(n: int, d: float) -> float:
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
    v = 1.0 - h**intm
    w = np.empty(m)
    fac = 1.0
    for j in intm:
        w[j - 1] = fac
        fac /= j
        v[j - 1] *= fac
    tt = max(2 * h - 1.0, 0) ** m - 2 * h**m
    v[-1] = (1.0 + tt) * fac
    for i in range(1, m):
        H[i - 1 :, i] = w[: m - i + 1]
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


def _pomeranz_j1j2(i: int, n: int, ll: int, ceilf: int, roundf: int) -> tuple[int, int]:
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


def _kolmogn_Pomeranz(n: int, x: float) -> float:
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
            conv = np.convolve(V0[k1 - V0s : k1 - V0s + ln2], pwrs[:ln2])
            conv_start = j1 - k1
            conv_len = j2 - j1 + 1
            V1[:conv_len] = conv[conv_start : conv_start + conv_len]
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


def _kolmogn_PelzGood(n: int, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    z = np.sqrt(n) * x
    zsquared, zthree, zfour, zsix = z**2, z**3, z**4, z**6
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
    k3a = -30 * zsix - 90 * z**8
    K0to3 = np.zeros(4)
    maxk = int(np.ceil(16 * z / np.pi))
    for k in range(maxk, 0, -1):
        m = 2 * k - 1
        msquared, mfour, msix = m**2, m**4, m**6
        qpower = np.power(q, 8 * k)
        coeffs = np.array(
            [
                1.0,
                k1a + k1b * msquared,
                k2a + k2b * msquared + k2c * mfour,
                k3a + k3b * msquared + k3c * mfour + k3d * msix,
            ]
        )
        K0to3 *= qpower
        K0to3 += coeffs
    K0to3 *= q
    K0to3 *= _SQRT2PI
    K0to3 /= np.array([z, 6 * zfour, 72 * z**7, 6480 * z**10])
    q = np.exp(-_PI_SQUARED / 2 / zsquared)
    ks = np.arange(maxk, 0, -1)
    ksquared = ks**2
    sqrt3z = _SQRT3 * z
    kspi = np.pi * ks
    qpwers = q**ksquared
    k2extra = np.sum(ksquared * qpwers)
    k2extra *= _PI_SQUARED * _SQRT2PI / (-36 * zthree)
    K0to3[2] += k2extra
    k3extra = np.sum((sqrt3z + kspi) * (sqrt3z - kspi) * ksquared * qpwers)
    k3extra *= _PI_SQUARED * _SQRT2PI / (216 * zsix)
    K0to3[3] += k3extra
    powers_of_n = np.power(n * 1.0, np.arange(len(K0to3)) / 2.0)
    K0to3 /= powers_of_n
    return float(sum(K0to3))


def _smirnov(n: int, d: float) -> float:
    """One-sided exact Smirnov sf (Birnbaum & Tingey sum, vectorised).  Only reached in
    branches where the two-sided p-value is far below the 0.05 gate, so only its
    magnitude matters (it is never reported)."""
    if d <= 0:
        return 1.0
    if d >= 1:
        return 0.0
    j = np.arange(int(math.floor(n * (1 - d))) + 1, dtype=np.float64)
    a = 1 - d - j / n
    b = d + j / n
    ok = a > 0
    j, a, b = j[ok], a[ok], b[ok]
    # log C(n, j) via cumulative sums of log((n - i + 1) / i)
    steps = np.log((n - np.arange(1, n + 1) + 1) / np.arange(1, n + 1))
    logc = np.concatenate(([0.0], np.cumsum(steps)))[j.astype(np.int64)]
    lt = logc + (n - j) * np.log(a) + (j - 1) * np.log(b)
    return float(min(max(d * np.exp(lt).sum(), 0.0), 1.0))


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
    elif n <= 100000 and n * x**1.5 <= 1.4:
        cdfprob = _kolmogn_DMTW(n, x)
    else:
        cdfprob = _kolmogn_PelzGood(n, x)
    return _clip(1.0 - cdfprob)


# --- fits ------------------------------------------------------------------


class _FitError(Exception):
    pass


_LOGXMAX = np.log(np.finfo(float).max)
_RTOL = 4 * np.finfo(float).eps


def _lognorm_logpdf(x: Any, s: Any) -> Any:
    return -(np.log(x) ** 2) / (2 * s**2) - np.log(s * x * np.sqrt(2 * np.pi))


def _lognorm_nnlf(theta: Any, data: Any) -> Any:
    """rv_continuous.nnlf for lognorm (non-penalised)."""
    s, loc, scale = theta
    if not (s > 0) or scale <= 0:
        return np.inf
    x = (np.asarray(data) - loc) / scale
    n_log_scale = len(x) * np.log(scale)
    if np.any(~((0 < x) & (x < np.inf))):
        return np.inf
    return -np.sum(_lognorm_logpdf(x, s), axis=0) + n_log_scale


_C_SQRT2PI = np.sqrt(2 * np.pi)


def _lognorm_logpdf_inplace(x: Any, s: Any) -> Any:
    """_lognorm_logpdf with fewer temporaries; same operations in the same order, so the
    result is bitwise identical."""
    a = np.log(x)
    np.square(a, out=a)
    np.negative(a, out=a)
    np.divide(a, 2 * s**2, out=a)
    b = np.multiply(s, x)
    np.multiply(b, _C_SQRT2PI, out=b)
    np.log(b, out=b)
    np.subtract(a, b, out=a)
    return a


def _lognorm_penalized_nnlf(theta: Any, data: Any) -> Any:
    """rv_continuous._penalized_nnlf for lognorm."""
    s, loc, scale = theta
    if not (s > 0) or scale <= 0:
        return np.inf
    x = np.subtract(data, loc)
    np.divide(x, scale, out=x)
    n_log_scale = len(x) * np.log(scale)
    with np.errstate(invalid="ignore"):
        good = (0 < x) & (x < np.inf)
    n_bad = len(x) - np.count_nonzero(good)
    if n_bad > 0:
        x = x[good]
    with np.errstate(divide="ignore", invalid="ignore"):
        logff = _lognorm_logpdf_inplace(x, s)
    finite = np.isfinite(logff)
    nf = len(logff) - np.count_nonzero(finite)
    n_bad += nf
    if n_bad > 0:
        tot = np.sum(logff[finite], axis=0) if nf else np.sum(logff, axis=0)
        return -tot + n_bad * np.log(np.finfo(float).max) * 100 + n_log_scale
    return -np.sum(logff, axis=0) + n_log_scale


class _MaxFun(Exception):
    pass


def _nelder_mead(func: Any, x0: Any, data: Any, xatol: float = 1e-4, fatol: float = 1e-4) -> Any:
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

    def f(x: Any) -> Any:
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
            if (
                np.max(np.ravel(np.abs(sim[1:] - sim[0]))) <= xatol
                and np.max(np.abs(fsim[0] - fsim[1:])) <= fatol
            ):
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


def _lognorm_generic_fit(data: Any) -> Any:
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


def _brentq(
    f: Any, xa: Any, xb: Any, xtol: float = 2e-12, rtol: float = _RTOL, maxiter: int = 100
) -> Any:
    """scipy.optimize.brentq (zeros.c) incl. the NaN-raising wrapper."""

    def fw(x: Any) -> Any:
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


def _lognorm_fit(data: Any) -> Any:
    """scipy.stats.lognorm.fit(data) (scipy 1.17 override, loc free)."""
    if not np.isfinite(data).all():
        raise _FitError("non-finite")
    data_min = np.min(data)

    # Same element-wise operations as scipy, evaluated into two reusable buffers
    # (bitwise-identical results, far fewer page-faulting temporaries on big columns).
    B1 = np.empty_like(data)
    B2 = np.empty_like(data)

    def get_shape_scale(loc: Any) -> Any:
        lndata = np.log(np.subtract(data, loc, out=B1), out=B1)
        scale = np.exp(lndata.mean())
        d = np.subtract(lndata, np.log(scale), out=B2)
        shape = np.sqrt(np.mean(np.square(d, out=d)))
        return shape, scale

    def dL_dLoc(loc: Any) -> Any:
        shape, scale = get_shape_scale(loc)
        shifted = np.subtract(data, loc, out=B1)
        t = np.divide(shifted, scale, out=B2)
        np.log(t, out=t)
        np.divide(t, shape**2, out=t)
        np.add(1, t, out=t)
        np.divide(t, shifted, out=t)
        return np.sum(t)

    def ll(loc: Any) -> Any:
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
        while (
            np.isfinite(lbrack)
            and np.isfinite(dL_dLoc_lbrack)
            and np.sign(dL_dLoc_lbrack) == np.sign(dL_dLoc_rbrack)
        ):
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


def fit(name: str, data: np.ndarray) -> tuple[Any, ...]:
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
    return cast(tuple[Any, ...], _lognorm_fit(data))


_CANDIDATES = ("normal", "uniform", "exponential", "lognormal")


def _param_dict(name: str, params: Any) -> dict[str, float]:
    if name == "lognormal":
        return {"s": float(params[0]), "loc": float(params[1]), "scale": float(params[2])}
    return {"loc": float(params[0]), "scale": float(params[1])}


def detect_distribution(values: np.ndarray) -> tuple[str | None, dict[str, float] | None]:
    """DataProfiler._detect_distribution."""
    if len(values) > FIT_SAMPLE_ROWS:
        values = np.random.default_rng(FIT_SAMPLE_SEED).choice(
            values, size=FIT_SAMPLE_ROWS, replace=False
        )
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
