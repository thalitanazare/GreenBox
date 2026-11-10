"""Polynomial model families as presets of the candidate generator.

Every family here is linear in the parameters, so they all share the same
ERR / Green selection machinery; they only differ in which lagged factors
and which polynomial degree feed the candidate library. New families are
added by appending a ``Family`` to ``FAMILIES``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .terms import generate_candidates


@dataclass
class Family:
    key: str
    label: str
    description: str
    # fixed values override user input; None means the user chooses
    degree: int | None = None
    lag_y: int | None = None
    lag_e: int | None = None
    # defaults shown in the GUI
    default_degree: int = 2
    default_lag_y: int = 2
    default_lag_u: int = 2
    default_lag_e: int = 0
    default_u_lag0: bool = False
    default_constant: bool = True
    linear: bool = False            # degree fixed to 1
    search_max_lag: int = 20        # default lag limit of the automatic structure search


FAMILIES = {
    f.key: f
    for f in [
        Family("NARX", "NARX", "Polynomial NARX: y(k) = F[y(k-1..ny), u(k-1..nu)]", lag_e=0,
               default_degree=2),
        Family("NARMAX", "NARMAX", "Polynomial NARMAX: NARX plus a noise model in e(k), "
               "estimated by extended least squares", default_lag_e=2),
        Family("ARX", "ARX (linear)", "Linear ARX: degree 1, no noise model", degree=1, lag_e=0,
               linear=True, search_max_lag=30),
        Family("ARMAX", "ARMAX (linear)", "Linear ARMAX: degree 1 with a moving-average noise model",
               degree=1, linear=True, search_max_lag=30, default_lag_e=2),
        Family("FIR", "FIR", "Finite impulse response: y(k) = sum b_i u(k-i)", degree=1, lag_y=0,
               lag_e=0, default_lag_u=10, default_u_lag0=True, linear=True,
               search_max_lag=100),
        Family("Volterra", "Volterra (truncated)", "Discrete Volterra series: products of u(k-i) up to "
               "the given degree, no output feedback", lag_y=0, lag_e=0, default_lag_u=5,
               default_u_lag0=True, search_max_lag=30),
    ]
}


def family_catalog():
    return [asdict(f) for f in FAMILIES.values()]


def build_candidates(family: str, degree=2, lag_y=2, lag_u=2, lag_e=0, u_lag0=False, constant=True):
    f = FAMILIES[family]
    degree = f.degree if f.degree is not None else int(degree)
    lag_y = f.lag_y if f.lag_y is not None else int(lag_y)
    lag_e = f.lag_e if f.lag_e is not None else int(lag_e)
    u_lags = range(0 if u_lag0 else 1, int(lag_u) + 1)
    return generate_candidates(degree, range(1, lag_y + 1), u_lags, range(1, lag_e + 1),
                               include_constant=bool(constant))
