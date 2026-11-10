"""Identification test battery: bundled examples plus synthetic systems with known equations.

Every case is run with the settings the GUI uses when the user only presses Next
(automatic structure search, hold-out simulation size, family defaults), so the
battery measures what users actually get. Used by tests/test_battery.py and by

    python tools/run_battery.py            # table of results
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from greenbox import webapi
from greenbox.families import FAMILIES
from greenbox.green import DECISION_METHODS
from greenbox.pipeline import run

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
METHODS = list(DECISION_METHODS)  # decision methods on the Pareto set


def gui_defaults(family="NARX", **structure):
    """Run configuration the GUI sends for ``family`` when nothing is changed."""
    f = FAMILIES[family]
    cfg = {
        "family": family, "degree": f.degree or f.default_degree, "lag_y": f.default_lag_y if f.lag_y is None else f.lag_y,
        "lag_u": f.default_lag_u, "lag_e": f.default_lag_e if f.lag_e is None else f.lag_e,
        "u_lag0": f.default_u_lag0, "constant": f.default_constant,
        "run_standard": False, "structure_search": True,
        "search_max_degree": 3, "search_max_lag": f.search_max_lag, "target_r2": 0.75,
        "size_selection": "simulation", "holdout_fraction": 0.3, "drop_dependent": True,
        "min_terms": 2, "max_terms": 80, "noise_terms": 3 if f.lag_e is None else 0, "els_iterations": 10,
        "green_methods": list(DECISION_METHODS), "residual_lags": 25, "cost_model": "uniform", "bits": 64,
        "mult_exponent": 1.585,
    }
    cfg.update(structure)
    return cfg


@dataclass
class Case:
    key: str
    description: str
    data: tuple                  # u_tr, y_tr, u_va, y_va, continuation
    cfg: dict
    true_terms: list = field(default_factory=list)
    kind: str = "synthetic"      # example | synthetic


# --------------------------------------------------------------------------- bundled examples
def _example(key, file, setup):
    text = (EXAMPLES / file).read_text(encoding="utf-8")
    webapi.load_text(key, file, text)
    spec = {"mode": setup["mode"], "u": {"file": key, "col": setup["u"]}, "y": {"file": key, "col": setup["y"]},
            "continuation": setup.get("continuation", False), "normalise": setup["normalise"],
            "n_train": setup.get("n_train"), "start": 0, "stop": None, "step": 1}
    if setup["mode"] == "separate":
        spec["u_val"] = {"file": key, "col": setup["uv"]}
        spec["y_val"] = {"file": key, "col": setup["yv"]}
    u_tr, y_tr, u_va, y_va, cont, _ = webapi._datasets(spec)
    return u_tr, y_tr, u_va, y_va, cont


def example_cases():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_web", ROOT / "tools" / "build_web.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = []
    for key, ex in mod.EXAMPLES.items():
        s = ex["setup"]
        st = dict(s["structure"])
        cfg = gui_defaults(st.pop("family"), **st)
        idf = s.get("identify", {})
        if idf.get("structure") == "manual":
            cfg["structure_search"] = False
        out.append(Case(key, f"bundled example {ex['file']}", _example(key, ex["file"], s), cfg, kind="example"))
    return out


# --------------------------------------------------------------------------- inputs
def steps(rng, n, hold=8):
    return np.repeat(rng.uniform(-1, 1, n // hold + 1), hold)[:n]


def white(rng, n):
    return rng.uniform(-1, 1, n)


def binary(rng, n, hold=3):
    return np.repeat(rng.choice([-1.0, 1.0], n // hold + 1), hold)[:n]


def sines(rng, n):
    k = np.arange(n)
    return 0.5 * np.sin(2 * np.pi * k / 50) + 0.3 * np.sin(2 * np.pi * k / 17 + 1) + 0.2 * np.sin(2 * np.pi * k / 7 + 2)


def simulate(step, u, lag=2):
    y = np.zeros(len(u))
    for k in range(lag, len(u)):
        y[k] = step(y, u, k)
    return y


def split(u, y, frac=0.5):
    n = int(len(y) * frac)
    return u[:n], y[:n], u[n:], y[n:], True


def add_noise(rng, y, snr_db):
    """White measurement noise at the given signal-to-noise ratio (dB)."""
    s = np.std(y)
    return y + s * 10 ** (-snr_db / 20) * rng.normal(size=len(y))


# --------------------------------------------------------------------------- synthetic systems
def lin1(y, u, k):
    return 0.8 * y[k - 1] + 0.4 * u[k - 1]


def lin2(y, u, k):  # poles 0.75 +- 0.37j
    return 1.5 * y[k - 1] - 0.7 * y[k - 2] + 0.5 * u[k - 1] + 0.3 * u[k - 2]


def nl1(y, u, k):
    return 0.6 * y[k - 1] + 0.5 * u[k - 1] - 0.3 * y[k - 1] * u[k - 1] + 0.2 * u[k - 1] ** 2


def nl2(y, u, k):  # the Home page system
    return (0.75 * y[k - 1] - 0.20 * y[k - 2] + 0.50 * u[k - 1] + 0.25 * u[k - 2]
            - 0.30 * y[k - 1] * u[k - 1] + 0.15 * u[k - 1] ** 2 - 0.10 * y[k - 1] ** 3)


def resonant(y, u, k):  # lightly damped (poles at radius 0.97) with a cubic stiffness
    return 1.84 * y[k - 1] - 0.9409 * y[k - 2] + 0.05 * u[k - 1] - 0.02 * y[k - 1] ** 3


def tank(y, u, k):  # non-polynomial: outflow ~ sqrt(level)
    return y[k - 1] - 0.25 * np.sqrt(max(y[k - 1], 0.0)) + 0.2 * u[k - 1]


TERMS_NL2 = ["y(k-1)", "y(k-2)", "u(k-1)", "u(k-2)", "u(k-1) * y(k-1)", "u(k-1)^2", "y(k-1)^3"]


def synthetic_cases():
    out = []
    rng = np.random.default_rng(2026)

    def add(key, desc, u, y, family="NARX", true=(), frac=0.5, **cfg):
        out.append(Case(key, desc, split(u, y, frac), gui_defaults(family, **cfg), list(true)))

    u = white(rng, 1000)
    add("lin1_white_low", "1st-order linear, white input, SNR 40 dB", u, add_noise(rng, simulate(lin1, u), 40),
        "ARX", ["y(k-1)", "u(k-1)"])
    u = steps(rng, 1000)
    add("lin2_steps_high", "2nd-order linear, steps, SNR 15 dB", u, add_noise(rng, simulate(lin2, u), 15),
        "ARX", ["y(k-1)", "y(k-2)", "u(k-1)", "u(k-2)"])
    u = steps(rng, 1000)
    add("lin2_narx", "2nd-order linear identified with NARX defaults, SNR 30 dB", u,
        add_noise(rng, simulate(lin2, u), 30), "NARX", ["y(k-1)", "y(k-2)", "u(k-1)", "u(k-2)"])
    u = white(rng, 1000)
    add("nl1_white_low", "1st-order NARX, white input, SNR 40 dB", u, add_noise(rng, simulate(nl1, u), 40),
        "NARX", ["y(k-1)", "u(k-1)", "u(k-1) * y(k-1)", "u(k-1)^2"])
    u = steps(rng, 1000, hold=4)
    add("nl2_steps_low", "2nd-order NARX, steps, SNR 40 dB", u, add_noise(rng, simulate(nl2, u), 40),
        "NARX", TERMS_NL2)
    u = steps(rng, 1000, hold=4)
    add("nl2_steps_high", "2nd-order NARX, steps, SNR 10 dB", u, add_noise(rng, simulate(nl2, u), 10),
        "NARX", TERMS_NL2)
    u = binary(rng, 1000)
    add("nl2_binary", "2nd-order NARX, binary input (u^2 = 1), SNR 30 dB", u,
        add_noise(rng, simulate(nl2, u), 30), "NARX", TERMS_NL2)
    u = sines(rng, 1000)
    add("nl1_sines", "1st-order NARX, sum of three sines, SNR 30 dB", u, add_noise(rng, simulate(nl1, u), 30),
        "NARX", ["y(k-1)", "u(k-1)", "u(k-1) * y(k-1)", "u(k-1)^2"])

    # coloured equation noise: y = NARX + e(k) + 0.8 e(k-1) (needs a noise model)
    u = steps(rng, 1200, hold=4)
    e = 0.05 * rng.normal(size=1200)
    y = np.zeros(1200)
    for k in range(2, 1200):
        y[k] = nl1(y, u, k) + e[k] + 0.8 * e[k - 1]
    add("narmax_coloured", "1st-order NARX with MA(1) coloured noise, NARMAX defaults", u, y, "NARMAX",
        ["y(k-1)", "u(k-1)", "u(k-1) * y(k-1)", "u(k-1)^2"])
    add("narmax_as_narx", "same coloured-noise data with NARX defaults", u, y, "NARX",
        ["y(k-1)", "u(k-1)", "u(k-1) * y(k-1)", "u(k-1)^2"])

    u = white(rng, 1000)
    b = [0.5, 0.8, 0.4, 0.15, -0.1, 0.05]
    y = np.convolve(u, b)[:1000]
    add("fir_white", "FIR of 6 taps, white input, SNR 30 dB", u, add_noise(rng, y, 30), "FIR",
        ["u(k)", "u(k-1)", "u(k-2)", "u(k-3)", "u(k-4)", "u(k-5)"])
    u = white(rng, 1000)
    y = np.zeros(1000)
    for k in range(2, 1000):
        y[k] = 0.6 * u[k] + 0.3 * u[k - 1] - 0.2 * u[k - 2] + 0.4 * u[k] * u[k - 1] - 0.25 * u[k - 1] ** 2
    add("volterra_white", "2nd-degree Volterra, white input, SNR 30 dB", u, add_noise(rng, y, 30), "Volterra",
        ["u(k)", "u(k-1)", "u(k-2)", "u(k) * u(k-1)", "u(k-1)^2"])

    u = steps(rng, 160, hold=4)
    add("nl2_few", "2nd-order NARX, only 160 samples (80 for estimation), SNR 30 dB", u,
        add_noise(rng, simulate(nl2, u), 30), "NARX", TERMS_NL2)
    u = steps(rng, 20000, hold=4)
    add("nl2_many", "2nd-order NARX, 20 000 samples, SNR 20 dB", u, add_noise(rng, simulate(nl2, u), 20),
        "NARX", TERMS_NL2)

    u = steps(rng, 2000, hold=20)
    add("resonant_cubic", "lightly damped 2nd order with cubic stiffness (prone to divergence), SNR 30 dB", u,
        add_noise(rng, simulate(resonant, u), 30), "NARX",
        ["y(k-1)", "y(k-2)", "u(k-1)", "y(k-1)^3"])
    u = 1.0 + 0.8 * steps(rng, 1500, hold=30)
    y = np.full(1500, 0.64)
    for k in range(1, 1500):
        y[k] = tank(y, u, k)
    add("tank_sqrt", "non-polynomial tank (square-root outflow), steps, SNR 30 dB", u, add_noise(rng, y, 30),
        "NARX")
    return out


def all_cases():
    return example_cases() + synthetic_cases()


# --------------------------------------------------------------------------- running
def summarise(case: Case, out: dict, seconds: float):
    def r2(m):
        v = m["metrics"]["R2"]
        return None if v is None else float(v)

    greens = [m for m in out["models"] if m["kind"] == "green"]
    ref = next((m for m in greens if m["method"] == "accurate"), None)  # most accurate Green model
    s = out["structure"]
    found = set(ref["terms"]) if ref else set()
    return {
        "case": case.key, "kind": case.kind, "family": s["family"],
        "structure": f"d{s['degree']} ny{s['lag_y']} nu{s['lag_u']}",
        "ref_method": ref["method"] if ref else None,
        "ref_terms": ref["n_terms"] if ref else None, "ref_r2": r2(ref) if ref else None,
        "ref_complexity": ref["complexity"] if ref else None,
        "true_found": (sum(t in found for t in case.true_terms), len(case.true_terms)) if case.true_terms else None,
        "green": [{"method": m["method"], "terms": m["n_terms"], "r2": r2(m), "complexity": m["complexity"],
                   "diverged": r2(m) is None} for m in greens],
        "seconds": seconds,
        "warnings": out["warnings"],
    }


def run_case(case: Case, methods=METHODS, **overrides):
    cfg = {**case.cfg, "green_methods": list(methods), **overrides}
    t0 = time.perf_counter()
    out = run(cfg, *case.data[:4], continuation=case.data[4])
    return summarise(case, out, time.perf_counter() - t0), out
