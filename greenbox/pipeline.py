"""End-to-end identification run used by the GUI (and usable from scripts).

    result = run(config, u_train, y_train, u_val, y_val, continuation=True)

Produces one Green model per decision method on the Pareto set (``green_methods``,
see ``greenbox.green.DECISION_METHODS``), and one per accuracy weight in ``green_weights``
for the p = 1, 2, inf methods. Models picked by several methods or weights share
``model_key``. All of them are validated in free run on the
validation data and returns plain Python data ready for JSON. Changes in complexity
and NRMSE are reported relative to the most accurate Green model, named in
``result["reference"]``.

Optional classical Standard model (``run_standard=True``, scripts only; it then
becomes the reference), size by ``size_selection``:
  "criterion"  - ERR ranking, size by AIC/BIC/FPE on one-step residuals
                 (the classical procedure)
  "simulation" - ERR ranking on the first part of the estimation data, size
                 chosen by free-run R^2 on the last part (hold-out). The
                 validation data are never used for any choice.

``structure_search`` chooses the degree and lags by hold-out free-run R^2 with a hill
climb on a lag ladder (``search_structure``) and keeps the simplest structure within
5 % of the best hold-out error; a warning is issued when the best one stays below ``target_r2``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from .families import FAMILIES, build_candidates
from .green import (DECISION_METHODS, DEFAULT_EMISSION_MG_PER_S, METHOD_LABELS, REF_MULT_SECONDS,
                    calibrated_cost_model, choose_from_pool,
                    choose_green_size, cost_efficiency_order, green_term_order, model_complexity,
                    operation_count, pareto_mask, prune_sequence, theoretical_cost_model)
from .linearity import check as linearity_check
from .metrics import fit_metrics, information_criterion, residual_tests
from .ols import housels, orthreg, solve_prefix
from .regressors import build_regressors
from .simulate import free_run, one_step_ahead
from .terms import info, is_noise_term, model_to_strings, n_factors

MAX_HOLDOUT_SAMPLES = None     # samples per simulated window when scoring a model (None: whole record)
MAX_SEARCH_CANDIDATES = 400    # skip libraries larger than this during the search
SEARCH_MAX_TERMS = 60
# Parsimony: prefer the smallest model whose hold-out error (NRMSE = sqrt(1 - R^2))
# is within this relative margin of the best one.
PARSIMONY_SIZE = 0.03          # between model sizes of one structure
PARSIMONY_STRUCTURE = 0.05     # between structures


def _within(r2, best_r2, margin):
    """True when the error of ``r2`` is at most (1 + margin) times the best error."""
    if not np.isfinite(r2):
        return False
    err, best = np.sqrt(max(1 - r2, 0.0)), np.sqrt(max(1 - best_r2, 0.0))
    return err <= best * (1 + margin) + 1e-12


@dataclass
class RunConfig:
    family: str = "NARX"
    degree: int = 2
    lag_y: int = 2
    lag_u: int = 2
    lag_e: int = 0
    u_lag0: bool = False
    constant: bool = True
    min_terms: int = 2
    max_terms: int = 80
    size_selection: str = "criterion"   # criterion | simulation
    criterion: str = "AIC"              # AIC | BIC | FPE
    holdout_fraction: float = 0.3
    structure_search: bool = False
    search_max_degree: int = 3
    search_max_lag: int = 4
    target_r2: float = 0.75               # "very good" free-run R² (NSE > 0.75, Moriasi et al. 2007, Table 4)
    noise_terms: int = 0                # NARMAX / ARMAX only
    els_iterations: int = 10
    run_standard: bool = False          # classical reference model (scripts only; the GUI never uses it)
    green_methods: list = field(default_factory=lambda: list(DECISION_METHODS))  # decision methods on the Pareto set
    green_weight: float = 0.5           # accuracy weight w_E of the p = 1, 2, inf methods (w_C = 1 - w_E)
    green_weights: list = field(default_factory=list)  # several w_E: one model per weight and p method (empty = [green_weight])
    green_epsilon: float = 0.5          # epsilon-constraint: bound on the scaled test error
    # complexity / carbon
    cost_model: str = "uniform"         # uniform | karatsuba | schoolbook | toom3 | schonhage | calibrated
    bits: int = 64
    mult_exponent: float = 1.585        # Karatsuba exponent (log2 3 rounded)
    t_add_ns: float = 0.0               # calibrated timings (cost_model == "calibrated")
    t_mult_ns: float = 0.0
    calibration: dict = field(default_factory=dict)
    ref_mult_seconds: float = REF_MULT_SECONDS
    emission_mg_per_s: float = DEFAULT_EMISSION_MG_PER_S
    residual_lags: int = 25
    drop_dependent: bool = True         # False keeps linearly dependent candidates (classical)
    green_selection: str = "simulation"  # simulation | classical
    green_min_r2: float = 0.75          # Green models must reach this hold-out R² ("very good", Moriasi et al. 2007)
    green_size_tolerance: float = 0.01  # parsimony when choosing the Green size; 0 = classical
    extrapolation_warning: float = 0.10  # warn when the validation input exceeds the estimation range by this share

    @classmethod
    def from_dict(cls, d):
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


WEIGHTED_METHODS = ("weighted", "euclidean", "tchebycheff")  # the methods that use w_E


def green_name(method, w_e=None):
    return f"Green · {METHOD_LABELS[method]}" + ("" if w_e is None else f", w_E = {w_e:g}")


def weight_values(cfg: RunConfig):
    """Accuracy weights of the p = 1, 2, inf methods, clipped to [0, 1], unique and sorted."""
    ws = cfg.green_weights if len(cfg.green_weights) else [cfg.green_weight]
    return sorted({round(float(np.clip(w, 0.0, 1.0)), 6) for w in ws})


def cost_model_from_config(cfg: RunConfig):
    if cfg.cost_model == "calibrated":
        return calibrated_cost_model(cfg.t_add_ns, cfg.t_mult_ns, cfg.emission_mg_per_s, cfg.calibration)
    return theoretical_cost_model(cfg.cost_model, cfg.bits, cfg.mult_exponent, cfg.ref_mult_seconds,
                                  cfg.emission_mg_per_s)


def _candidates(cfg: RunConfig):
    return build_candidates(cfg.family, cfg.degree, cfg.lag_y, cfg.lag_u, cfg.lag_e, cfg.u_lag0, cfg.constant)


def candidate_summary(cfg_dict):
    cfg = RunConfig.from_dict(cfg_dict)
    cand = _candidates(cfg)
    npr, nno, lag = info(cand)
    return {"n_process": npr, "n_noise": nno, "max_lag": lag, "terms": model_to_strings(cand)}


class Validator:
    """Free-run validation that scores every model on the same samples."""

    def __init__(self, u_tr, y_tr, u_va, y_va, continuation, init_len):
        self.continuation = continuation
        if continuation:
            self.u = None if u_tr is None else np.concatenate([u_tr, u_va])
            self.y_init = y_tr
            self.n0 = len(y_tr)
            self.y_true = y_va
        else:
            self.u = u_va
            self.y_init = y_va[:init_len]
            self.n0 = init_len
            self.y_true = y_va[init_len:]
        self.n = self.n0 + len(self.y_true)

    def run(self, model, theta):
        ys = free_run(model, theta, self.u, self.y_init, n=self.n)
        return ys[self.n0:]


# --------------------------------------------------------------------------- Standard size
def size_by_criterion(proc, u, y, min_terms, max_terms, criterion, drop_dependent=True):
    _, _, lag = info(proc)
    P = build_regressors(proc, u, y)[lag:]
    Y = y[lag:]
    st = housels(P, Y, max_terms, drop_dependent=drop_dependent)
    max_terms = st.n_selected
    min_terms = min(min_terms, max_terms)
    ks = np.arange(min_terms, max_terms + 1)
    vals = []
    for k in ks:
        theta, _, _ = solve_prefix(st, k)
        vals.append(information_criterion(Y - P[:, st.piv[:k]] @ theta, k, criterion))
    vals = np.array(vals)
    k_sel = int(ks[int(np.nanargmin(vals))])
    return {"k": ks, "value": vals, "k_selected": k_sel, "criterion": criterion,
            "order": st.piv[:max_terms], "err": st.err[:max_terms], "better": "lower"}


def holdout_split(n, lag, holdout_fraction):
    """(n_fit, end, w0): fit part y[:n_fit], hold-out y[n_fit:end], spanning window y[w0:end].

    By default the hold-out is the whole test part and the spanning window the whole estimation
    record; ``MAX_HOLDOUT_SAMPLES`` limits both to that many samples on each side of the split."""
    n_hold = max(int(round(n * holdout_fraction)), 2 * lag + 10)
    n_fit = n - n_hold
    if MAX_HOLDOUT_SAMPLES is None:
        return n_fit, n, 0
    return n_fit, min(n, n_fit + MAX_HOLDOUT_SAMPLES), max(0, n_fit - MAX_HOLDOUT_SAMPLES)


def holdout_r2(model, theta, u, y, split, lag):
    """Worse of two free-run R^2 values: on the hold-out (started from the measured
    fit data) and on a window that spans the fit part and the hold-out (by default the
    whole estimation record, started from measured values at its start). A model must
    simulate well on both, which rejects models that look good on the hold-out but drift
    or diverge over a long run. ``lag`` is the lag of the candidate library (samples that start the
    window). Returns -inf when the simulation diverges."""
    n_fit, end, w0 = split

    def r2(a, b):
        v = fit_metrics(a, b)["R2"]
        return v if np.isfinite(v) else -np.inf

    ys = free_run(model, theta, None if u is None else u[:end], y[:n_fit], n=end)
    v = r2(y[n_fit:end], ys[n_fit:end])
    if v > -np.inf:
        yw = free_run(model, theta, None if u is None else u[w0:end], y[w0:w0 + lag], n=end - w0)
        v = min(v, r2(y[w0 + lag:end], yw[lag:]))
    return v


def size_by_simulation(proc, u, y, min_terms, max_terms, holdout_fraction):
    """Rank terms on the fit part; choose the size by free-run R^2 (``holdout_r2``)."""
    _, _, lag = info(proc)
    split = holdout_split(len(y), lag, holdout_fraction)
    n_fit, end, _ = split
    if n_fit <= lag + max_terms + 5:
        raise ValueError("Too few estimation samples for a training/test split; use the information criterion.")
    P = build_regressors(proc, u, y)
    st = housels(P[lag:n_fit], y[lag:n_fit], max_terms)
    max_terms = st.n_selected
    min_terms = min(min_terms, max_terms)

    ks, score = [], []
    best_k, best = None, -np.inf
    for k in range(min_terms, max_terms + 1):
        theta, _, _ = solve_prefix(st, k)
        v = holdout_r2(proc[st.piv[:k]], theta, u, y, split, lag)
        ks.append(k)
        score.append(v)
        if v > best:
            best, best_k = v, k
        elif best_k is not None and k - best_k >= 15:  # no improvement for a while
            break
    score = np.array(score)
    k_sel = next(k for k, v in zip(ks, score) if _within(v, best, PARSIMONY_SIZE)) if np.isfinite(best) else ks[0]
    return {"k": np.array(ks), "value": np.where(np.isfinite(score), score, np.nan), "k_selected": int(k_sel),
            "criterion": "free-run R² on the test part", "order": st.piv[:max_terms], "err": st.err[:max_terms],
            "better": "higher", "holdout_r2": float(best) if np.isfinite(best) else None,
            "n_fit": n_fit, "n_holdout": end - n_fit}


# --------------------------------------------------------------------------- Green candidates
GREEN_POOL_WEIGHTS = (0.25, 0.5, 0.75)  # classical Green term orders added to the pool


class HoldoutScorer:
    """Hold-out free-run R^2 (``holdout_r2``) of any subset of candidate terms, with
    coefficients estimated on the fit part. Scores are cached by term set."""

    def __init__(self, proc, u, y, holdout_fraction):
        self.proc, self.u, self.y = proc, u, y
        self.lag = info(proc)[2]
        self.split = holdout_split(len(y), self.lag, holdout_fraction)
        self.n_fit = self.split[0]
        if self.n_fit <= self.lag + 10:
            raise ValueError("Too few estimation samples for a training/test split.")
        self.P = build_regressors(proc, u, y)
        self.cache = {}

    def fit_part(self):
        return (None if self.u is None else self.u[:self.n_fit]), self.y[:self.n_fit]

    def score(self, key):
        if key not in self.cache:
            idx = list(key)
            A = self.P[self.lag:self.n_fit, idx]
            theta = np.linalg.lstsq(A, self.y[self.lag:self.n_fit], rcond=None)[0]
            self.cache[key] = holdout_r2(self.proc[idx], theta, self.u, self.y, self.split, self.lag)
        return self.cache[key]


def distinct_candidates(P, proc, cs, decimals=9):
    """Indices of the candidates worth ranking: a column proportional to a cheaper one
    (or, at equal cost, an earlier one) is dropped, e.g. u^2 = u for a 0/1 input.
    Identical columns tie in every ranking and rounding would pick among them."""
    cost = cs.add_cost + n_factors(proc) * cs.mult_cost
    seen, keep = set(), []
    for j in sorted(range(P.shape[1]), key=lambda j: (cost[j], j)):
        col = P[:, j]
        nz = np.flatnonzero(col)
        if not len(nz):
            continue
        v = col / (np.linalg.norm(col) * np.sign(col[nz[0]]))
        h = np.round(v, decimals).tobytes()
        if h not in seen:
            seen.add(h)
            keep.append(j)
    return np.array(sorted(keep), dtype=int)


GREEN_PREFIX_PATIENCE = 15  # stop a term order after this many sizes without a better hold-out error


def green_pool(proc, u, y, min_terms, max_terms, cfg: RunConfig, cs):
    """Candidate models of one structure for the Green choice (independent of the Standard model).

    Nested models from several term orders, all ranked on the fit part: ERR, ERR per unit
    of cost, and the classical Green order for a few weights; plus the backward elimination
    (by error alone and by error per unit of cost saved) of the most accurate of these models.
    Each is scored by hold-out free-run simulation. Returns {term set (tuple of proc rows):
    (hold-out R^2, complexity)} and the hold-out split size.
    """
    sc = HoldoutScorer(proc, u, y, cfg.holdout_fraction)
    min_terms = min(min_terms, max_terms)
    pool = {}

    def add(idx):
        key = tuple(sorted(int(i) for i in idx))
        if len(key) >= min_terms and key not in pool:
            pool[key] = (sc.score(key), model_complexity(proc[list(key)], cs))
        return pool.get(key, (-np.inf, 0))[0]

    def add_prefixes(order):
        best, since = -np.inf, 0
        for k in range(1, len(order) + 1):
            v = add(order[:k])
            if v > best:
                best, since = v, 0
            elif k >= min_terms:
                since += 1
                if since >= GREEN_PREFIX_PATIENCE:
                    break

    u_fit, y_fit = sc.fit_part()
    lag = sc.lag
    keep = distinct_candidates(sc.P[lag:sc.n_fit], proc, cs)
    lib = proc[keep]
    n_cap = min(max_terms, len(keep))
    add_prefixes(keep[housels(sc.P[lag:sc.n_fit, keep], y_fit[lag:], n_cap,
                              drop_dependent=cfg.drop_dependent).piv[:n_cap]])
    add_prefixes(keep[cost_efficiency_order(lib, u_fit, y_fit, n_cap, cs, cfg.drop_dependent)[0]])
    for w in GREEN_POOL_WEIGHTS:
        add_prefixes(keep[green_term_order(lib, u_fit, y_fit, n_cap, w, 1 - w, cs, cfg.drop_dependent)[0]])
    if pool:
        top = max(pool, key=lambda k: (pool[k][0], -len(k)))
        for costs in (cs, None):
            for subset in prune_sequence(sc.P[lag:sc.n_fit], y_fit[lag:], list(top), n_factors(proc), costs):
                add(subset)
    return {tuple(map(tuple, proc[list(k)].tolist())): v for k, v in pool.items()}, sc.n_fit


# --------------------------------------------------------------------------- structure search
# Lags tried by the structure search: every lag up to 6, then a geometric ladder, so long
# memories (FIR or Volterra models of slow systems need tens of lags) stay reachable.
LAG_LADDER = (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 60, 80, 100)
SEARCH_MOVES = ((1, 0), (0, 1), (1, 1), (2, 0), (0, 2), (-1, 0), (0, -1))  # ladder steps in (lag_y, lag_u)
SEARCH_COARSE = (1, 2, 4, 8, 15, 30, 60, 100)  # lags of the initial linear scan
SEARCH_MIN_GAIN = 0.005        # a move must lower the hold-out error (NRMSE) by at least 0.5 %


def _error(r2):
    """Hold-out NRMSE from R^2 (inf when the model diverged)."""
    return float(np.sqrt(max(1.0 - r2, 0.0))) if np.isfinite(r2) else np.inf


def search_structure(cfg: RunConfig, u, y, progress, scorer=None, label="Structure search"):
    """Choose the degree and the lags by hold-out free-run R^2 (estimation data only).

