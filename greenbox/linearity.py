"""Is the system linear? Two complementary checks on the estimation data.

1. Correlation test (Billings & Voon, 1983), model free: for a linear
   system driven by an input with zero third-order moments,
   r_{y'y'^2}(tau) = corr(y'(k), y'^2(k+tau) - mean) stays inside the 95 %
   band; y' = y - mean(y). The band uses Bartlett's variance so that the
   autocorrelation of the output does not inflate the test.
2. Model comparison: the best linear model (degree 1) against the best
   polynomial model of degree >= 2, both scored by free-run R^2 on hold-out
   estimation data (the same score as the structure search).

Verdict from the relative reduction of the hold-out error (NRMSE) obtained by
the nonlinear model, backed by the correlation test.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .metrics import ccf

STRONG = 0.25   # nonlinear model removes >= 25 % of the linear model's hold-out error
WEAK = 0.10


def correlation_test(y, max_lag=20):
    y = np.asarray(y, dtype=float)
    y = y[np.isfinite(y)]
    yc = y - y.mean()
    lags, r = ccf(yc, yc**2 - np.mean(yc**2), max_lag)
    keep = lags >= 0
    lags, r = lags[keep], r[keep]
    # Bartlett: var(r_xz) ~ (1/N) sum_j rho_x(j) rho_z(j). The plain 1.96/sqrt(N) band
    # assumes white signals and flags almost every strongly autocorrelated output.
    n = len(y)
    m = int(min(n // 4, 100))
    z = yc**2 - np.mean(yc**2)
    _, rx = ccf(yc, yc, m)
    _, rz = ccf(z, z, m)
    bound = 1.96 * np.sqrt(max(float(np.sum(rx * rz)), 1.0) / n)
    outside = np.abs(r) > bound
    return {"lags": lags.tolist(), "r": r.tolist(), "bound": float(bound),
            "fraction_outside": float(outside.mean()), "max_abs": float(np.max(np.abs(r)))}


def model_comparison(rows):
    """Best hold-out R^2 of degree-1 vs degree>=2 structures from a search table."""
    lin = [r["holdout_r2"] for r in rows if r["degree"] == 1 and r["holdout_r2"] is not None]
    nl = [r["holdout_r2"] for r in rows if r["degree"] >= 2 and r["holdout_r2"] is not None]
    if not lin or not nl:
        return None
    r2_lin, r2_nl = max(lin), max(nl)
    e_lin, e_nl = np.sqrt(max(1 - r2_lin, 0)), np.sqrt(max(1 - r2_nl, 0))
    gain = 1 - e_nl / e_lin if e_lin > 0 else 0.0
    return {"r2_linear": r2_lin, "r2_nonlinear": r2_nl, "nrmse_linear": float(e_lin),
            "nrmse_nonlinear": float(e_nl), "error_reduction": float(gain)}


def verdict(corr, comp):
    if comp is None:
        nl = corr["fraction_outside"] > 0.2
        return ("nonlinear" if nl else "linear",
                f"Correlation test only: {100 * corr['fraction_outside']:.0f}% of the lags of r_y'y'² lie "
                f"outside the 95% band.")
    g = comp["error_reduction"]
    corr_flag = corr["fraction_outside"] > 0.2
    # the correlation test assumes a symmetric input, so it only supports the model evidence
    if g >= STRONG or (g >= WEAK and corr_flag):
        label = "nonlinear"
    elif g >= WEAK:
        label = "weakly nonlinear"
    else:
        label = "linear"
    text = (f"The best nonlinear polynomial model simulates the test part with "
            f"{100 * g:.0f}% less error than the best linear model "
            f"(NRMSE {comp['nrmse_nonlinear']:.3f} vs {comp['nrmse_linear']:.3f}; "
            f"R² {comp['r2_nonlinear']:.3f} vs {comp['r2_linear']:.3f}). "
            f"Correlation test: {100 * corr['fraction_outside']:.0f}% of the lags of r_y'y'² lie outside the 95% band.")
    return label, text


def check(u, y, holdout_fraction=0.3, max_degree=3, max_lag=4, rows=None, progress=None):
    """Run both checks. ``rows`` may reuse a structure-search table that already
    contains degree-1 and higher-degree structures."""
    from .pipeline import RunConfig, search_structure  # local import: pipeline imports this module

    corr = correlation_test(y)
    comp = model_comparison(rows) if rows else None
    if comp is None and u is not None:
        cfg = RunConfig(family="NARX", search_max_degree=max(2, max_degree), search_max_lag=max_lag,
                        holdout_fraction=holdout_fraction)
        _, search = search_structure(replace(cfg), u, y, progress or (lambda m, f: None))
        comp = model_comparison(search["rows"])
    label, text = verdict(corr, comp)
    return {"label": label, "text": text, "correlation": corr, "comparison": comp}
