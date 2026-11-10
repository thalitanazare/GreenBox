"""Build the single-file browser app: dist/GreenBox.html

    python tools/build_web.py

Inlines the stylesheet, the JavaScript, the worker, the greenbox Python
package and the example datasets into one HTML file that can be opened by
double-clicking it (an internet connection is needed the first time to load
Pyodide and Plotly from their CDNs).
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
OUT = ROOT / "dist" / "GreenBox.html"

EXAMPLES = {
    "dc_motor": {
        "file": "dc_motor.csv",
        "name": "DC motor",
        "url": "https://github.com/wilsonrljr/sysidentpy",  # original data: users are sent here instead of exporting them
        "source": 'Data distributed with the SysIdentPy package: W. R. Lacerda, L. P. C. Andrade, S. C. P. Oliveira, S. A. M. Martins. SysIdentPy: a Python package for system identification using NARMAX models. <i>J. Open Source Software</i>, 5(54), 2020. <a href="https://doi.org/10.21105/joss.02384" target="_blank" rel="noopener">doi:10.21105/joss.02384</a>',
        # decimated by 500, max-abs scaling, 250 estimation samples
        "setup": {"mode": "split", "u": "u", "y": "y", "normalise": "maxabs", "n_train": 250,
                  "structure": {"family": "NARX", "degree": 3, "lag_y": 3, "lag_u": 4},
                  "identify": {"structure": "manual"}},
        # best structure of each family on this data set (python tools/tune_examples.py)
        "structures": {"ARX": {"degree": 1, "lag_y": 4, "lag_u": 12, "r2": 0.779, "terms": 6}, "ARMAX": {"degree": 1, "lag_y": 1, "lag_u": 10, "lag_e": 2, "r2": 0.76, "terms": 13}, "FIR": {"degree": 1, "lag_y": 0, "lag_u": 50, "r2": 0.763, "terms": 7}, "NARX": {"degree": 3, "lag_y": 3, "lag_u": 4, "r2": 0.989, "terms": 13}, "NARMAX": {"degree": 3, "lag_y": 3, "lag_u": 1, "lag_e": 2, "r2": 0.99, "terms": 12}, "Volterra": {"degree": 3, "lag_y": 0, "lag_u": 8, "r2": 0.966, "terms": 59}},
    },
    "silverbox": {
        "file": "silverbox.csv",
        "name": "Silverbox",
        "url": "https://www.nonlinearbenchmark.org",  # original data: users are sent here instead of exporting them
        "source": 'Silverbox benchmark, <a href="https://www.nonlinearbenchmark.org" target="_blank" rel="noopener">nonlinearbenchmark.org</a>: T. Wigren, J. Schoukens. Three free data sets for development and benchmarking in nonlinear system identification. <i>European Control Conference</i>, 2013. <a href="https://doi.org/10.23919/ECC.2013.6669201" target="_blank" rel="noopener">doi:10.23919/ECC.2013.6669201</a>',
        # Silverbox benchmark (Wigren & Schoukens): estimation = first 10 000 samples of the
        # filtered-Gaussian segment, validation = first 20 000 samples of the multisine part
        "setup": {"mode": "separate", "u": "uEst", "y": "yEst", "uv": "uVal", "yv": "yVal",
                  "continuation": False, "normalise": "maxabs",
                  "structure": {"family": "NARX", "degree": 3, "lag_y": 3, "lag_u": 3},
                  "identify": {"structure": "manual"}},
        # best structure of each family on this data set (python tools/tune_examples.py)
        "structures": {"ARX": {"degree": 1, "lag_y": 6, "lag_u": 4, "r2": 0.907, "terms": 8}, "ARMAX": {"degree": 1, "lag_y": 6, "lag_u": 4, "lag_e": 2, "r2": 0.908, "terms": 10}, "FIR": {"degree": 1, "lag_y": 0, "lag_u": 50, "r2": 0.912, "terms": 39}, "NARX": {"degree": 3, "lag_y": 3, "lag_u": 3, "r2": 0.997, "terms": 8}, "NARMAX": {"degree": 3, "lag_y": 3, "lag_u": 3, "lag_e": 2, "r2": 0.995, "terms": 11}, "Volterra": {"degree": 2, "lag_y": 0, "lag_u": 20, "r2": 0.735, "terms": 20}},
    },
    "emps": {
        "file": "emps.csv",
        "name": "EMPS (positioning system)",
        "url": "https://www.nonlinearbenchmark.org",  # original data: users are sent here instead of exporting them
        "source": 'Electro-mechanical positioning system benchmark, <a href="https://www.nonlinearbenchmark.org" target="_blank" rel="noopener">nonlinearbenchmark.org</a>: A. Janot, M. Gautier, M. Brunot. Data set and reference models of EMPS. <i>Nonlinear System Identification Benchmarks Workshop</i>, Eindhoven, 2019.',
        # EMPS benchmark (Janot, Gautier & Brunot): prismatic joint of a robot driven by a DC motor with friction;
        # input = motor force, output = position; estimation and test records decimated from 1 kHz to 100 Hz
        "setup": {"mode": "separate", "u": "uEst", "y": "yEst", "uv": "uVal", "yv": "yVal",
                  "continuation": False, "normalise": "maxabs",
                  "structure": {"family": "NARX", "degree": 3, "lag_y": 4, "lag_u": 1},
                  "identify": {"structure": "manual"}},
        # best structure of each family on this data set (python tools/tune_examples.py)
        "structures": {"ARX": {"degree": 1, "lag_y": 1, "lag_u": 30, "r2": 0.994, "terms": 9}, "ARMAX": {"degree": 1, "lag_y": 1, "lag_u": 30, "lag_e": 2, "r2": 0.997, "terms": 11}, "FIR": {"degree": 1, "lag_y": 0, "lag_u": 100, "r2": 0.604, "terms": 19}, "NARX": {"degree": 3, "lag_y": 4, "lag_u": 1, "r2": 0.998, "terms": 11}, "NARMAX": {"degree": 3, "lag_y": 2, "lag_u": 1, "lag_e": 2, "r2": 0.997, "terms": 17}, "Volterra": {"degree": 3, "lag_y": 0, "lag_u": 2, "r2": 0.032, "terms": 2}},
    },
    "lanl_three_story": {
        "file": "lanl_three_story.csv",
        "name": "Three-story structure (LANL)",
        "url": "https://www.osti.gov/biblio/961604",  # original data: users are sent here instead of exporting them
        "source": 'Three-story frame structure of Los Alamos National Laboratory, distributed with SHMTools: E. Figueiredo, G. Park, J. Figueiras, C. Farrar, K. Worden. Structural health monitoring algorithm comparisons using standard data sets. Report LA-14393, Los Alamos National Laboratory, 2009.',
        # LANL three-story structure, undamaged state: shaker force (input) and acceleration of the third floor
        # (output), 8192 samples at 320 Hz; test 1 for estimation, test 2 for validation
        "setup": {"mode": "separate", "u": "uEst", "y": "yEst", "uv": "uVal", "yv": "yVal",
                  "continuation": False, "normalise": "maxabs",
                  "structure": {"family": "NARX", "degree": 1, "lag_y": 20, "lag_u": 15},
                  "identify": {"structure": "manual"}},
        # best structure of each family on this data set (python tools/tune_examples.py)
        "structures": {"ARX": {"degree": 1, "lag_y": 30, "lag_u": 30, "r2": 0.992, "terms": 26}, "ARMAX": {"degree": 1, "lag_y": 15, "lag_u": 10, "lag_e": 2, "r2": 0.989, "terms": 28}, "FIR": {"degree": 1, "lag_y": 0, "lag_u": 100, "r2": 0.852, "terms": 66}, "NARX": {"degree": 1, "lag_y": 20, "lag_u": 15, "r2": 0.991, "terms": 31}, "NARMAX": {"degree": 1, "lag_y": 20, "lag_u": 15, "lag_e": 2, "r2": 0.991, "terms": 33}, "Volterra": {"degree": 1, "lag_y": 0, "lag_u": 30, "r2": 0.513, "terms": 14}},
    },
    "wiener_hammerstein": {
        "file": "wiener_hammerstein.csv",
        "name": "Wiener–Hammerstein",
        "url": "https://www.nonlinearbenchmark.org",  # original data: users are sent here instead of exporting them
        "source": 'Wiener–Hammerstein benchmark, <a href="https://www.nonlinearbenchmark.org" target="_blank" rel="noopener">nonlinearbenchmark.org</a>: J. Schoukens, J. Suykens, L. Ljung. Wiener-Hammerstein benchmark. <i>15th IFAC Symposium on System Identification (SYSID)</i>, Saint-Malo, 2009.',
        # Wiener-Hammerstein benchmark (Schoukens, Suykens & Ljung), 51.2 kHz: estimation = first
        # 10 000 samples of the estimation record, validation = first 20 000 samples of the test record
        "setup": {"mode": "separate", "u": "uEst", "y": "yEst", "uv": "uVal", "yv": "yVal",
                  "continuation": False, "normalise": "maxabs",
                  "structure": {"family": "NARX", "degree": 2, "lag_y": 3, "lag_u": 8},
                  "identify": {"structure": "manual"}},
        # best structure of each family on this data set (python tools/tune_examples.py)
        "structures": {"ARX": {"degree": 1, "lag_y": 4, "lag_u": 12, "r2": 0.964, "terms": 7}, "ARMAX": {"degree": 1, "lag_y": 4, "lag_u": 10, "lag_e": 2, "r2": 0.964, "terms": 9}, "FIR": {"degree": 1, "lag_y": 0, "lag_u": 40, "r2": 0.962, "terms": 9}, "NARX": {"degree": 2, "lag_y": 3, "lag_u": 8, "r2": 0.994, "terms": 48}, "NARMAX": {"degree": 2, "lag_y": 4, "lag_u": 6, "lag_e": 2, "r2": 0.991, "terms": 42}, "Volterra": {"degree": 2, "lag_y": 0, "lag_u": 20, "r2": 0.972, "terms": 20}},
    },
}


# (coefficient, term) of the synthetic NARX system shown on the Home page: terms of graded size, so that
# the accuracy weight moves the Green models along a long Pareto front
TRUE_SYSTEM = [
    (0.70, "y(k-1)"), (-0.25, "y(k-2)"), (0.08, "y(k-3)"), (0.60, "u(k-1)"), (0.30, "u(k-2)"), (0.12, "u(k-3)"),
    (-0.25, "y(k-1) * u(k-1)"), (0.15, "u(k-1)^2"), (-0.08, "y(k-1)^3"), (0.05, "u(k-1) * u(k-2)"),
    (0.04, "y(k-2) * u(k-2)"),
]


def simulate_true_system(n=1000, noise_std=0.02, seed=7):
    import numpy as np
    rng = np.random.default_rng(seed)
    u = np.repeat(rng.uniform(-1, 1, n // 4 + 1), 4)[:n]          # random steps held for 4 samples
    y = np.zeros(n)
    for k in range(3, n):
        y[k] = (0.70 * y[k - 1] - 0.25 * y[k - 2] + 0.08 * y[k - 3] + 0.60 * u[k - 1] + 0.30 * u[k - 2]
                + 0.12 * u[k - 3] - 0.25 * y[k - 1] * u[k - 1] + 0.15 * u[k - 1] ** 2 - 0.08 * y[k - 1] ** 3
                + 0.05 * u[k - 1] * u[k - 2] + 0.04 * y[k - 2] * u[k - 2])
    return u, y + noise_std * rng.normal(size=n)                    # measurement noise


def demo_data():
    """Green models of every decision method (p = 1, 2, inf at w_E = 0, 0.1, ..., 1) on a synthetic system, for the Home page."""
    import sys
    sys.path.insert(0, str(ROOT))
    import numpy as np
    from greenbox import run
    from greenbox.green import pareto_mask

    u, y = simulate_true_system()
    n = 600
    weights = [round(0.1 * i, 1) for i in range(11)]  # the w_E slider: 0, 0.1, ..., 1
    out = run({"family": "NARX", "structure_search": True, "size_selection": "simulation", "green_weights": weights},
              u[:n], y[:n], u[n:], y[n:])
    keep = lambda m: {k: m[k] for k in ("name", "kind", "method", "n_terms", "terms", "codes", "theta", "n_add",
                                        "n_mult", "complexity", "metrics", "holdout_r2", "yhat", "w_E") if k in m}
    p = out["green_pool"]
    c = np.array(p["complexity"], dtype=float)
    e = np.sqrt(np.maximum(1 - np.array([np.nan if r is None else r for r in p["holdout_r2"]], dtype=float), 0))
    e[~np.array(p["admissible"], dtype=bool)] = np.inf
    front = np.flatnonzero(pareto_mask(c, e))
    front = front[np.argsort(c[front])]
    return {"y_val": out["y_val"], "offset": out["val_offset"], "structure": out["structure"],
            "true": [{"theta": c, "term": t} for c, t in TRUE_SYSTEM], "noise_std": 0.02,
            "models": [keep(m) for m in out["models"]], "weights": weights,
            "front": {"complexity": c[front].tolist(), "error": e[front].tolist()}}


def script_safe(text: str) -> str:
    """Make text safe to embed inside a <script> element."""
    return text.replace("</", "<\\/")


def build(out: Path = OUT) -> Path:
    py_sources = {
        str(p.relative_to(ROOT)).replace("\\", "/"): p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "greenbox").glob("*.py"))
    }
    examples = {}
    for key, ex in EXAMPLES.items():
        examples[key] = {**ex, "text": (ROOT / "examples" / ex["file"]).read_text(encoding="utf-8")}

    html = (WEB / "index.html").read_text(encoding="utf-8")
    parts = {
        "/*@@STYLE@@*/": (WEB / "style.css").read_text(encoding="utf-8"),
        "/*@@WORKER@@*/": script_safe((WEB / "worker.js").read_text(encoding="utf-8")),
        "/*@@PYSOURCES@@*/": script_safe(json.dumps(py_sources)),
        "/*@@EXAMPLES@@*/": script_safe(json.dumps(examples)),
        "/*@@DEMO@@*/": script_safe(json.dumps(demo_data(), separators=(",", ":"))),
        "/*@@APP@@*/": script_safe((WEB / "app.js").read_text(encoding="utf-8")),
    }
    for marker, content in parts.items():
        if marker not in html:
            raise SystemExit(f"marker {marker} missing from web/index.html")
        html = html.replace(marker, content)
    out.parent.mkdir(exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")  # LF on every system
    return out


if __name__ == "__main__":
    out = build()
    print(f"wrote {out.relative_to(ROOT)} ({out.stat().st_size / 1024:.0f} KiB)")
