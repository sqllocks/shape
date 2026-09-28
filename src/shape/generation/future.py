import numpy as np


def gaussian_copula(corr, n, seed=0):
    c = np.asarray(corr, float)
    w, v = np.linalg.eigh(c)
    c = (v * np.maximum(w, 1e-9)) @ v.T
    return np.random.default_rng(seed).multivariate_normal(np.zeros(len(c)), c, n)


def missingness(values, rate, seed=0):
    x = np.array(values, copy=True)
    mask = np.random.default_rng(seed).random(len(x)) < rate
    return x, mask
