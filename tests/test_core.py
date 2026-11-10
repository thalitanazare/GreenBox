import json
from pathlib import Path

import numpy as np
import pytest

from greenbox import calibrated_cost_model, generate_candidates, run, theoretical_cost_model
from greenbox.calibration import fit
from greenbox.data import prepare, read_table
from greenbox.families import FAMILIES, build_candidates
from greenbox.green import model_complexity, pareto_mask
from greenbox.terms import term_to_string


def narx_data(n=600, seed=0):
    rng = np.random.default_rng(seed)
    u = rng.uniform(-1, 1, n)
    y = np.zeros(n)
    for k in range(2, n):
        y[k] = 0.5 * y[k - 1] - 0.2 * y[k - 2] + 0.8 * u[k - 1] + 0.3 * u[k - 1] * y[k - 1]
    return u, y


def test_true_structure_recovered():
    u, y = narx_data()
    out = run({"family": "NARX", "degree": 2, "lag_y": 2, "lag_u": 2, "green_methods": ["weighted"],
               "run_standard": True},
              u[:400], y[:400], u[400:], y[400:])
    std = out["models"][0]
    coef = dict(zip(std["terms"], std["theta"]))
    true = {"y(k-1)": 0.5, "y(k-2)": -0.2, "u(k-1)": 0.8, "u(k-1) * y(k-1)": 0.3}
    for term, value in true.items():
        assert coef[term] == pytest.approx(value, abs=1e-8)
    assert all(abs(v) < 1e-8 for t, v in coef.items() if t not in true)
    assert std["metrics"]["NRMSE"] < 1e-6
    json.dumps(out)  # GUI payload must be JSON-serialisable


@pytest.mark.parametrize("family", list(FAMILIES))
def test_every_family_runs(family):
    u, y = narx_data(300)
    out = run({"family": family, "degree": 2, "lag_y": 2, "lag_u": 3, "lag_e": 2, "noise_terms": 2,
               "els_iterations": 3, "max_terms": 12, "green_methods": ["weighted", "accurate"]},
              u[:200], y[:200], u[200:], y[200:])
    assert len(out["models"]) == 2
    assert out["reference"] == out["models"][-1]["name"]  # most accurate Green model


def test_weight_range_gives_one_model_per_weight():
    """The p = 1, 2, inf methods give one model per accuracy weight; a candidate picked twice is the same model."""
    d = np.loadtxt(Path(__file__).resolve().parents[1] / "examples" / "dc_motor.csv", delimiter=",", skiprows=1)
    u, y = d[:, 0], d[:, 1]
    weights = [0.25, 0.5, 0.75]
    out = run({"family": "NARX", "degree": 2, "lag_y": 2, "lag_u": 2, "green_weights": weights},
              u[:300], y[:300], u[300:], y[300:], continuation=True)
    greens = [m for m in out["models"] if m["kind"] == "green"]
    assert len(greens) == 3 + 3 * len(weights)
    assert len({m["name"] for m in greens}) == len(greens)
    for method in ("weighted", "euclidean", "tchebycheff"):
        assert [m["w_E"] for m in greens if m["method"] == method] == weights
    by_key = {}
    for m in greens:
        by_key.setdefault(m["model_key"], []).append(m)
    for same in by_key.values():
        assert all(m["terms"] == same[0]["terms"] and m["complexity"] == same[0]["complexity"] for m in same)
    json.dumps(out)


def test_family_presets():
    fir = build_candidates("FIR", lag_u=4, u_lag0=True, constant=False)  # explicit: no constant
    assert [term_to_string(r) for r in fir] == ["u(k)", "u(k-1)", "u(k-2)", "u(k-3)", "u(k-4)"]
    volterra = build_candidates("Volterra", degree=2, lag_u=2, u_lag0=True, constant=False)
    assert len(volterra) == 3 + 6  # linear + quadratic kernels in u(k), u(k-1), u(k-2)


