"""Term coding for polynomial (NAR(MA)X, FIR, Volterra) models.

A model is an integer matrix with one row per term and ``degree`` columns.
Each non-zero entry is a factor coded as ``1000 * signal + lag``:

    signal 1 -> y(k - lag)     (output)
    signal 2 -> u(k - lag)     (input)
    signal 3 -> e(k - lag)     (noise / residual)

Zero entries are empty slots, so a row of zeros is the constant term.
This is the usual coding of polynomial NARMAX candidate sets.
"""

from __future__ import annotations

from math import comb

import numpy as np

Y, U, E = 1, 2, 3
SIGNAL_NAMES = {Y: "y", U: "u", E: "e"}


def code(signal: int, lag: int) -> int:
    return 1000 * signal + lag


def signal_of(c):
    return np.floor_divide(c, 1000)


def lag_of(c):
    return np.mod(c, 1000)


def generate_candidates(degree, y_lags=(), u_lags=(), e_lags=(), include_constant=True):
    """Return all monomials up to ``degree`` built from the given lagged factors.

    The enumeration order is fixed (process terms first, noise terms last). Term order matters
    because ties in the ERR ranking are broken by position.
    """
    if degree < 1:
        raise ValueError("degree must be >= 1")
    factors = [code(Y, l) for l in y_lags] + [code(U, l) for l in u_lags] + [code(E, l) for l in e_lags]
    if any(l == 0 for l in y_lags) or any(l == 0 for l in e_lags):
        raise ValueError("y and e lags must be >= 1")
    nf = len(factors)
    col = [0] + factors  # col[v]: code of factor v, v = 0 is the constant

    rows = [[0] * degree]
    rows += [[f] + [0] * (degree - 1) for f in factors]
    total = comb(nf + degree, degree)
    a = [nf] + [0] * (degree - 1)
    for _ in range(nf + 1, total):
        j = next(i for i, v in enumerate(a) if v < nf)
        val = a[j] + 1
        for t in range(j + 1):
            a[t] = val
        rows.append([col[v] for v in a])

    model = reorder_process_first(np.array(rows, dtype=int).reshape(-1, degree))
    if not include_constant:
        model = model[np.any(model != 0, axis=1)]
    return model


def genterms(degree, lagy, lagu=0, lage=0):
    """All terms up to degree ``degree`` with lags 1..lagy, 1..lagu and 1..lage."""
    return generate_candidates(degree, range(1, lagy + 1), range(1, lagu + 1), range(1, lage + 1))


def is_noise_term(model):
    return np.any(signal_of(model) == E, axis=1)


def reorder_process_first(model):
    noise = is_noise_term(model)
    return np.vstack([model[~noise], model[noise]])


def info(model):
    """(n_process, n_noise, max_lag) of a model."""
    model = np.atleast_2d(model)
    noise = is_noise_term(model)
    lag = int(lag_of(model).max()) if model.size else 0
    return int((~noise).sum()), int(noise.sum()), lag


def n_factors(model):
    """Number of signal factors in each term (0 for the constant)."""
    return np.count_nonzero(np.atleast_2d(model) > 0, axis=1)


def term_to_string(row) -> str:
    factors = []
    for c in row:
        if c <= 0:
            continue
        s, l = divmod(int(c), 1000)
        factors.append(f"{SIGNAL_NAMES[s]}(k)" if l == 0 else f"{SIGNAL_NAMES[s]}(k-{l})")
    if not factors:
        return "1"
    # group repeated factors as powers: y(k-1)*y(k-1) -> y(k-1)^2
    out, i = [], 0
    while i < len(factors):
        j = i
        while j < len(factors) and factors[j] == factors[i]:
            j += 1
        out.append(factors[i] if j - i == 1 else f"{factors[i]}^{j - i}")
        i = j
    return " * ".join(out)


def model_to_strings(model):
    return [term_to_string(r) for r in np.atleast_2d(model)]
