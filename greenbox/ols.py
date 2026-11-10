"""Orthogonal least squares with Error Reduction Ratio (ERR) term selection.

Householder QR with column pivoting where the pivot is the candidate with the
largest ERR (forward orthogonal regression: Korenberg et al. 1988; Billings,
Chen & Korenberg 1989; Golub & Van Loan, sec. 5.4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .regressors import build_regressors
from .terms import info, is_noise_term


DEPENDENT_TOL = 1e-10  # relative remaining norm below which a column counts as dependent


def house(x):
    v = np.array(x, dtype=float)
    mu = np.linalg.norm(x)
    if mu != 0:
        with np.errstate(divide="ignore", invalid="ignore"):
            # sign(0) taken as +1 (sign(0) = 0 would divide by zero when x[0] == 0)
            v[1:] = v[1:] / (x[0] + (1.0 if x[0] >= 0 else -1.0) * mu)
    v[0] = 1.0
    return v


def rowhouse(A, v):
    beta = -2.0 / (v @ v)
    w = beta * (A.T @ v)
    return A + np.multiply.outer(v, w)


EXACT_TIE_TOL = 1e-12  # relative ERR difference that is only rounding
TIE_TOL = 1e-9  # relative ERR difference treated as a tie (identical columns, e.g. u^2 = u for a 0/1 input)


def _argmax(c, tie=0.0):
    """First index of the maximum, ignoring NaN. With ``tie`` > 0 the first index within
    that relative distance of the maximum, so that rounding never decides between
    identical candidates (candidates are listed from the lowest degree up)."""
    c = np.where(np.isnan(c), -np.inf, c)
    k = int(np.argmax(c))
    if tie > 0 and np.isfinite(c[k]) and c[k] > 0:
        k = int(np.argmax(c >= c[k] * (1 - tie)))
    return k


@dataclass
class QRState:
    A: np.ndarray       # factorised matrix (R on/above diagonal, Householder vectors below)
    y: np.ndarray       # Q' y
    piv: np.ndarray     # column permutation
    err: np.ndarray     # ERR of each selected column, in selection order
    a: np.ndarray
    b: np.ndarray
    c: np.ndarray
    n_selected: int
    sqy: float


def housels(A, y, n_terms, sqy=None, state: QRState | None = None, drop_dependent=True):
    """Select ``n_terms`` columns of ``A`` by ERR and factorise.

    When ``state`` is given, the first ``state.n_selected`` columns of ``A``
    are taken as already factorised (used to append noise terms) and ``n_terms`` further columns are selected.
    """
    A = np.array(A, dtype=float)
    m, n = A.shape
    if state is None:
        y = np.array(y, dtype=float)
        sqy = float(y @ y) if sqy is None else sqy
        r = 0
        piv = np.arange(n)
        err = np.zeros(n)
        a, b, c = np.zeros(n), np.zeros(n), np.zeros(n)
    else:
        y = state.y.copy()
        sqy = state.sqy
        r = state.n_selected
        piv = np.concatenate([state.piv[:r], np.arange(r, n)])
        err = np.concatenate([state.err[:r], np.zeros(n - r)])
        a = np.concatenate([state.a[:r], np.zeros(n - r)])
        b = np.concatenate([state.b[:r], np.zeros(n - r)])
        c = np.concatenate([state.c[:r], np.zeros(n - r)])

    target = min(r + n_terms, n)
    # columns whose remaining norm collapses are (numerically) linear combinations of
    # columns already selected, e.g. u^2 = 5u for a binary input: never select them
    norm0 = np.concatenate([np.full(r, np.inf), np.einsum("ij,ij->j", A[:, r:], A[:, r:])])
    tol = DEPENDENT_TOL if drop_dependent else -1.0
    # the classical procedure keeps the maximum; only exact ties (identical columns, equal up to
    # rounding) go to the first candidate, so the result does not depend on the processor
    tie = TIE_TOL if drop_dependent else EXACT_TIE_TOL
    with np.errstate(divide="ignore", invalid="ignore"):
        a[r:] = np.einsum("ij,ij->j", A[r:, r:], A[r:, r:])
        b[r:] = A[r:, r:].T @ y[r:]
        c[r:] = b[r:] ** 2 / (a[r:] * sqy)
        c[r:][a[r:] <= tol * norm0[r:]] = -np.inf
        k = r + _argmax(c[r:], tie)
        while r < target:
            if c[k] == -np.inf:  # only dependent columns left: the library is exhausted
                break
            for arr in (piv, a, b, c):
                arr[[r, k]] = arr[[k, r]]
            A[:, [r, k]] = A[:, [k, r]]
            v = house(A[r:, r])
            A[r:, r:] = rowhouse(A[r:, r:], v)
            A[r + 1:, r] = v[1:]
            y[r:] = rowhouse(y[r:, None], v)[:, 0]
            a[r + 1:] -= A[r, r + 1:] ** 2
            b[r + 1:] -= A[r, r + 1:] * y[r]
            c[r + 1:] = b[r + 1:] ** 2 / (a[r + 1:] * sqy)
            c[r + 1:][a[r + 1:] <= tol * norm0[r + 1:]] = -np.inf
            if r < n - 1:
                k = r + 1 + _argmax(c[r + 1:], tie)
            err[r] = c[r]
            r += 1
    return QRState(A, y, piv, err, a, b, c, r, sqy)


def solve_prefix(state: QRState, k: int):
    """Coefficients and standard deviations of the model made of the first
    ``k`` selected columns (nested models come for free from one QR)."""
    m = state.A.shape[0]
    R = np.triu(state.A[:k, :k])
    theta = np.linalg.solve(R, state.y[:k]) if k else np.zeros(0)
    var = float(state.y[k:] @ state.y[k:]) / max(m - k, 1)
    try:
        Rinv = np.linalg.inv(R)
        std = np.sqrt(var * np.sum(Rinv**2, axis=1))
    except np.linalg.LinAlgError:
        std = np.full(k, np.nan)
    return theta, std, var


@dataclass
class OrthRegResult:
    model: np.ndarray      # selected terms (process first, then noise)
    theta: np.ndarray
    err: np.ndarray
    std: np.ndarray
    residuals: np.ndarray  # full length, first ``lag`` samples set to zero
    variance: float
    lag: int


def orthreg(model, u, y, n_process, n_noise=0, n_iter=0, drop_dependent=True):
    """Structure selection + estimation by forward orthogonal regression.

    ``drop_dependent`` skips candidates that are linear combinations of the
    terms already selected (pass False to keep them, as in the classical procedure).

    ``n_noise`` > 0 with ``n_iter`` > 0 runs extended least squares: the
    residuals are used as e(k) to select/estimate the noise model (NARMAX).

    Every noise iteration restarts from the process-term factorisation, so the
    noise terms of one iteration never inherit the pivoting of the previous one.
    """
    y = np.asarray(y, dtype=float).ravel()
    u = None if u is None else np.asarray(u, dtype=float).ravel()
    model = np.atleast_2d(model)
    npr, nno, lag = info(model)
    proc = model[~is_noise_term(model)]
    noise = model[is_noise_term(model)]
    n_process = min(n_process, npr)
    n_noise = min(n_noise, nno)
    if n_noise == 0:
        n_iter = 0

    Pp = build_regressors(proc, u, y)
    Y = y[lag:]
    st = housels(Pp[lag:], Y, n_process, drop_dependent=drop_dependent)
    n_process = st.n_selected
    theta, std, var = solve_prefix(st, n_process)
    piv_p = st.piv[:n_process]
    Pp_sel = Pp[:, piv_p]
    e = y - Pp_sel @ theta
    sel_model, err = proc[piv_p], st.err[:n_process]

    B = st.A[:, :n_process]
    for _ in range(n_iter):
        e = np.concatenate([np.zeros(lag), e[lag:]])
        Pn = build_regressors(noise, u, y, e)
        Pn1 = apply_qr(Pn[lag:], B)
        base = QRState(np.hstack([B, Pn1]), st.y, st.piv, st.err, st.a, st.b, st.c, n_process, st.sqy)
        stn = housels(base.A, None, n_noise, state=base, drop_dependent=drop_dependent)
        n_noise = stn.n_selected - n_process
        theta, std, var = solve_prefix(stn, n_process + n_noise)
        piv_n = stn.piv[n_process:n_process + n_noise] - n_process
        e = y - np.hstack([Pp_sel, Pn[:, piv_n]]) @ theta
        sel_model = np.vstack([proc[piv_p], noise[piv_n]])
        err = stn.err[:n_process + n_noise]

    e = np.concatenate([np.zeros(lag), e[lag:]])
    return OrthRegResult(sel_model, theta, err, std, e, var, lag)


def apply_qr(P0, A):
    """Apply the Householder reflections stored below the diagonal of ``A``."""
    P0 = np.array(P0, dtype=float)
    m, n = A.shape
    for j in range(n):
        v = np.concatenate([[1.0], A[j + 1:, j]])
        P0[j:] = rowhouse(P0[j:], v)
    return P0
