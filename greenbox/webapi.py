"""JSON bridge between the browser GUI (Pyodide worker) and the package.

Every public function takes / returns JSON strings so the JavaScript side
never handles numpy objects. Loaded files stay in ``_FILES`` so large
signals are not sent back and forth.
"""

from __future__ import annotations

import json

import numpy as np

from . import calibration, data
from .families import family_catalog
from .green import THEORETICAL, theoretical_cost_model
from .pipeline import RunConfig, candidate_summary, run, to_jsonable

_FILES: dict[str, dict] = {}
MAX_PLOT_POINTS = 4000


def _ok(x):
    return json.dumps(to_jsonable(x), allow_nan=False)


def _describe(file_id):
    f = _FILES[file_id]
    cols = [{"name": k, "n": int(np.isfinite(v).sum()), "min": float(np.nanmin(v)), "max": float(np.nanmax(v))}
            for k, v in f["columns"].items() if len(v) and np.isfinite(v).any()]
    return {"id": file_id, "name": f["name"], "columns": cols}


def load_file(file_id, name, raw):
    if hasattr(raw, "to_py"):  # JavaScript Uint8Array inside Pyodide
        raw = raw.to_py()
    _FILES[file_id] = {"name": name, "columns": data.read_file(name, bytes(raw))}
    return _ok(_describe(file_id))


def load_text(file_id, name, text):
    return load_file(file_id, name, text.encode())


def info():
    models = [{"key": k, "label": v[0], "formula": v[1],
               "rho_64": theoretical_cost_model(k, 64).rho} for k, v in THEORETICAL.items()]
    return _ok({"families": family_catalog(), "cost_models": models,
                "defaults": RunConfig().__dict__})


def _column(ref):
    if not ref or not ref.get("col"):
        return None
    return np.asarray(_FILES[ref["file"]]["columns"][ref["col"]], dtype=float)


def _datasets(spec):
    """Training and validation signals described by the GUI data spec."""
    pp = dict(start=spec.get("start", 0), stop=spec.get("stop") or None, step=spec.get("step", 1))
    norm = spec.get("normalise", "none")
    u, y = _column(spec["u"]), _column(spec["y"])
    if y is None:
        raise ValueError("Choose the output signal y.")
    if spec.get("mode", "split") == "split":
        u, y, scale = data.prepare(u, y, normalise=norm, **pp)
        n_tr = int(spec.get("n_train") or round(len(y) * float(spec.get("train_fraction", 0.5))))
        n_tr = min(max(n_tr, 2), len(y) - 2)
        return (None if u is None else u[:n_tr], y[:n_tr], None if u is None else u[n_tr:], y[n_tr:],
                True, scale)
    u_tr, y_tr, scale = data.prepare(u, y, normalise=norm, **pp)
    uv, yv = _column(spec.get("u_val")), _column(spec.get("y_val"))
    if yv is None:
        raise ValueError("Choose the validation output signal.")
    uv, yv, _ = data.prepare(uv, yv, start=0, stop=None, step=spec.get("step", 1))
    uv, yv = data.apply_scale(uv, yv, scale)
    return u_tr, y_tr, uv, yv, bool(spec.get("continuation", False)), scale


def _thin(x):
    x = np.asarray(x)
    step = max(1, int(np.ceil(len(x) / MAX_PLOT_POINTS)))
    return np.arange(0, len(x), step), x[::step]


def preview(spec_json):
    u_tr, y_tr, u_va, y_va, cont, scale = _datasets(json.loads(spec_json))
    out = {"n_train": len(y_tr), "n_val": len(y_va), "continuation": cont, "scale": scale, "series": {}}
    for name, x in (("u_train", u_tr), ("y_train", y_tr), ("u_val", u_va), ("y_val", y_va)):
        if x is not None:
            i, v = _thin(x)
            out["series"][name] = {"i": i.tolist(), "v": v.tolist()}
    return _ok(out)


def candidates(cfg_json):
    return _ok(candidate_summary(json.loads(cfg_json)))


def identify(spec_json, cfg_json, progress=None):
    u_tr, y_tr, u_va, y_va, cont, scale = _datasets(json.loads(spec_json))
    res = run(json.loads(cfg_json), u_tr, y_tr, u_va, y_va, continuation=cont, progress=progress)
    res["scale"] = scale
    res["n_train"] = len(y_tr)
    return _ok(res)


def linearity(spec_json, progress=None):
    from .linearity import check
    u_tr, y_tr, *_ = _datasets(json.loads(spec_json))
    return _ok(check(u_tr, y_tr, progress=progress))


def calibrate_python(seconds_per_structure=0.03, progress=None):
    return _ok(calibration.calibrate(min_seconds=float(seconds_per_structure), progress=progress))


def fit_calibration(points_json, engine):
    return _ok(calibration.fit(json.loads(points_json), engine=engine))
