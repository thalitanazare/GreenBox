"""Identification quality on the battery of tests/battery.py, with the GUI's default settings.

Guarantees of the Green selection by hold-out simulation: the most accurate Green model
is accurate and finds the true terms, no Green model diverges or costs more than it, all
stay useful, and every decision method picks a Pareto-optimal candidate between the
cheapest and the most accurate one.
"""

import numpy as np
import pytest

from battery import all_cases, run_case
from greenbox.green import DECISION_METHODS, choose_from_pool, decision_score, prune_sequence

CASES = all_cases()
# low-noise systems whose true terms the most accurate Green model must find
# (lin2_narx is excluded: the most accurate model there is an equally accurate longer-lag equivalent,
# validation R² 0.999 with y(k-1), y(k-6), u(k-1..5) instead of y(k-2);
# nl2_few is excluded: with 80 estimation samples the test part has 24 samples, too few to separate the
# true 7-term model (test R² 0.9971) from larger ones (0.9979), so the most accurate model overfits; the
# compromise methods recover the true terms)
RECOVERS_TRUE_TERMS = {"lin1_white_low", "nl1_white_low", "nl2_steps_low", "nl1_sines",
                       "narmax_coloured", "narmax_as_narx", "fir_white"}


@pytest.fixture(scope="module", params=CASES, ids=[c.key for c in CASES])
def result(request):
    case = request.param
    summary, out = run_case(case)
    return case, summary, out


def greens(out):
    return sorted((m for m in out["models"] if m["kind"] == "green"), key=lambda m: DECISION_METHODS.index(m["method"]))


def test_most_accurate_green_model_is_good(result):
    case, s, out = result
    assert all(m["kind"] == "green" for m in out["models"]), "the GUI run must not produce a Standard model"
    assert s["ref_method"] == "accurate" and out["reference"] == greens(out)[-1]["name"]
    assert s["ref_r2"] is not None and s["ref_r2"] >= 0.85, f"{case.key}: most accurate R² {s['ref_r2']}"
    if case.key in RECOVERS_TRUE_TERMS:
        assert s["true_found"][0] == s["true_found"][1], f"{case.key}: true terms {s['true_found']}"


def test_green_models_are_stable_and_admissible(result):
    """Green models must simulate, be useful, reach the minimum hold-out R^2 when any candidate
    does, and be no larger than the most accurate one (the reference of the comparisons)."""
    case, s, out = result
    ref = greens(out)[-1]
    assert ref["complexity_reduction_pct"] == 0.0 and ref["nrmse_change_pct"] == 0.0
    for m in greens(out):
        assert m["complexity"] <= ref["complexity"] + 1e-9, f"{case.key} {m['name']}"
        assert m["complexity_reduction_pct"] >= -1e-9
        r2 = m["metrics"]["R2"]
        assert r2 is not None, f"{case.key} {m['name']} diverged"
        if any(out["green_pool"]["admissible"]):
            assert m["holdout_r2"] >= out["green_pool"]["r2_limit"] - 1e-9


def test_decision_methods_pick_pareto_optimal_candidates(result):
    """No admissible candidate is cheaper and at least as accurate on the test part (or vice versa)
    than a Green model; the cheapest and the most accurate methods bound the others."""
    case, s, out = result
    pool = out["green_pool"]
    if not any(pool["admissible"]):
        return
    adm = [i for i, a in enumerate(pool["admissible"]) if a]
    c = np.array(pool["complexity"])[adm]
    h = np.array(pool["holdout_r2"], dtype=float)[adm]
    for m in greens(out):
        mc, mh = m["complexity"], m["holdout_r2"]
        dom = (c <= mc + 1e-9) & (h >= mh - 1e-12) & ((c < mc - 1e-9) | (h > mh + 1e-12))
        assert not dom.any(), f"{case.key} {m['name']}: dominated by a candidate"
    g = {m["method"]: m for m in greens(out)}
    for m in g.values():
        assert g["cheapest"]["complexity"] <= m["complexity"] + 1e-9
        assert g["accurate"]["holdout_r2"] >= m["holdout_r2"] - 1e-12


def test_classical_green_procedure_still_available():
    case = next(c for c in CASES if c.key == "dc_motor")
    _, out = run_case(case, methods=["weighted", "accurate"], green_selection="classical")
    assert "green_pool" not in out
    assert all(m["selection"]["method"] == "classical" for m in out["models"])


def test_choose_from_pool_endpoints():
    error = np.array([0.10, 0.12, 0.30, 0.50, np.inf])
    comp = np.array([100.0, 60.0, 20.0, 5.0, 1.0])
    best = choose_from_pool(error, comp, "accurate", 0.316)
    cheap = choose_from_pool(error, comp, "cheapest", 0.316)
    assert best.index == 0 and cheap.index == 2      # 0.50 is above the limit, inf diverged
    assert list(best.admissible) == [True, True, True, False, False]
    assert choose_from_pool(error, comp, "weighted", 0.05).index == -1


def test_decision_methods_on_a_known_front():
    """Scaled front (E, C): (0, 1), (0.1, 0.5), (0.4, 0.3), (0.6, 0.05), (1, 0). The weighted sum
    never reaches the unsupported point (0.4, 0.3); Tchebycheff does."""
    e = np.array([0.0, 0.1, 0.4, 0.6, 1.0])
    c = np.array([1.0, 0.5, 0.3, 0.05, 0.0])
    pick = lambda m, **kw: int(np.lexsort((e, c, np.round(decision_score(e, c, m, **kw), 12)))[0])
    assert pick("cheapest") == 4 and pick("accurate") == 0
    assert pick("weighted") == 1                       # 0.3, against 0.35 at (0.4, 0.3)
    assert pick("euclidean") == 2                      # 0.25, against 0.255 at (0.1, 0.5)
    assert pick("tchebycheff") == 2                    # 0.2
    assert pick("epsilon", epsilon=0.5) == 2           # cheapest with E <= 0.5
    assert all(pick("weighted", w_err=w / 20) != 2 for w in range(21))


def test_prune_sequence_matches_brute_force():
    rng = np.random.default_rng(3)
    P = rng.normal(size=(200, 6))
    Y = P @ np.array([1.0, 0.5, 0.05, 2.0, 0.01, 0.3]) + 0.1 * rng.normal(size=200)
    seq = prune_sequence(P, Y, list(range(6)), np.zeros(6, dtype=int))
    cur = list(range(6))
    for step in seq:  # removal with the smallest SSE increase, by refitting every option
        sse = {j: np.sum((Y - P[:, [i for i in cur if i != j]] @ np.linalg.lstsq(
            P[:, [i for i in cur if i != j]], Y, rcond=None)[0]) ** 2) for j in cur}
        cur.remove(min(sse, key=sse.get))
        assert step == cur


def test_rounding_never_decides_the_models():
    """dc_motor has a 0/1 input, so u^2 = u and many candidates are identical; native
    NumPy and the browser's must choose the same models."""
    from greenbox import run
    case = next(c for c in CASES if c.key == "dc_motor")
    u, y, uv, yv, cont = case.data
    picks = set()
    for eps in (0.0, 1e-14, 1e-12):
        yy = y * (1 + eps * np.random.default_rng(0).normal(size=len(y)))
        out = run({**case.cfg, "green_methods": list(DECISION_METHODS)}, u, yy, uv, yv, continuation=cont)
        picks.add(tuple(tuple(sorted(m["terms"])) for m in out["models"]))
    assert len(picks) == 1
