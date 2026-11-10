"""Regression tests: the classical procedure (ERR ranking, AIC size, no parsimony, linearly
dependent candidates kept) must keep giving the same models on the bundled examples."""

from pathlib import Path

import numpy as np
import pytest

from greenbox import ComplexitySettings, choose_green_size, genterms, green_term_order, orthreg, run
from greenbox.data import apply_scale, prepare
from greenbox.metrics import information_criterion
from greenbox.terms import info

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
# the classical procedure of the original Green-Box, with its Karatsuba cost
CLASSICAL = {"family": "NARX", "max_terms": 80, "green_methods": ["weighted"], "green_weight": 0.5, "cost_model": "karatsuba",
             "drop_dependent": False, "green_size_tolerance": 0, "green_selection": "classical",
             "run_standard": True}  # classical Standard model as the first model


def dc_motor():
    d = np.loadtxt(EXAMPLES / "dc_motor.csv", delimiter=",", skiprows=1)
    u, y, _ = prepare(d[:, 0], d[:, 1], normalise="maxabs")
    return u[:250], y[:250], u[250:], y[250:]


def test_genterms_size_and_order():
    m = genterms(2, 2, 2, 0)
    assert m.shape == (15, 2)
    assert m[:5].tolist() == [[0, 0], [1001, 0], [1002, 0], [2001, 0], [2002, 0]]
    assert m[5].tolist() == [1001, 1001]


@pytest.mark.parametrize("case, cfg, cont, expected", [
    (dc_motor, {"degree": 2, "lag_y": 2, "lag_u": 2}, True,
     [(13, 0.1140268281637819, 15350.274002914011), (6, 0.3035484004608956, 4694.682200874204)]),
])
def test_classical_procedure_is_stable(case, cfg, cont, expected):
    out = run({**CLASSICAL, **cfg}, *case(), continuation=cont)
    for m, (k, nrmse, comp) in zip(out["models"], expected):
        assert m["n_terms"] == k
        np.testing.assert_allclose(m["metrics"]["NRMSE"], nrmse, rtol=1e-6)
        np.testing.assert_allclose(m["complexity"], comp, rtol=1e-12)


def test_building_blocks_agree_with_pipeline():
    ut, yt, uv, yv = dc_motor()
    terms = genterms(2, 2, 2, 0)
    _, _, lag = info(terms)
    aic = {k: information_criterion(orthreg(terms, ut, yt, k, drop_dependent=False).residuals[lag:], k)
           for k in range(2, len(terms) + 1)}
    assert min(aic, key=aic.get) == 13
    cs = ComplexitySettings(64, 1.585)
    order, err = green_term_order(terms, ut, yt, len(terms), 0.5, 0.5, cs, drop_dependent=False)
    sel = choose_green_size(terms, order, err, 2, len(terms), "weighted", 0.5, 0.5, cs)
    assert sel.k_best == 6
