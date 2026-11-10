"""Fit metrics, information criteria and residual validation tests."""

from __future__ import annotations

import numpy as np


def fit_metrics(y, yhat):
    """RMSE, NRMSE = ||e|| / ||y - mean(y)|| (a.k.a. RRSE) and R^2."""
    y = np.asarray(y, dtype=float).ravel()
    yhat = np.asarray(yhat, dtype=float).ravel()
    if not np.all(np.isfinite(yhat)):
        return {"RMSE": np.nan, "NRMSE": np.nan, "R2": np.nan}
    r = y - yhat
    den = np.linalg.norm(y - y.mean())
    sst = den**2
    return {
        "RMSE": float(np.sqrt(np.mean(r**2))),
        "NRMSE": float(np.linalg.norm(r) / den) if den > np.finfo(float).eps else np.nan,
        "R2": float(1 - (r @ r) / sst) if sst > np.finfo(float).eps else np.nan,
    }


def information_criterion(residuals, n_params, kind="AIC"):
    """N ln(sigma^2) + penalty (factor 2 for AIC, ln N for BIC)."""
    r = np.asarray(residuals, dtype=float)
    N = len(r)
    s2 = max(float(np.mean(r**2)), np.finfo(float).tiny)
    penalty = {"AIC": 2.0, "BIC": np.log(N), "FPE": None}[kind]
    if kind == "FPE":
        return N * np.log(s2) + N * np.log((N + n_params) / (N - n_params))
    return N * np.log(s2) + penalty * n_params


def ccf(x, z, max_lag):
    """Normalised cross-correlation r_xz(tau) for tau = -max_lag..max_lag."""
    x = np.asarray(x, dtype=float) - np.mean(x)
    z = np.asarray(z, dtype=float) - np.mean(z)
    n = len(x)
    den = np.sqrt((x @ x) * (z @ z))
    lags = np.arange(-max_lag, max_lag + 1)
    if den == 0:
        return lags, np.zeros(len(lags))
    r = np.array([(x[max(0, -t):n - max(0, t)] @ z[max(0, t):n - max(0, -t)]) for t in lags]) / den
    return lags, r


def residual_tests(e, u=None, max_lag=25):
    """Billings & Voon correlation tests; values inside +-1.96/sqrt(N) pass."""
    e = np.asarray(e, dtype=float)
    e = e[np.isfinite(e)]
    tests = {}
    lags, r = ccf(e, e, max_lag)
    tests["r_ee"] = r
    e2 = (e - e.mean()) ** 2
    tests["r_e(ee)"] = ccf(e, e2, max_lag)[1]
    if u is not None:
        u = np.asarray(u, dtype=float)[: len(e)]
        tests["r_ue"] = ccf(u, e, max_lag)[1]
        u2 = u**2 - np.mean(u**2)
        tests["r_u2e"] = ccf(u2, e, max_lag)[1]
        tests["r_u2e2"] = ccf(u2, e**2 - np.mean(e**2), max_lag)[1]
    return {"lags": lags, "bound": 1.96 / np.sqrt(len(e)), "tests": tests}
