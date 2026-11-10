"""Calibrate the cost of an addition and a multiplication on this computer.

Straight-line polynomials with known numbers of additions ``a`` and
multiplications ``b`` are timed per sample, and the regression

    t = t0 + t_add * a + t_mult * b

gives the measured cost of each operation (rho = t_mult / t_add).

The result depends on the processor *and* on the execution implementation
(interpreted Python, JavaScript JIT, compiled C, ...), so it describes the
platform where the timing ran. The GUI offers a JavaScript engine (close to
compiled code) and this Python engine (interpreter cost).

Run from a terminal:  python -m greenbox.calibration
"""

from __future__ import annotations

import os
import platform
import random
import statistics
import time

import numpy as np

N_VARS = 4  # every structure loads the same inputs, so loads go into t0


def structures(max_terms=10, extra_mults=(0, 2, 5, 9), seed=0):
    """(a, b, degrees) triples with a and b varied independently."""
    rng = random.Random(seed)
    out = []
    for a in range(1, max_terms + 1):
        for extra in extra_mults:
            degs = [1] * a
            for _ in range(extra):
                degs[rng.randrange(a)] += 1
            out.append((a, sum(degs), degs))
    rng.shuffle(out)
    return out


def _compile(degs, seed, c):
    """Coefficients are written as constants, as the GreenBox simulator does; reading them
    from a list would add a per-term cost that the fit attributes to the additions."""
    rng = random.Random(seed)
    terms = []
    for i, d in enumerate(degs):
        factors = "*".join(f"x{rng.randrange(N_VARS)}" for _ in range(d))
        terms.append(f"{c[i + 1]!r}*{factors}")
    loads = "; ".join(f"x{j} = X{j}[k]" for j in range(N_VARS))
    args = ", ".join(f"X{j}" for j in range(N_VARS))
    src = (f"def f({args}, c, n):\n"
           f"    s = 0.0\n"
           f"    for k in range(n):\n"
           f"        {loads}\n"
           f"        s += {c[0]!r} + {' + '.join(terms)}\n"
           f"    return s\n")
    ns = {}
    exec(src, ns)
    return ns["f"]


def calibrate(n_samples=2000, min_seconds=0.03, repeats=3, max_terms=10, progress=None):
    """Time the structures and fit the per-operation costs (ns)."""
    rng = np.random.default_rng(1)
    X = [list(rng.uniform(0.5, 1.5, n_samples)) for _ in range(N_VARS)]
    rows = []
    sts = structures(max_terms)
    for idx, (a, b, degs) in enumerate(sts):
        c = [round(float(v), 6) for v in rng.uniform(-1, 1, a + 1)]
        f = _compile(degs, idx, c)
        f(*X, c, 50)  # warm-up
        reps = []
        for _ in range(repeats):
            loops, t0 = 0, time.perf_counter()
            while True:
                f(*X, c, n_samples)
                loops += 1
                el = time.perf_counter() - t0
                if el >= min_seconds:
                    break
            reps.append(el / (loops * n_samples) * 1e9)
        # the constant term adds one addition; ``a`` non-constant terms -> a additions
        rows.append((a, b, statistics.median(reps)))
        if progress:
            progress(f"Timing structure {idx + 1}/{len(sts)}", (idx + 1) / len(sts))
    return fit(rows, engine="Python " + platform.python_version())


def fit(rows, engine=""):
    rows = np.asarray(rows, dtype=float)
    A = np.column_stack([np.ones(len(rows)), rows[:, 0], rows[:, 1]])
    t = rows[:, 2]
    coef, *_ = np.linalg.lstsq(A, t, rcond=None)
    r = t - A @ coef
    r2 = 1 - (r @ r) / np.sum((t - t.mean()) ** 2)
    t0, t_add, t_mult = (float(v) for v in coef)
    return {
        "engine": engine,
        "platform": f"{platform.system()} {platform.machine()}".strip(),
        "t0_ns": t0, "t_add_ns": t_add, "t_mult_ns": t_mult,
        "rho": t_mult / t_add if t_add > 0 else float("nan"),
        "r2": float(r2), "n_structures": int(len(rows)),
        "points": rows.tolist(),
        "valid": bool(t_add > 0 and t_mult > 0),
    }


