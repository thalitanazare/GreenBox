<p align="center"><img src="assets/logo-wordmark.svg" alt="GreenBox" width="380"></p>

GreenBox identifies polynomial models (NARMAX, NARX, ARX, ARMAX, FIR and truncated Volterra) from input/output data and selects them by a **Green** criterion that trades prediction error against arithmetic complexity, the cost of evaluating the model at every sample.

The interface runs entirely in the web browser. There is nothing to install, and data files never leave the computer.

## Using it

Open [`dist/GreenBox.html`](dist/GreenBox.html) by double-clicking it. It works on Windows, macOS and Linux in any recent browser. The file can be shared directly (e-mail, cloud drive). It needs an internet connection the first time to fetch Pyodide (Python compiled to WebAssembly) and Plotly from their CDNs.

**Offline version (no internet at all):** run `python tools/build_offline.py` to create `dist/GreenBox-offline/` and `dist/GreenBox-offline.zip` (~13 MB). Unzip, keep the `lib` folder next to `GreenBox.html`, and double-click it. Python, NumPy and Plotly are bundled; only `.mat` files need internet (SciPy is not bundled; use CSV instead). Tested offline in Edge (Chromium) and WebKit (Safari engine) on macOS. Phones are not supported for local files: mobile browsers do not run JavaScript from local HTML files reliably.

**Desktop application (Windows):** download `GreenBox-Setup.exe` (installer) or `GreenBox-Windows.zip` (portable) from the GreenBox record on Zenodo (https://doi.org/10.5281/zenodo.23687749). The desktop application runs the same interface with native Python and needs no internet. To build it from source, see [`desktop/README.md`](desktop/README.md).

The workflow has five steps:

1. **Data**: CSV/TXT/DAT or `.mat`. Use one record split into estimation and validation, or a separate validation record. Optional cut, decimation and normalisation. File format: one signal per column, one sample per row, optional header with the column names; comma, semicolon, tab or space separated (decimal comma accepted with semicolon or tab). Five examples are built in: DC motor, Silverbox, EMPS, three-story structure and Wiener–Hammerstein.
2. **Model structure**: family, nonlinearity degree, lags of y, u and e, u(k) feed-through, constant term.
3. **Complexity**: how a multiplication is weighted against an addition (see below), including calibration on your own computer.
4. **Identification**: one Green model per decision method on the Pareto set (cheapest, ε-constraint, weighted sum, Euclidean, Tchebycheff, most accurate), and for the weighted methods one per accuracy weight w<sub>E</sub> in a chosen range. Noise models use extended least squares.
5. **Results**: free-run validation, complexity vs. NRMSE with the Pareto front, every distinct Green model with the methods and weights that picked it (or the models of one method), the model equation, residual correlation tests, and CSV/JSON export.

## Method

**Model size.** Candidate terms are ranked by ERR. The number of terms is chosen by *free-run simulation* on a hold-out part of the estimation data: the last 30 % is held out, terms are ranked on the rest, and each candidate is scored by the worse of two free-run R² values, on the hold-out and on a window spanning the end of the fitted part and the hold-out. The validation data are never used for any choice. Candidate terms that are linear combinations of the terms already selected (e.g. u² = u for a 0/1 input) are skipped unless "Keep linearly dependent candidate terms" is ticked (classical procedure).

The results report the change in complexity and in validation NRMSE of every Green model relative to the most accurate Green model of the run. For scripts, `run(..., {"run_standard": True})` still adds the classical reference model of the original Green-Box (ERR ranking, size by simulation or by AIC/BIC/FPE), which then becomes the reference; the GUI does not use it.

**Structure search.** The automatic search uses the estimation data only: a scan of linear models with 1 to 100 lags locates the memory of the system, then a hill climb on a lag ladder (1–6, 8, 10, 12, 15, 20, 25, 30, 40, …) for every degree keeps the structure that simulates the hold-out part of the estimation data best (the simplest one within 5 %). Hold-out = the last 30 % of the estimation data by default; the validation data are never used for any choice.

**Linearity check.** Before choosing a model family it helps to know whether the system is linear. GreenBox compares the best linear model with the best nonlinear polynomial model (free-run R² on the hold-out estimation data) and runs the Billings–Voon correlation test r<sub>y′y′²</sub>, with Bartlett's variance for the confidence band. The verdict is *linear*, *weakly nonlinear* or *nonlinear*, and a warning appears when a linear family (ARX, ARMAX, FIR) is used on a nonlinear system. On the examples: DC motor, nonlinear (the nonlinear model removes 91 % of the linear model's error); Silverbox, nonlinear.

