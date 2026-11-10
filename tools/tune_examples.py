"""Find, for every bundled example and every model family, the structure (degree and lags)
that gives the best Standard model, so the GUI can propose it.

    python tools/tune_examples.py [--only key1,key2] [--families NARX,FIR] [--json out.json]

For each (example, family) the script runs the GUI's automatic structure search and a grid of
fixed structures, and reports the free-run validation R2, the complexity and the run time of the
Standard model, together with the most accurate Green model.
The recommended structure is the smallest one whose validation R2 is within TOL of the best.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

TOL = 0.005  # R2 margin within which a smaller structure is preferred

GRIDS = {  # family: list of (degrees, lag_y values, lag_u values, lag_e values) blocks
    "ARX": [([1], [1, 2, 3, 4, 6, 8, 10, 15, 20, 30], [1, 2, 3, 4, 6, 8, 10, 12, 15, 20, 30], [0])],
    "ARMAX": [([1], [1, 2, 3, 4, 6, 8, 15, 30], [1, 2, 4, 6, 8, 10, 15, 30], [2])],
    "FIR": [([1], [0], [5, 10, 15, 20, 30, 40, 50, 60, 80], [0])],
    "NARX": [([2], [1, 2, 3, 4, 6, 8], [1, 2, 3, 4, 6, 8], [0]), ([3], [1, 2, 3, 4], [1, 2, 3, 4], [0])],
    "NARMAX": [([2], [1, 2, 3, 4, 6], [1, 2, 4, 6], [2]), ([3], [1, 2, 3], [1, 2, 3, 4], [2])],
    "Volterra": [([2], [0], [2, 4, 6, 8, 10, 12, 15, 20], [0]), ([3], [0], [2, 4, 6, 8], [0])],
}


def _r2(m):
    v = m["metrics"]["R2"] if m else None
    return None if v is None else float(v)


def evaluate(job):
    from battery import example_cases, gui_defaults
    from greenbox.pipeline import run

    key, family, structure = job
    case = next(c for c in example_cases() if c.key == key)
    cfg = gui_defaults(family)
    cfg.update(green_methods=["accurate"], run_standard=True, criterion="AIC")  # classical Standard as the yardstick
    if structure is None:
        cfg["structure_search"] = True
    else:
        cfg.update(structure_search=False, **structure)
    t0 = time.perf_counter()
    try:
        out = run(cfg, *case.data[:4], continuation=case.data[4])
    except Exception as exc:  # reported, not fatal
        return {"example": key, "family": family, "search": structure is None, "error": f"{type(exc).__name__}: {exc}"}
    seconds = time.perf_counter() - t0
    std = next((m for m in out["models"] if m["kind"] == "standard"), None)
    g1 = next((m for m in out["models"] if m["kind"] == "green"), None)
    s = out["structure"]
    return {"example": key, "family": family, "search": structure is None,
            "degree": s["degree"], "lag_y": s["lag_y"], "lag_u": s["lag_u"], "lag_e": s.get("lag_e", 0),
            "std_terms": std and std["n_terms"], "std_r2": _r2(std), "std_complexity": std and std["complexity"],
            "green1_terms": g1 and g1["n_terms"], "green1_r2": _r2(g1), "green1_complexity": g1 and g1["complexity"],
            "seconds": round(seconds, 2)}


def jobs(examples, families):
    for key in examples:
        for fam in families:
            yield key, fam, None
            for block in GRIDS[fam]:
                for d, ly, lu, le in itertools.product(*block):
                    yield key, fam, {"degree": d, "lag_y": ly, "lag_u": lu, "lag_e": le}


def recommend(rows):
    ok = [r for r in rows if r.get("std_r2") is not None]
    if not ok:
        return None
    best = max(r["std_r2"] for r in ok)
    near = [r for r in ok if r["std_r2"] >= best - TOL]
    return min(near, key=lambda r: (r["std_complexity"], r["seconds"]))


def main():
    from battery import example_cases

    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--families", default=",".join(GRIDS))
    ap.add_argument("--json", default=str(ROOT / "tools" / "tune_examples.json"))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--search-only", action="store_true", help="only the automatic structure search")
    a = ap.parse_args()
    examples = a.only.split(",") if a.only else [c.key for c in example_cases()]
    families = a.families.split(",")
    todo = [j for j in jobs(examples, families) if not a.search_only or j[2] is None]
    print(f"{len(todo)} runs", flush=True)
    rows = []
    with ProcessPoolExecutor(a.workers) as pool:
        for i, r in enumerate(pool.map(evaluate, todo), 1):
            rows.append(r)
            if i % 20 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    summary = {}
    for key in examples:
        for fam in families:
            mine = [r for r in rows if r["example"] == key and r["family"] == fam]
            summary[f"{key}/{fam}"] = {"recommended": recommend(mine),
                                       "search": next((r for r in mine if r["search"]), None)}
    Path(a.json).write_text(json.dumps({"rows": rows, "summary": summary}, indent=1), encoding="utf-8")
    print("| example | family | recommended (d ny nu ne) | std R2 | terms | most accurate Green R2 | terms | search found | search R2 |")
    print("|---|---|---|---|---|---|---|---|---|")
    for k, v in summary.items():
        r, s = v["recommended"], v["search"] or {}
        if r is None:
            print(f"| {k.replace('/', ' | ')} | all diverged/failed | | | | | | |")
            continue
        print(f"| {k.replace('/', ' | ')} | d{r['degree']} ny{r['lag_y']} nu{r['lag_u']} ne{r['lag_e']} | {r['std_r2']:.3f} | "
              f"{r['std_terms']} | {r['green1_r2'] if r['green1_r2'] is None else round(r['green1_r2'], 3)} | {r['green1_terms']} | "
              f"d{s.get('degree')} ny{s.get('lag_y')} nu{s.get('lag_u')} | {s.get('std_r2') and round(s['std_r2'], 3)} |")


if __name__ == "__main__":
    main()