PURE_C = r"""
#include <stdio.h>
#include <time.h>
#define N 100000000L
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec*1e9+t.tv_nsec;}
#if defined(__aarch64__)
#define KEEP(x) __asm__ volatile("" : "+w"(x))
#else
#define KEEP(x) __asm__ volatile("" : "+x"(x))
#endif
static double lat(int mul){volatile double vk = mul ? 1.0000000001 : 1e-9; double k = vk, x = 1.0, t0 = now();
 if(mul) for(long i=0;i<N;i++){x=x*k;KEEP(x);} else for(long i=0;i<N;i++){x=x+k;KEEP(x);}
 double t=(now()-t0)/N; if(x==42.0) puts(""); return t;}
static double thr(int mul){volatile double vk = mul ? 1.0000000001 : 1e-9; double k = vk, a=1,b=1,c=1,d=1,e=1,f=1,g=1,h=1, t0 = now();
 if(mul) for(long i=0;i<N;i++){a*=k;b*=k;c*=k;d*=k;e*=k;f*=k;g*=k;h*=k;KEEP(a);KEEP(b);KEEP(c);KEEP(d);KEEP(e);KEEP(f);KEEP(g);KEEP(h);}
 else    for(long i=0;i<N;i++){a+=k;b+=k;c+=k;d+=k;e+=k;f+=k;g+=k;h+=k;KEEP(a);KEEP(b);KEEP(c);KEEP(d);KEEP(e);KEEP(f);KEEP(g);KEEP(h);}
 double t=(now()-t0)/(8.0*N); if(a+b+c+d+e+f+g+h==42.0) puts(""); return t;}
int main(void){ double w=1, t0=now(); while(now()-t0<5e8){ for(int i=0;i<1000000;i++){w*=1.0000000001;KEEP(w);} }
 double v[4][5];
 for(int r=0;r<5;r++){ v[0][r]=lat(0); v[1][r]=lat(1); v[2][r]=thr(0); v[3][r]=thr(1); }
 for(int i=0;i<4;i++) for(int a=0;a<5;a++) for(int b=a+1;b<5;b++) if(v[i][b]<v[i][a]){double t=v[i][a];v[i][a]=v[i][b];v[i][b]=t;}
 printf("%.6f %.6f %.6f %.6f %.6f %.6f %.6f %.6f %g\n", v[0][0], v[1][0], v[2][0], v[3][0],
        v[0][2], v[1][2], v[2][2], v[3][2], w); return 0; }
"""


def compile_pure(exe, compiler="cc"):
    """Compile the processor-arithmetic benchmark (``PURE_C``) into the executable ``exe``."""
    import subprocess
    from pathlib import Path

    exe = Path(exe)
    src = exe.with_suffix(".c")
    src.write_text(PURE_C)
    flag_sets = (["-fno-vectorize", "-fno-slp-vectorize"], ["-fno-tree-vectorize"], [])  # clang, gcc, other
    static = ["-static"] if os.name == "nt" else []  # no MinGW DLLs needed on the user's machine
    try:
        for flags in flag_sets:
            done = subprocess.run([compiler, "-O2", *flags, *static, str(src), "-o", str(exe)], capture_output=True)
            if done.returncode == 0:
                return exe
        raise RuntimeError(f"could not compile with {compiler}: {done.stderr.decode()[:300]}")
    finally:
        src.unlink(missing_ok=True)


def run_pure(exe, engine="Processor arithmetic (C -O2, 64-bit float)"):
    """Run a compiled benchmark: cost of ONE 64-bit floating-point addition and multiplication (ns).

    A dependent chain x = x + k (and x = x * k) is timed after a warm-up so the processor reaches
    its full clock; the minimum of 5 runs is kept. Loop, loads and stores run in parallel with the
    chain and are not counted. Same result fields as the WebAssembly calibration of the GUI.
    """
    import subprocess

    v = [float(x) for x in subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split()]
    best, median = v[0:4], v[4:8]
    lat, thr = {"add": best[0], "mult": best[1]}, {"add": best[2], "mult": best[3]}
    return {"kind": "arithmetic", "engine": engine, "use": "latency",
            "platform": f"{platform.system()} {platform.machine()}".strip(),
            "latency": lat, "throughput": thr, "spread": max(m / b - 1 for m, b in zip(median, best)),
            "t0_ns": 0.0, "t_add_ns": lat["add"], "t_mult_ns": lat["mult"], "rho": lat["mult"] / lat["add"],
            "r2": None, "valid": lat["add"] > 0 and lat["mult"] > 0, "points": []}


def pure_arithmetic(compiler="cc"):
    """Compile and run the processor-arithmetic benchmark (needs a C compiler: cc / clang / gcc)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        exe = compile_pure(Path(d) / "pure", compiler)
        return run_pure(exe, f"Processor arithmetic (C, {compiler} -O2, 64-bit float)")


if __name__ == "__main__":
    import sys

    if "--pure" in sys.argv:
        r = pure_arithmetic()
        L, T = r["latency"], r["throughput"]
        print(f"engine     : {r['engine']} on {r['platform']}")
        print(f"latency    : add {L['add']:.3f} ns   mult {L['mult']:.3f} ns   rho {L['mult'] / L['add']:.3f}")
        print(f"throughput : add {T['add']:.3f} ns   mult {T['mult']:.3f} ns   rho {T['mult'] / T['add']:.3f}")
        sys.exit(0)
    res = calibrate(progress=lambda m, f: print(f"\r{m}", end="", flush=True))
    print()
    print(f"engine   : {res['engine']} on {res['platform']}")
    print(f"t_add    : {res['t_add_ns']:.3f} ns")
    print(f"t_mult   : {res['t_mult_ns']:.3f} ns")
    print(f"rho      : {res['rho']:.3f}   (R^2 = {res['r2']:.4f})")