**NARMAX / ARMAX.** The e(k) terms form the noise model. They are estimated by extended least squares to remove bias from the process parameters, set to zero in free-run simulation, and therefore not counted in the complexity. The GUI shows the process and noise equations separately.

Terms are ranked with forward orthogonal least squares, a Householder QR with column pivoting on the Error Reduction Ratio (ERR).

*Hold-out simulation (default).* For every structure the Green structure search visits, candidate models come from several term orders ranked on the first part of the estimation data: ERR, ERR per unit of cost, the classical Green order, and the most accurate of these with terms removed one by one (backward elimination, by error and by error per unit of cost saved). Each candidate is simulated in free run on the held-out part. It is **admissible** when it does not diverge and reaches the minimum hold-out R² of Green models (default 0.75). The hold-out NRMSE E and the complexity C are scaled to 0–1 over the admissible candidates (Ê, Ĉ), and each selected decision method on the Pareto set (Chankong & Haimes, 1983) picks one Green model:

| Method | Chooses the admissible candidate that minimises |
|---|---|
| Cheapest | Ĉ |
| ε-constraint | Ĉ subject to Ê ≤ ε (default ε = 0.5) |
| Weighted sum (p = 1) | w<sub>E</sub>Ê + w<sub>C</sub>Ĉ |
| Euclidean (p = 2) | √((w<sub>E</sub>Ê)² + (w<sub>C</sub>Ĉ)²) |
| Tchebycheff (p = ∞) | max(w<sub>E</sub>Ê, w<sub>C</sub>Ĉ) |
| Most accurate | Ê |

with w<sub>C</sub> = 1 − w<sub>E</sub> and equal weights by default (w<sub>E</sub> = 0.5). A range of weights (from, to, step; `green_weights` in scripts) gives one model per weight for the p = 1, 2, ∞ methods. A candidate picked by several methods or weights is one model (same `model_key`). Every method returns a Pareto-optimal candidate; the cheapest and the most accurate bound the others. The chosen model is re-estimated on all estimation data and must stay bounded in free run over it. Validation data are never used for any choice.

*Classical procedure* (`green_selection = "classical"`):

- term order: J = w<sub>E</sub>(1 − ERR<sub>norm</sub>) + w<sub>C</sub>·C<sub>norm</sub>, at each step
- model size: J = w<sub>E</sub>·ERRloss<sub>norm</sub> + w<sub>C</sub>·C<sub>norm</sub>, over k terms

The one-step ERR can look almost perfect for models that drift or diverge in simulation, which is why the hold-out choice is the default.

**Test battery.** `tests/battery.py` holds the bundled examples and synthetic systems with known equations (linear and nonlinear, first and second order, low and high measurement noise, coloured noise, FIR and Volterra, step, white, binary and multisine inputs, few and many samples, a lightly damped system prone to divergence, a non-polynomial tank), all run with the GUI's default settings. `python tools/run_battery.py` prints the results table; `tests/test_battery.py` checks that the most accurate Green model is accurate and finds the true terms, that no Green model diverges, costs more than it or falls below the minimum accuracy, and that every decision method picks a Pareto-optimal candidate.

A model with *a* additions and *b* multiplications per sample costs **C = a·c<sub>+</sub> + b·c<sub>×</sub>**. Because C is min-max normalised, only ρ = c<sub>×</sub>/c<sub>+</sub> affects which model is selected. Available cost models for B-bit operands (an addition costs B bit operations):

