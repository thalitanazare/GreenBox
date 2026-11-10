"""Run the identification battery (tests/battery.py) and print a results table.

    python tools/run_battery.py [--json out.json] [--only key1,key2] [key=value ...]

Extra ``key=value`` arguments override the run configuration of every case
(e.g. ``green_selection=classical``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from battery import all_cases, run_case  # noqa: E402


def fmt(v, nd=3):
    return "div." if v is None else f"{v:.{nd}f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    ap.add_argument("--only")
    ap.add_argument("overrides", nargs="*")
    a = ap.parse_args()
    over = {}
    for kv in a.overrides:
        k, v = kv.split("=", 1)
        try:
            over[k] = json.loads(v)
        except json.JSONDecodeError:
            over[k] = v
    only = set(a.only.split(",")) if a.only else None
    rows = []
    for case in all_cases():
        if only and case.key not in only:
            continue
        s, _ = run_case(case, **over)
        rows.append(s)
        g = s["green"]
        worst = min((x["r2"] if x["r2"] is not None else -1e9) for x in g)
        n_div = sum(x["diverged"] for x in g)
        bigger = sum(x["terms"] > (s["ref_terms"] or 0) for x in g)
        tf = s["true_found"]
        print(f"| {s['case']} | {s['family']} {s['structure']} | {s['ref_terms']} | {fmt(s['ref_r2'])} | "
              + " ".join(f"{x['terms']}:{fmt(x['r2'], 2)}" for x in g)
              + f" | {fmt(worst if worst > -1e9 else None, 2)} | {n_div} | {bigger} | "
              + (f"{tf[0]}/{tf[1]}" if tf else "-") + f" | {s['seconds']:.1f} |", flush=True)
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
