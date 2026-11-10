"""Green identification: term selection trading accuracy against arithmetic cost.

Classical procedure (T. Nazare):

    term order :  J = w_E (1 - ERR_norm) + w_C C_norm      (per FROLS step)
    model size :  J = w_E ERRloss_norm   + w_C C_norm      (over k)

Hold-out simulation procedure (default, see ``choose_from_pool``): candidate
models from several term orders are scored by free-run simulation on held-out
estimation data. Among the admissible ones (stable and at least as accurate as a
minimum hold-out R^2), each decision method on the Pareto set picks one Green model
from the scaled error E and complexity C (``DECISION_METHODS``, ``decision_score``):

    cheapest      C
    epsilon       C subject to E <= eps            (epsilon-constraint)
    weighted      w_E E + w_C C                    (compromise programming, p = 1)
    euclidean     ((w_E E)^2 + (w_C C)^2)^(1/2)    (p = 2)
    tchebycheff   max(w_E E, w_C C)                (p = infinity)
    accurate      E

The Green identification never uses the Standard model.

Complexity of one model evaluation (one output sample):

    C = n_add * c_add + n_mult * c_mult      (equivalently C = c_add (a + rho b))

with n_add = n_terms - 1 and n_mult = number of signal factors (each factor
multiplies the running product starting at the coefficient). The cost per
operation comes from a ``CostModel``: either a theoretical bit-level model
for B-bit operands (an addition costs B bit operations, a multiplication
M(B)) or timings calibrated on the user's own machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .ols import house, rowhouse
from .regressors import build_regressors
from .terms import info, n_factors

# Reference timing: 1.072312e-9 s per 64-bit multiplication
# and 121.81/60 mgCO2e/s from the Green Algorithms calculator. With Karatsuba
# this gives about 3e-12 mgCO2e per complexity unit.
REF_MULT_SECONDS = 1.072312e-9
DEFAULT_EMISSION_MG_PER_S = 121.81 / 60

THEORETICAL = {
    "karatsuba": ("Karatsuba", "M(B) = B^1.585 (log2 3)"),
    "schoolbook": ("Schoolbook (long multiplication)", "M(B) = B^2"),
    "toom3": ("Toom-Cook 3-way", "M(B) = B^(log 5 / log 3) = B^1.465"),
    "schonhage": ("Schonhage-Strassen", "M(B) = B log2(B) log2(log2(B))"),
    "uniform": ("Uniform operation count", "rho = 1: every operation costs the same"),
}


@dataclass
class CostModel:
    key: str
    label: str
    add_cost: float
    mult_cost: float
    unit: str                    # what one complexity unit is
    seconds_per_unit: float      # converts complexity into execution time
    emission_mg_per_s: float = DEFAULT_EMISSION_MG_PER_S
    details: dict = field(default_factory=dict)

    @property
    def rho(self):
        return self.mult_cost / self.add_cost

    def complexity(self, n_add, n_mult):
        return n_add * self.add_cost + n_mult * self.mult_cost

    def carbon_mg(self, complexity):
        return complexity * self.seconds_per_unit * self.emission_mg_per_s


def theoretical_cost_model(kind="karatsuba", bits=64, karatsuba_exponent=1.585,
                           ref_mult_seconds=REF_MULT_SECONDS, emission_mg_per_s=DEFAULT_EMISSION_MG_PER_S):
    B = float(bits)
    if kind == "karatsuba":
        add, mult = B, B ** karatsuba_exponent
    elif kind == "schoolbook":
        add, mult = B, B ** 2
    elif kind == "toom3":
        add, mult = B, B ** (math.log(5) / math.log(3))
    elif kind == "schonhage":
        add, mult = B, B * math.log2(B) * math.log2(math.log2(B))
    elif kind == "uniform":
        add, mult = 1.0, 1.0
    else:
        raise ValueError(f"unknown cost model {kind!r}")
    label, formula = THEORETICAL[kind]
    unit = "operation" if kind == "uniform" else "bit operation"
    # anchor the theoretical multiplication cost to a measured multiplication time
    return CostModel(kind, label, add, mult, unit, ref_mult_seconds / mult, emission_mg_per_s,
                     {"bits": bits, "formula": formula})


def calibrated_cost_model(t_add_ns, t_mult_ns, emission_mg_per_s=DEFAULT_EMISSION_MG_PER_S, details=None):
    if t_add_ns <= 0 or t_mult_ns <= 0:
        raise ValueError("calibrated times must be positive")
    return CostModel("calibrated", "Calibrated on this computer", float(t_add_ns), float(t_mult_ns),
                     "ns", 1e-9, emission_mg_per_s, dict(details or {}))


def ComplexitySettings(bits=64, mult_exponent=1.585):
    """Classical setting: Karatsuba with the given word length."""
    return theoretical_cost_model("karatsuba", bits, mult_exponent)


def operation_count(model):
    model = np.atleast_2d(model)
    n_add = max(model.shape[0] - 1, 0)
    n_mult = int(n_factors(model).sum())
    return n_add, n_mult


def complexity_from_counts(n_add, n_mult, cs: CostModel):
    return cs.complexity(n_add, n_mult)


def model_complexity(model, cs: CostModel):
    return complexity_from_counts(*operation_count(model), cs)


def normalise01(x):
    x = np.asarray(x, dtype=float).ravel()
    lo, hi = x.min(), x.max()
    if abs(hi - lo) <= np.spacing(np.max(np.abs(x)) + 1):
        return np.zeros_like(x)
    return (x - lo) / (hi - lo)


def green_term_order(candidates, u, y, max_terms, w_err, w_comp, cs: CostModel, drop_dependent=True):
    """Forward orthogonal regression ranking terms by ERR and complexity."""

    def score(err, r, mult_sel, nf_left):
        comp = complexity_from_counts(r, mult_sel + nf_left, cs)
        return w_err * (1 - normalise01(err)) + w_comp * normalise01(comp)

    return _forward_order(candidates, u, y, max_terms, score, drop_dependent)


def cost_efficiency_order(candidates, u, y, max_terms, cs: CostModel, drop_dependent=True):
    """Forward orthogonal regression ranking terms by ERR per unit of added cost
    (one addition plus one multiplication per factor of the term)."""

    def score(err, r, mult_sel, nf_left):
        return -err / (cs.add_cost + nf_left * cs.mult_cost)

    return _forward_order(candidates, u, y, max_terms, score, drop_dependent)


def _forward_order(candidates, u, y, max_terms, score, drop_dependent=True):
    """Forward orthogonal regression picking, at each step, the candidate with the
    lowest ``score(err, r, mult_sel, nf_left)`` (ties go to the largest ERR)."""
    npr, _, lag = info(candidates)
    candidates = candidates[:npr]
    max_terms = min(max_terms, npr)
    P = build_regressors(candidates, u, y)
    A = P[lag:].copy()
    Y = np.asarray(y, dtype=float)[lag:].copy()
    m, n = A.shape
    sqy = float(Y @ Y)
    if sqy <= np.finfo(float).eps:
        raise ValueError("Output energy is zero.")
    nf = n_factors(candidates)
    norm0 = np.einsum("ij,ij->j", A, A)
    piv = np.arange(n)
    err_sel = np.zeros(max_terms)
    mult_sel = 0
    for r in range(max_terms):
        Ar = A[r:, r:]
        aj = np.einsum("ij,ij->j", Ar, Ar)
        bj = Ar.T @ Y[r:]
        with np.errstate(divide="ignore", invalid="ignore"):
            err = np.where(aj <= np.finfo(float).eps, 0.0, bj**2 / (aj * sqy))
        J = score(err, r, mult_sel, nf[piv[r:]])
        # linearly dependent on the selected terms (e.g. u^2 = 5u for a binary input)
        if drop_dependent:
            J[aj <= 1e-10 * norm0[piv[r:]]] = np.inf
        if not np.isfinite(J).any():
            return piv[:r], err_sel[:r]
        tie = np.flatnonzero(np.abs(J - J.min()) <= 1e-12)
        best = tie[np.argmax(err[tie])]
        k = r + best
        if k != r:
            A[:, [r, k]] = A[:, [k, r]]
            piv[[r, k]] = piv[[k, r]]
        err_sel[r] = err[best]
        mult_sel += nf[piv[r]]
        v = house(A[r:, r])
        A[r:, r:] = rowhouse(A[r:, r:], v)
        Y[r:] = rowhouse(Y[r:, None], v)[:, 0]
    return piv[:max_terms], err_sel


@dataclass
class SizeSelection:
    k: np.ndarray
    cumulative_err: np.ndarray
    err_loss: np.ndarray
    complexity: np.ndarray
    cost: np.ndarray
    k_best: int = field(default=0)


def choose_green_size(candidates, order, err_order, min_terms, max_terms, method, w_err, epsilon, cs, tolerance=0.0):
    """``tolerance`` > 0 keeps the smallest size whose cost is within ``tolerance`` of the
    minimum (costs are on a 0-1 scale); 0 disables it (classical procedure)."""
    max_terms = min(max_terms, len(order))
    min_terms = min(min_terms, max_terms)
    ks = np.arange(min_terms, max_terms + 1)
    cum = np.array([err_order[:k].sum() for k in ks])
    loss = np.maximum(1 - cum, 0)
    comp = np.array([model_complexity(candidates[order[:k]], cs) for k in ks])
    J = decision_score(normalise01(loss), normalise01(comp), method, w_err, epsilon)
    sel = SizeSelection(ks, cum, loss, comp, J)
    sel.k_best = int(ks[int(np.flatnonzero(J <= J.min() + tolerance)[0])])
    return sel


def prune_sequence(P, Y, idx, nf, cs: CostModel | None = None):
    """Backward elimination from the terms ``idx`` (columns of ``P``): each step removes
    the term with the smallest increase of the residual sum of squares, per unit of
    cost saved when ``cs`` is given. Returns the nested subsets, from ``idx`` minus
    one term down to one term.

    Removing column j of a least-squares fit raises the SSE by theta_j^2 / [(A'A)^-1]_jj.
    """
    cur = list(idx)
    out = []
    while len(cur) > 1:
        A = P[:, cur]
        Q, R = np.linalg.qr(A)
        try:
            Rinv = np.linalg.inv(R)
        except np.linalg.LinAlgError:
            Rinv = np.linalg.pinv(R)
        theta = Rinv @ (Q.T @ Y)
        diag = np.sum(Rinv**2, axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            rise = np.where(diag > 0, theta**2 / diag, 0.0)
        saved = 1.0 if cs is None else cs.add_cost + nf[cur] * cs.mult_cost
        cur.pop(int(np.nanargmin(rise / saved)))
        out.append(list(cur))
    return out


@dataclass
class PoolChoice:
    index: int              # chosen entry of the pool
    cost: np.ndarray        # J of every pool entry (inf when not admissible)
    admissible: np.ndarray
    ranking: np.ndarray     # admissible entries, best first


# Decision methods on the Pareto set (Chankong & Haimes 1983), in menu order.
DECISION_METHODS = ("cheapest", "epsilon", "weighted", "euclidean", "tchebycheff", "accurate")
METHOD_LABELS = {
    "cheapest": "Cheapest",
    "epsilon": "ε-constraint",
    "weighted": "Weighted sum (p = 1)",
    "euclidean": "Euclidean (p = 2)",
    "tchebycheff": "Tchebycheff (p = ∞)",
    "accurate": "Most accurate",
}


def decision_score(e, c, method, w_err=0.5, epsilon=0.5):
    """Score (lower is better) of candidates with error ``e`` and complexity ``c`` scaled to 0-1.

    Every score is non-decreasing in both objectives. The epsilon-constraint ranks the
    candidates that meet ``e <= epsilon`` by complexity, then the others by error."""
    e = np.asarray(e, dtype=float)
    c = np.asarray(c, dtype=float)
    w_comp = 1.0 - w_err
    if method == "cheapest":
        return c.copy()
    if method == "accurate":
        return e.copy()
    if method == "weighted":
        return w_err * e + w_comp * c
    if method == "euclidean":
        return np.hypot(w_err * e, w_comp * c)
    if method == "tchebycheff":
        return np.maximum(w_err * e, w_comp * c)
    if method == "epsilon":
        return np.where(e <= epsilon + 1e-12, c, 1.0 + e)
    raise ValueError(f"Unknown decision method: {method}")


def choose_from_pool(error, complexity, method, error_limit, w_err=0.5, epsilon=0.5):
    """Green choice among candidate models scored by hold-out simulation.

    ``error`` is the hold-out NRMSE (inf when the simulation diverged). A model is
    admissible when it simulates and ``error <= error_limit``. The error is scaled to
    0-1 between the best admissible model and ``error_limit``, the complexity between
    the cheapest and the most costly admissible model; ``method`` (``DECISION_METHODS``)
    then picks the candidate. Ties go to lower complexity, then lower error.
    """
    e = np.asarray(error, dtype=float)
    c = np.asarray(complexity, dtype=float)
    ok = np.isfinite(e) & (e <= error_limit)
    J = np.full(len(e), np.inf)
    if ok.any():
        e_min, c_min = e[ok].min(), c[ok].min()
        e_span, c_span = error_limit - e_min, c[ok].max() - c_min
        e_hat = (e[ok] - e_min) / e_span if e_span > 1e-12 else np.zeros(ok.sum())
        c_hat = (c[ok] - c_min) / c_span if c_span > 1e-12 else np.zeros(ok.sum())
        J[ok] = decision_score(e_hat, c_hat, method, w_err, epsilon)
    ranking = np.array(sorted(np.flatnonzero(ok), key=lambda i: (round(J[i], 12), c[i], e[i])), dtype=int)
    return PoolChoice(int(ranking[0]) if len(ranking) else -1, J, ok, ranking)


def pareto_mask(complexity, error):
    """Non-dominated points when minimising both objectives."""
    c = np.asarray(complexity, dtype=float)
    e = np.asarray(error, dtype=float)
    ok = np.isfinite(c) & np.isfinite(e)
    mask = ok.copy()
    for i in np.flatnonzero(ok):
        dom = ok & (c <= c[i]) & (e <= e[i]) & ((c < c[i]) | (e < e[i]))
        mask[i] = not dom.any()
    return mask