First a coarse scan of linear (degree-1) structures over ``SEARCH_COARSE`` locates the
    memory of the system, also when short lags explain nothing (fast sampling). Then, for
    every degree, a hill climb on the lag ladder (``LAG_LADDER`` up to ``search_max_lag``)
    starts from one output and one input lag and from the best coarse point, and moves to
    the best neighbouring structure (one or two ladder steps up, or one step down, in the
    output lags, the input lags or both) while the hold-out error decreases by more than
    ``SEARCH_MIN_GAIN``.
    Each structure is scored by ``scorer(c, proc)`` -> (best hold-out R^2, number of terms):
    by default the Standard procedure (``size_by_simulation``); the Green search passes its
    own pool (``green_pool``). Libraries above ``MAX_SEARCH_CANDIDATES`` terms are skipped. Among all structures
    visited, the smallest library whose hold-out error is within ``PARSIMONY_STRUCTURE``
    of the best one is chosen.
    """
    fam = FAMILIES[cfg.family]
    degrees = [fam.degree] if fam.degree is not None else list(range(1, cfg.search_max_degree + 1))
    ladder = [lag for lag in LAG_LADDER if lag <= cfg.search_max_lag] or [1]
    fixed_y = fam.lag_y is not None
    cache = {}

    def score(d, iy, iu):
        ly, lu = (fam.lag_y if fixed_y else ladder[iy]), ladder[iu]
        if (d, ly, lu) in cache:
            return cache[(d, ly, lu)]
        c = replace(cfg, degree=d, lag_y=ly, lag_u=lu)
        proc = _candidates(c)
        proc = proc[: info(proc)[0]]
        row = None
        if len(proc) <= MAX_SEARCH_CANDIDATES:
            progress(f"{label}: degree {d}, output lags {ly}, input lags {lu}",
                     0.02 + 0.5 * min(len(cache) / 60, 1))
            max_terms = min(c.max_terms, len(proc), SEARCH_MAX_TERMS)
            try:
                if scorer is None:
                    st = size_by_simulation(proc, u, y, max(1, min(c.min_terms, max_terms)), max_terms,
                                            c.holdout_fraction)
                    best, terms = st["holdout_r2"], st["k_selected"]
                else:
                    best, terms = scorer(c, proc, max_terms)
                r2 = best if best is not None else -np.inf
                row = {"degree": d, "lag_y": ly, "lag_u": lu, "candidates": len(proc), "terms": terms,
                       "holdout_r2": best, "_e": _error(r2), "_cfg": c}
            except ValueError:
                pass
        cache[(d, ly, lu)] = row
        return row

    def err(row):
        return row["_e"] if row else np.inf

    def climb(d, iy, iu):
        cur = score(d, iy, iu)
        while True:
            best_move, best_e = None, err(cur) * (1 - SEARCH_MIN_GAIN)
            for dy, du in SEARCH_MOVES:
                if fixed_y and dy:
                    continue
                jy, ju = iy + dy, iu + du
                if not (0 <= jy < len(ladder) and 0 <= ju < len(ladder)):
                    continue
                e = err(score(d, jy, ju))
                if e < best_e:
                    best_move, best_e = (jy, ju), e
            if best_move is None:
                break
            iy, iu = best_move
            cur = score(d, iy, iu)

    coarse = [ladder.index(lag) for lag in SEARCH_COARSE if lag in ladder]
    lin = 1 if 1 in degrees else None
    if lin is None and fam.degree is None:
        lin = 1  # nonlinear search without degree 1: the linear scan still locates the memory
    # diagonal (as many output as input lags) plus the edges (one or two lags of one signal)
    edges = [i for i in coarse if ladder[i] <= 2]
    pairs = {(0, iu) for iu in coarse} if fixed_y else (
        {(i, i) for i in coarse} | {(e, i) for e in edges for i in coarse} | {(i, e) for e in edges for i in coarse})
    for iy, iu in sorted(pairs):
        score(lin or degrees[0], iy, iu)
    scanned = [(k, r) for k, r in cache.items() if r]
    start = None
    if scanned:
        (_, ly, lu), _ = min(scanned, key=lambda kr: kr[1]["_e"])
        start = (0 if fixed_y else ladder.index(ly), ladder.index(lu))
    for d in degrees:
        climb(d, 0, 0)
        if start:
            climb(d, *start)

    rows = sorted((r for r in cache.values() if r and r["degree"] in degrees), key=lambda r: (r["candidates"], r["degree"], r["lag_y"] + r["lag_u"]))
    if not rows:
        raise ValueError("Structure search found no usable structure; check the data or the search limits.")
    best = min(r["_e"] for r in rows)
    pick = next(r for r in rows if r["_e"] <= best * (1 + PARSIMONY_STRUCTURE))
    pick["selected"] = True
    chosen = pick["_cfg"]
    for r in rows:
        r.pop("_e"), r.pop("_cfg")
    best_r2 = 1 - best**2 if np.isfinite(best) else None
    return chosen, {"rows": rows, "target_r2": cfg.target_r2,
                    "reached": bool(best_r2 is not None and best_r2 >= cfg.target_r2),
                    "best_holdout_r2": best_r2,
                    "degree": chosen.degree, "lag_y": chosen.lag_y, "lag_u": chosen.lag_u}


def _process_indices(proc, model):
    """Rows of ``proc`` that are the process terms of ``model``."""
    pos = {tuple(r): i for i, r in enumerate(proc.tolist())}
    return [pos[tuple(r)] for r in np.atleast_2d(model)[~is_noise_term(model)].tolist()]


def green_candidates(cfg: RunConfig, u, y, cs, progress):
    """Green identification: the candidate models of every structure visited by the Green
    structure search (or of the fixed structure), scored on the hold-out. It never uses the
    Standard model. Returns arrays over the union of the pools."""
    pools = {}

    def scorer(c, proc, max_terms):
        pool, _ = green_pool(proc, u, y, max(1, min(c.min_terms, max_terms)), max_terms, c, cs)
        pools[(c.degree, c.lag_y, c.lag_u)] = (pool, c)
        finite = [v[0] for v in pool.values() if np.isfinite(v[0])]
        best = max(finite) if finite else None
        return best, (min(len(k) for k, v in pool.items() if v[0] == best) if best is not None else 0)

    search = None
    if cfg.structure_search:
        _, search = search_structure(cfg, u, y, progress, scorer=scorer, label="Green structure search")
    else:
        cand = _candidates(cfg)
        npr = info(cand)[0]
        scorer(cfg, cand[:npr], min(cfg.max_terms, npr))
    keys, r2, comp, struct, noise, seen = [], [], [], [], [], set()
    for (d, ly, lu), (pool, c) in pools.items():
        cand = _candidates(c)
        nz = cand[info(cand)[0]:]
        for k, (v, cc) in pool.items():
            if k in seen:
                continue
            seen.add(k)
            keys.append(k)
            r2.append(v)
            comp.append(cc)
            struct.append({"degree": d, "lag_y": ly, "lag_u": lu})
            noise.append(nz)
    r2 = np.array(r2, dtype=float)
    return {"keys": keys, "r2": r2, "error": np.where(np.isfinite(r2), np.sqrt(np.maximum(1 - r2, 0)), np.inf),
            "complexity": np.array(comp, dtype=float), "n_terms": np.array([len(k) for k in keys]),
            "structure": struct, "noise": noise,
            "search": None if search is None else {k: search[k] for k in ("rows", "degree", "lag_y", "lag_u", "target_r2", "reached",
                                                             "best_holdout_r2")}}


# --------------------------------------------------------------------------- reports
def _model_report(name, kind, res, cs, val: Validator, u_tr, y_tr, cfg, extra=None):
    model, theta = res.model, res.theta
    deployed = model[~is_noise_term(model)]  # noise terms are not evaluated in simulation
    n_add, n_mult = operation_count(deployed)
    C = model_complexity(deployed, cs)
    yhat = val.run(model, theta)
    osa = one_step_ahead(model, theta, u_tr, y_tr)
    lag = res.lag
    resid = res.residuals[lag:]
    tests = residual_tests(resid, None if u_tr is None else u_tr[lag:], cfg.residual_lags)
    out = {
        "name": name,
        "kind": kind,
        "n_terms": int(len(theta)),
        "terms": model_to_strings(model),
        "codes": model.tolist(),
        "theta": theta.tolist(),
        "err": res.err.tolist(),
        "std": res.std.tolist(),
        "n_add": n_add,
        "n_mult": n_mult,
        "complexity": float(C),
        "time_ns_per_sample": float(C * cs.seconds_per_unit * 1e9),
        "carbon_mg_per_sample": float(cs.carbon_mg(C)),
        "carbon_mg_validation": float(cs.carbon_mg(C) * len(val.y_true)),
        "metrics": fit_metrics(val.y_true, yhat),
        "metrics_osa_train": fit_metrics(y_tr[lag:], osa[lag:]),
        "yhat": yhat.tolist(),
        "residual_tests": {"lags": tests["lags"].tolist(), "bound": tests["bound"],
                           "tests": {k: v.tolist() for k, v in tests["tests"].items()}},
        "residual_variance": float(res.variance),
    }
    if extra:
        out.update(extra)
    return out


def run(cfg_dict, u_tr, y_tr, u_va, y_va, continuation=True, progress=None):
    cfg = cfg_in = RunConfig.from_dict(cfg_dict)
    progress = progress or (lambda msg, frac: None)
    y_tr = np.asarray(y_tr, dtype=float)
    y_va = np.asarray(y_va, dtype=float)
    u_tr = None if u_tr is None else np.asarray(u_tr, dtype=float)
    u_va = None if u_va is None else np.asarray(u_va, dtype=float)
    cs = cost_model_from_config(cfg)

    search = None
    if cfg.structure_search and cfg.run_standard:  # Standard identification: its own structure search
        cfg, search = search_structure(cfg, u_tr, y_tr, progress)

    progress("Checking whether the system is linear", 0.53 if search else 0.02)
    lin = linearity_check(u_tr, y_tr, cfg.holdout_fraction, max_lag=3,
                          rows=search["rows"] if search else None)

    cand = _candidates(cfg)
    npr, nno, lag = info(cand)
    proc = cand[:npr]
    noise = cand[npr:]
    max_terms = min(cfg.max_terms, npr)
    min_terms = max(1, min(cfg.min_terms, max_terms))
    n_noise = min(cfg.noise_terms, nno)
    els = cfg.els_iterations if n_noise else 0
    if len(y_tr) <= lag + max_terms:
        raise ValueError(f"Too few training samples ({len(y_tr)}) for lag {lag} and {max_terms} terms.")
    val = Validator(u_tr, y_tr, u_va, y_va, continuation, lag)

    result = {"candidates": {"n_process": npr, "n_noise": nno, "max_lag": lag,
                             "terms": model_to_strings(cand)},
              "structure": {"family": cfg.family, "degree": cfg.degree, "lag_y": cfg.lag_y,
                            "lag_u": cfg.lag_u, "lag_e": cfg.lag_e},
              "search": search,
              "linearity": lin,
              "y_val": val.y_true.tolist(), "val_offset": int(val.n0 if continuation else lag),
              "continuation": bool(continuation), "models": [], "warnings": [], "notes": [],
              "cost_model": {"key": cs.key, "label": cs.label, "add_cost": cs.add_cost,
                             "mult_cost": cs.mult_cost, "rho": cs.rho, "unit": cs.unit,
                             "details": cs.details}}
    if u_tr is not None and u_va is not None and len(u_tr) and len(u_va):
        lo, hi = float(np.min(u_tr)), float(np.max(u_tr))
        span = hi - lo
        beyond = max(float(np.max(u_va)) - hi, lo - float(np.min(u_va)), 0.0) / span if span > 0 else 0.0
        result["extrapolation"] = {"input_beyond_estimation": beyond}
        if beyond > cfg.extrapolation_warning:
            result["warnings"].append(
                f"The validation input goes {100 * beyond:.0f} % beyond the range of the estimation input. "
                "Models are identified within that range; large nonlinear models can extrapolate poorly outside it, "
                "so prefer a cheaper decision method (e.g. Cheapest or ε-constraint) or include such inputs in the estimation data.")
    fam_linear = FAMILIES[cfg.family].linear
    if fam_linear and lin["label"] != "linear":
        result["warnings"].append(
            f"The data indicate a {lin['label']} system, but {cfg.family} models are linear and cannot "
            "represent it: try NARX/NARMAX, or Volterra when no output feedback is wanted.")
    elif not fam_linear and lin["label"] == "linear":
        result["notes"].append("The data indicate a linear system: an ARX model may be as accurate and cheaper.")
    if search and not search["reached"]:
        if fam_linear and lin["label"] != "linear":
            pass  # already explained above
        elif fam_linear:
            result["warnings"].append(
                f"No {cfg.family} structure reached the target test R² of {cfg.target_r2:g} "
                f"(best {search['best_holdout_r2']:.3f}). {cfg.family} models are linear, so this usually means the "
                "system is nonlinear: try NARX/NARMAX, or Volterra when no output feedback is wanted.")
        else:
            result["warnings"].append(
                f"No structure reached the target test R² of {cfg.target_r2:g} "
                f"(best {search['best_holdout_r2']:.3f}); the best one found is used. "
                "Try larger search limits, another model family or more estimation data.")

    # ---------------- Standard ------------------------------------------------------
    if cfg.run_standard:
        progress("Standard model: ranking terms", 0.55 if search else 0.02)
        if cfg.size_selection == "simulation":
            sel = size_by_simulation(proc, u_tr, y_tr, min_terms, max_terms, cfg.holdout_fraction)
            chosen = proc[sel["order"][: sel["k_selected"]]]
            res = orthreg(np.vstack([chosen, noise]) if n_noise else chosen, u_tr, y_tr,
                          sel["k_selected"], n_noise, els)
        else:
            sel = size_by_criterion(proc, u_tr, y_tr, min_terms, max_terms, cfg.criterion, cfg.drop_dependent)
            res = orthreg(np.vstack([proc, noise]) if n_noise else proc, u_tr, y_tr, sel["k_selected"],
                          n_noise, els, cfg.drop_dependent)
        result["standard_curve"] = {"k": sel["k"].tolist(), "value": sel["value"].tolist(),
                                    "criterion": sel["criterion"], "better": sel["better"],
                                    "k_selected": sel["k_selected"], "err_order": sel["err"].tolist(),
                                    "err_terms": model_to_strings(proc[sel["order"]])}
        rep = _model_report("Standard", "standard", res, cs, val, u_tr, y_tr, cfg,
                            {"size_selection": cfg.size_selection, "holdout_r2": (
                                float(sel["value"][list(sel["k"]).index(sel["k_selected"])])
                                if cfg.size_selection == "simulation" else None)})
        result["models"].append(rep)
        r2 = rep["metrics"]["R2"]
        if (r2 is None or not np.isfinite(r2) or r2 < cfg.target_r2) and not (search and not search["reached"]):
            result["warnings"].append(
                f"The Standard model reached validation R² = {r2:.3f}, below {cfg.target_r2:g}. "
                + ("Try the automatic structure search or" if not cfg.structure_search else "Try")
                + " a larger degree/lag range, or the size selection by simulation on the test part."
                if r2 is not None and np.isfinite(r2) else
                "The Standard model diverged in free run on the validation data. "
                "Use the size selection by simulation on the test part or the automatic structure search.")

    # ---------------- Green models (own identification, independent of the Standard model) -----
    methods = [m for m in DECISION_METHODS if m in set(cfg.green_methods)]
    unknown = set(cfg.green_methods) - set(DECISION_METHODS)
    if unknown:
        raise ValueError(f"Unknown decision method(s): {', '.join(sorted(unknown))}")
    weights, eps = weight_values(cfg), float(np.clip(cfg.green_epsilon, 0.0, 1.0))
    w_mid = float(np.clip(cfg.green_weight, 0.0, 1.0))  # w_E of the classical term order for the methods without weights
    # one job per method, and per weight for the methods that use w_E
    jobs = [(m, w, m in WEIGHTED_METHODS and len(weights) > 1)
            for m in methods for w in (weights if m in WEIGHTED_METHODS else [w_mid])]
    base = 0.6 if search else 0.1
    pool = None
    if methods and cfg.green_selection == "simulation":
        progress("Green models: simulating candidate models on the test part", base)
        try:
            pool = green_candidates(cfg_in, u_tr, y_tr, cs, progress)
        except ValueError:
            result["warnings"].append("Too few estimation samples for a training/test split: the Green models use the "
                                      "classical procedure (size from the one-step error reduction).")
    if pool:
        e_lim = float(np.sqrt(1 - np.clip(cfg.green_min_r2, 0.0, 1.0)))
        adm = np.isfinite(pool["error"]) & (pool["error"] <= e_lim)
        result["green_pool"] = {
            "n_terms": pool["n_terms"], "complexity": pool["complexity"],
            "holdout_r2": np.where(np.isfinite(pool["r2"]), pool["r2"], np.nan), "admissible": adm,
            "terms": [model_to_strings(np.array(k)) for k in pool["keys"]], "structure": pool["structure"],
            "min_r2": cfg.green_min_r2, "r2_limit": 1 - e_lim**2, "search": pool["search"]}
        if not adm.any():
            result["warnings"].append(
                f"No candidate model reached the minimum test R² of {cfg.green_min_r2:g} for Green models: "
                "each Green model is the most accurate candidate that simulates.")
    fits = {}
    y_dev = np.max(np.abs(y_tr - y_tr.mean())) if len(y_tr) else 0.0

    def fit_green(j):
        """Estimate on all estimation data; stable = bounded free run over the estimation record."""
        if j not in fits:
            chosen = np.array(pool["keys"][j])
            gnoise = pool["noise"][j]
            gn = min(cfg.noise_terms, len(gnoise))
            res = orthreg(np.vstack([chosen, gnoise]) if gn else chosen, u_tr, y_tr, len(chosen),
                          gn, cfg.els_iterations if gn else 0, cfg.drop_dependent)
            ys = free_run(res.model, res.theta, u_tr, y_tr[:res.lag], n=len(y_tr))
            fits[j] = (res, bool(np.all(np.isfinite(ys)) and np.max(np.abs(ys - y_tr.mean())) <= 10 * y_dev))
        return fits[j]

    unstable = False
    reports = {}  # the same candidate picked by several methods or weights is validated once
    for i, (method, w_e, tag) in enumerate(jobs):
        name = green_name(method, w_e if tag else None)
        progress(name, base + (0.95 - base) * i / max(len(jobs), 1))
        about = {"method": method, "method_label": METHOD_LABELS[method], "w_E": w_e, "epsilon": eps}
        if pool:
            ch = choose_from_pool(pool["error"], pool["complexity"], method, e_lim, w_e, eps)
            finite = np.flatnonzero(np.isfinite(pool["error"]))
            ranking = list(ch.ranking) or sorted(finite, key=lambda j: pool["error"][j])
            pick = next((j for j in ranking if fit_green(j)[1]), None)
            if pick is None:
                pick, unstable = ranking[0], True
            cost = np.where(np.isfinite(ch.cost), ch.cost, np.nan)
            if pick in reports:
                rep = {**reports[pick], "name": name, **about}
                rep["selection"] = {**rep["selection"], "cost": cost}
                result["models"].append(rep)
                continue
            res = fit_green(pick)[0]
            extra = {"model_key": f"pool:{int(pick)}", "holdout_r2": pool["r2"][pick] if np.isfinite(pool["r2"][pick]) else None,
                     "structure": pool["structure"][pick],
                     "selection": {"method": "simulation", "index": int(pick),
                                   "cost": cost}}
        else:
            order, err_order = green_term_order(proc, u_tr, y_tr, max_terms, w_e, 1.0 - w_e, cs, cfg.drop_dependent)
            sel = choose_green_size(proc, order, err_order, min_terms, max_terms, method, w_e, eps, cs,
                                    cfg.green_size_tolerance)
            chosen = proc[order[: sel.k_best]]
            res = orthreg(np.vstack([chosen, noise]) if n_noise else chosen, u_tr, y_tr, sel.k_best,
                          n_noise, els, cfg.drop_dependent)
            extra = {"model_key": "classical:" + ",".join(map(str, sorted(order[: sel.k_best]))),
                     "selection": {"method": "classical", "k": sel.k.tolist(),
                                   "cumulative_err": sel.cumulative_err.tolist(),
                                   "complexity": sel.complexity.tolist(), "cost": sel.cost.tolist(),
                                   "k_selected": sel.k_best}}
        gval = Validator(u_tr, y_tr, u_va, y_va, continuation, res.lag)
        rep = _model_report(name, "green", res, cs, gval, u_tr, y_tr, cfg, {**about, **extra})
        if pool:
            reports[pick] = rep
        result["models"].append(rep)
    if unstable:
        result["warnings"].append("No admissible candidate model stayed bounded in free run over the estimation "
                                  "data for some Green models; inspect them before use.")

    # ---------------- comparisons --------------------------------------------------
    std = next((m for m in result["models"] if m["kind"] == "standard"), None)
    ref = std
    if ref is None:  # no Standard model: compare with the most accurate Green model
        greens = [m for m in result["models"] if m["kind"] == "green"]
        ref = (next((m for m in greens if m["method"] == "accurate"), None)
               or max(greens, key=lambda m: m["complexity"])) if greens else None
        if ref is not None:
            r2 = ref["metrics"]["R2"]
            if (r2 is None or not np.isfinite(r2) or r2 < cfg.target_r2) and not (search and not search["reached"]):
                fits_test = ref.get("holdout_r2") is not None and ref["holdout_r2"] >= cfg.target_r2
                result["warnings"].append(
                    (f"The most accurate Green model ({ref['name']}) fits the estimation data (test R² = "
                     f"{ref['holdout_r2']:.3f}) but reaches only R² = {r2:.3f} on the validation data, which may differ "
                     "from the estimation data. Compare the cheaper Green models: they are often more robust."
                     if fits_test else
                     f"The most accurate Green model ({ref['name']}) reached validation R² = {r2:.3f}, "
                     f"below {cfg.target_r2:g}. Try a larger degree/lag range or the automatic structure search.")
                    if r2 is not None and np.isfinite(r2) else
                    f"The most accurate Green model ({ref['name']}) diverged in free run on the validation data.")
    result["reference"] = ref["name"] if ref is not None else None
    for m in result["models"]:
        if ref is None:
            continue
        if m is ref:
            if std is None:
                m["complexity_reduction_pct"] = 0.0
                m["nrmse_change_pct"] = 0.0
            continue
        m["complexity_reduction_pct"] = (100 * (ref["complexity"] - m["complexity"]) / ref["complexity"]
                                         if ref["complexity"] else None)
        a, b = m["metrics"]["NRMSE"], ref["metrics"]["NRMSE"]
        m["nrmse_change_pct"] = 100 * (a - b) / b if np.isfinite(a) and np.isfinite(b) and b else None
    mask = pareto_mask([m["complexity"] for m in result["models"]],
                       [m["metrics"]["NRMSE"] for m in result["models"]])
    diverged = []
    for m, p in zip(result["models"], mask):
        m["pareto"] = bool(p)
        if m["kind"] == "green" and not np.isfinite(m["metrics"]["NRMSE"]):
            diverged.append(m["name"])
    if diverged:
        result["warnings"].append(f"Free-run simulation diverged on validation data: {', '.join(diverged)}.")
    progress("Done", 1.0)
    return to_jsonable(result)


def to_jsonable(x):
    if isinstance(x, dict):
        return {k: to_jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return to_jsonable(x.tolist())
    if isinstance(x, (np.floating, float)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x