def test_noise_terms_last():
    m = generate_candidates(2, [1], [1], [1])
    noise = np.any(m // 1000 == 3, axis=1)
    assert not noise[: (~noise).sum()].any()


def test_cost_models_rho():
    assert theoretical_cost_model("karatsuba", 64, karatsuba_exponent=np.log2(3)).rho == pytest.approx(729 / 64)
    assert theoretical_cost_model("schoolbook", 64).rho == pytest.approx(64)
    assert theoretical_cost_model("uniform").rho == 1
    m = np.array([[0, 0], [1001, 0], [2001, 1001]])  # 1 + y(k-1) + u(k-1)y(k-1): a=2, b=3
    assert model_complexity(m, theoretical_cost_model("uniform")) == 5
    cal = calibrated_cost_model(10.0, 30.0)
    assert model_complexity(m, cal) == pytest.approx(2 * 10 + 3 * 30)
    assert cal.carbon_mg(1e9) == pytest.approx(cal.emission_mg_per_s)  # 1e9 ns = 1 s


def test_calibration_fit_recovers_costs():
    pts = [(a, b, 5 + 2 * a + 7 * b) for a in range(1, 8) for b in range(a, a + 6)]
    res = fit(pts)
    assert res["t_add_ns"] == pytest.approx(2) and res["t_mult_ns"] == pytest.approx(7)
    assert res["rho"] == pytest.approx(3.5) and res["valid"]


def test_pareto():
    assert pareto_mask([1, 2, 3, 2], [3, 2, 1, 3]).tolist() == [True, True, True, False]


def test_read_table_and_prepare():
    names, arr = read_table("u;y\n1;2\n3;4\n5;6\n")
    assert names == ["u", "y"] and arr.shape == (3, 2)
    u, y, scale = prepare(arr[:, 0], arr[:, 1], step=2, normalise="maxabs")
    assert u.tolist() == [0.2, 1.0] and scale["y"][1] == 6


def _example(name):
    from pathlib import Path
    return np.loadtxt(Path(__file__).resolve().parents[1] / "examples" / name, delimiter=",", skiprows=1)


def test_structure_search_reaches_target_r2():
    d = _example("dc_motor.csv")
    u, y, _ = prepare(d[:, 0], d[:, 1], normalise="maxabs")
    out = run({"family": "NARX", "structure_search": True, "size_selection": "simulation",
               "green_methods": ["weighted"], "run_standard": True}, u[:250], y[:250], u[250:], y[250:], continuation=True)
    std = out["models"][0]
    assert out["search"]["reached"]
    assert std["metrics"]["R2"] > 0.9


def test_dependent_terms_are_skipped():
    # binary input: u^2 == u, so u(k-1)^2 must never enter the model
    rng = np.random.default_rng(3)
    u = (rng.uniform(size=400) > 0.5).astype(float)
    y = np.zeros(400)
    for k in range(1, 400):
        y[k] = 0.6 * y[k - 1] + 0.5 * u[k - 1] + 0.01 * rng.normal()
    out = run({"family": "NARX", "degree": 2, "lag_y": 1, "lag_u": 1, "max_terms": 6,
               "green_methods": ["cheapest", "accurate"]}, u[:300], y[:300], u[300:], y[300:])
    for m in out["models"]:
        assert "u(k-1)^2" not in m["terms"]
        assert all(np.isfinite(m["theta"]))


def test_linearity_check():
    from greenbox.linearity import check
    rng = np.random.default_rng(1)
    u = rng.normal(size=1500)
    y_lin, y_nl = np.zeros(1500), np.zeros(1500)
    for k in range(2, 1500):
        y_lin[k] = 1.5 * y_lin[k - 1] - 0.7 * y_lin[k - 2] + 0.5 * u[k - 1] + 0.02 * rng.normal()
        y_nl[k] = 0.5 * y_nl[k - 1] + 0.8 * u[k - 1] + 0.4 * u[k - 1] ** 2 - 0.3 * y_nl[k - 1] * u[k - 1]
    assert check(u, y_lin, max_degree=2, max_lag=2)["label"] == "linear"
    assert check(u, y_nl, max_degree=2, max_lag=2)["label"] == "nonlinear"
