"""Model simulation: free-run (MPO) and one-step-ahead prediction."""

from __future__ import annotations

import numpy as np

from .regressors import build_regressors
from .terms import U, Y, info, is_noise_term, lag_of, signal_of


def _compile(model, theta, limit):
    """Straight-line Python for one free-run step (much faster than looping
    over the term table). Noise terms are dropped: e(k) = 0 in free run."""
    exprs = []
    for row, coef in zip(np.atleast_2d(model), theta):
        f = [repr(float(coef))]
        for c in row:
            if c <= 0:
                continue
            s, l = int(signal_of(c)), int(lag_of(c))
            f.append(f"y[k-{l}]" if s == Y else f"u[k-{l}]")
        exprs.append("*".join(f))
    if not np.all(np.isfinite(theta)):
        return lambda y, u, k0, n: k0  # unusable coefficients: treat as diverged
    body = " + ".join(exprs) if exprs else "0.0"
    src = (
        "def step(y, u, k0, n):\n"
        "    for k in range(k0, n):\n"
        f"        v = {body}\n"
        f"        if v != v or v > {limit!r} or v < -{limit!r}:\n"
        "            return k\n"
        "        y[k] = v\n"
        "    return n\n"
    )
    ns = {}
    exec(src, ns)
    return ns["step"]


def free_run(model, theta, u, y_init, n=None, divergence_limit=1e12):
    """Simulate the model in free run (y fed back, noise terms set to zero).

    ``y_init`` holds the first ``len(y_init)`` output samples (at least the
    model lag); simulation continues from there until ``n`` samples
    (default ``len(u)``). Returns the full simulated sequence; samples after
    a divergence are NaN.
    """
    u = None if u is None else np.asarray(u, dtype=float).ravel()
    n = len(u) if n is None else n
    y0 = np.asarray(y_init, dtype=float).ravel()
    model = np.atleast_2d(model)
    _, _, lag = info(model)
    if len(y0) < lag:
        raise ValueError(f"need at least {lag} initial output samples")
    keep = ~is_noise_term(model)
    th = np.asarray(theta, dtype=float)[: len(keep)][keep]
    step = _compile(model[keep], th, float(divergence_limit))
    ys = [0.0] * n
    ys[: len(y0)] = y0.tolist()
    uu = [0.0] * n if u is None else u[:n].tolist()
    stop = step(ys, uu, len(y0), n)
    out = np.array(ys)
    out[stop:] = np.nan
    return out


def one_step_ahead(model, theta, u, y, e=None):
    """One-step-ahead prediction using measured past outputs."""
    model = np.atleast_2d(model)
    if e is None and is_noise_term(model).any():
        keep = ~is_noise_term(model)
        model, theta = model[keep], np.asarray(theta)[keep]
    P = build_regressors(model, u, y, e)
    yhat = P @ np.asarray(theta, dtype=float)
    _, _, lag = info(model)
    yhat[:lag] = np.asarray(y, dtype=float)[:lag]
    return yhat
