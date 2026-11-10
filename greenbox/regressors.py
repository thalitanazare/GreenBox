"""Regressor matrices: one column per candidate term."""

from __future__ import annotations

import numpy as np

from .terms import E, U, Y, lag_of, signal_of


def shift(x, lag):
    """x delayed by ``lag`` samples, zero-padded at the start (shift_col)."""
    out = np.zeros_like(x, dtype=float)
    if lag == 0:
        out[:] = x
    elif lag < len(x):
        out[lag:] = x[:-lag]
    return out


def build_regressors(model, u=None, y=None, e=None):
    """Regressor matrix with one column per row of ``model``.

    Rows of the matrix are time samples. The first ``max_lag`` rows contain
    zero-padded values and are normally discarded by the caller.
    """
    model = np.atleast_2d(model)
    signals = {Y: y, U: u, E: e}
    n = next(len(s) for s in signals.values() if s is not None)
    P = np.ones((n, model.shape[0]))
    cache = {}
    for i, row in enumerate(model):
        for c in row:
            if c <= 0:
                continue
            s, l = int(signal_of(c)), int(lag_of(c))
            if signals[s] is None:
                raise ValueError(f"term needs signal {'yue'[s - 1] if s in (1, 2, 3) else s} which was not given")
            key = (s, l)
            if key not in cache:
                cache[key] = shift(np.asarray(signals[s], dtype=float), l)
            P[:, i] *= cache[key]
    return P