| Cost model | Multiplication M(B) | ρ at B = 64 |
|---|---|---|
| Karatsuba (original Green-Box) | B<sup>1.585</sup> | 11.4 |
| Schoolbook | B² | 64 |
| Toom-Cook 3-way | B<sup>log 5 / log 3</sup> | 6.9 |
| Schönhage–Strassen | B·log₂B·log₂log₂B | 15.5 |
| Uniform operation count (default) | – | 1 |
| **Calibrated on this computer** | measured t<sub>×</sub> | measured |

**Calibration.** The default, *processor arithmetic*, measures the cost of one 64-bit floating-point addition and one multiplication on the processor of the computer running GreenBox: a chain of dependent operations gives the latency t<sub>+</sub> and t<sub>×</sub> (on an Apple M2: 0.86 and 1.15 ns, ρ ≈ 1.33). In the browser the chain is WebAssembly, translated by the browser into native instructions (same values in any browser); the desktop application runs it as native machine code compiled in C when the application is built, so it works offline and needs no compiler on the user's computer. The optional *model evaluation* engines time whole polynomials in Python or JavaScript and fit t = t<sub>0</sub> + t<sub>+</sub>a + t<sub>×</sub>b, so their costs also include the interpreter's work. From a terminal, `python -m greenbox.calibration --pure` gives the pure cost natively (needs a C compiler).

**Carbon** per sample is the estimated execution time multiplied by an emission rate (default 121.81 mgCO₂e/min, from the Green Algorithms calculator). Calibrated costs are already times. Theoretical costs are anchored to a reference multiplication time. Both are estimates, not measured energy.


## Python package

The algorithms live in [`greenbox/`](greenbox/), which depends only on NumPy, and can be used without the GUI:

```python
from greenbox import run

result = run({"family": "NARX", "degree": 2, "lag_y": 2, "lag_u": 2,
              "green_methods": ["cheapest", "epsilon", "tchebycheff", "accurate"], "green_weights": [0.25, 0.5, 0.75],
              "cost_model": "karatsuba"},
             u_train, y_train, u_val, y_val, continuation=True)
```

[`tests/`](tests/) contains unit tests, regression tests that keep the classical procedure (ERR ranking, AIC size, classical Green choice) producing the same models on the bundled examples, and the identification battery described above.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                        # unit, regression and battery tests (about 15 min)
python tools/build_web.py     # rebuild dist/GreenBox.html after changing web/ or greenbox/
```

The single-file app is assembled from `web/index.html`, `web/style.css`, `web/app.js`, `web/worker.js` and the `greenbox/` sources. Commit the rebuilt `dist/GreenBox.html`; CI checks that it is up to date.

## Reference

T. Nazaré, K. Mahmoodi and E. Nepomuceno, "Green-Box System Identification and Carbon Footprint Analysis for Sustainable Computing", *OCEANS 2023 – Limerick*, IEEE, 2023.

Example data: the DC motor case study distributed with SysIdentPy (Lacerda et al., JOSS 2020); from nonlinearbenchmark.org, the Silverbox benchmark (Wigren & Schoukens, ECC 2013; estimation = first 10 000 samples of the filtered-Gaussian segment, validation = first 20 000 samples of the multisine part), the EMPS robot-joint positioning system (Janot, Gautier & Brunot, Nonlinear System Identification Benchmarks Workshop 2019; decimated from 1 kHz to 100 Hz) and the Wiener–Hammerstein benchmark (Schoukens, Suykens & Ljung, SYSID 2009; estimation = first 10 000 samples of the estimation record, validation = first 20 000 samples of the test record); and the LANL three-story structure (Figueiredo et al., Los Alamos National Laboratory report LA-14393, 2009; undamaged-state tests 1 and 2).

## License

MIT; see [LICENSE](LICENSE).
