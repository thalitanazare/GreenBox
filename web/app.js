"use strict";
// GreenBox GUI controller. All numerics run in Python (greenbox package) via
// the engine in worker.js; this file only manages the interface and plots.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------------------------------------------------------------- engine
function bytesToB64(bytes) { let s = ""; for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000)); return btoa(s); }
// Desktop app (desktop/): calculations run in native Python through the pywebview bridge;
// the processor-arithmetic calibration runs native machine code bundled with the application
// (WebAssembly here only when the bundle has no compiled benchmark).
const NativeEngine = (() => {
  let seq = 0;
  const progressFns = new Map();
  window.__gbProgress = (id, msg, frac) => { const f = progressFns.get(id); if (f) f(msg, frac); };
  const api = () => window.pywebview.api;
  const toB64 = bytesToB64;

  async function start() {
    if (!(window.pywebview && window.pywebview.api && window.pywebview.api.call))
      await new Promise((res) => window.addEventListener("pywebviewready", res, { once: true }));
    new Function($("#worker-src").textContent)(); // defines self.GreenBoxEngine (arithmetic benchmarks)
  }

  async function py(cmd, args, onProgress) {
    const id = ++seq;
    if (onProgress) progressFns.set(id, onProgress);
    try {
      const r = JSON.parse(await api().call(id, cmd, JSON.stringify(args)));
      if (!r.ok) throw new Error(r.error);
      return r.result;
    } finally { progressFns.delete(id); }
  }

  async function call(cmd, args = {}, onProgress) {
    if (cmd === "calibrate" && args.engine === "arith") {
      const r = await py("calibrate_native", {}, onProgress);
      if (r.available !== false) return r;
    }
    if (cmd === "calibrate" && args.engine === "arith")
      return new Promise((res, rej) => self.GreenBoxEngine.handle({ id: 0, cmd, args },
        (m) => (m.type === "progress" ? onProgress && onProgress(m.msg, m.frac) : m.ok ? res(m.result) : rej(new Error(m.error)))));
    if (cmd === "calibrate" && args.engine === "js") {
      const pts = self.GreenBoxEngine.calibrateJS(args.seconds * 1000, onProgress || (() => {}));
      return py("fit_calibration", { points: pts, engine: "JavaScript (JIT)" });
    }
    if (cmd === "load_file") return py("load_file", { fileId: args.fileId, name: args.name, b64: toB64(args.bytes) }, onProgress);
    return py(cmd, args, onProgress);
  }
  return { start, call, py };
})();

const Engine = window.GB_NATIVE ? NativeEngine : (() => {
  let worker = null;
  let seq = 0;
  const pending = new Map();

  function onMessage(m) {
    const p = pending.get(m.id);
    if (!p) return;
    if (m.type === "progress") { p.onProgress && p.onProgress(m.msg, m.frac); return; }
    pending.delete(m.id);
    m.ok ? p.resolve(m.result) : p.reject(new Error(m.error));
  }

  async function start() {
    const src = $("#worker-src").textContent;
    try {
      const url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
      worker = new Worker(url);
      worker.onmessage = (e) => onMessage(e.data);
      worker.onerror = (e) => console.error("worker error", e);
    } catch (err) {
      // Some browsers block workers on file:// pages: run on the main thread.
      console.warn("Web Worker unavailable, running on main thread:", err);
      await new Promise((res, rej) => {
        const s = document.createElement("script");
        s.src = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js";
        s.onload = res; s.onerror = rej;
        document.head.appendChild(s);
      });
      new Function(src)();
      worker = null;
    }
  }

  function call(cmd, args = {}, onProgress) {
    const id = ++seq;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject, onProgress });
      const msg = { id, cmd, args };
      if (worker) worker.postMessage(msg);
      else setTimeout(() => self.GreenBoxEngine.handle(msg, onMessage), 0);
    });
  }
  return { start, call };
})();

// ---------------------------------------------------------------- state
const S = {
  info: null,
  files: {},          // id -> {name, columns:[{name,n,min,max}]}
  nextFile: 1,
  family: "NARX",
  cost: "uniform",
  calibration: null,
  results: null,
  selected: 0,
  ready: false,
};

function setStatus(kind, text) {
  const el = $("#status");
  el.className = "status " + kind;
  $("#status-text").textContent = text;
  const sb = document.getElementById("sb-text");
  if (sb) { sb.textContent = text; sb.className = kind; }
}

function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

function fmt(x, d = 4) {
  if (x === null || x === undefined || Number.isNaN(x)) return "–";
  if (Math.abs(x) >= 1e6 || (Math.abs(x) < 1e-3 && x !== 0)) return x.toExponential(d - 1);
  if (Math.abs(x) >= 10 ** d) return Math.round(x).toString();
  const s = (+x).toPrecision(d);
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}
const fmtFixed = (x, d = 2) => (x === null || x === undefined) ? "–" : (+x).toFixed(d);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

// ---------------------------------------------------------------- navigation
function goto(step) {
  $$("#steps button").forEach((b) => b.classList.toggle("active", b.dataset.step === step));
  $$(".step").forEach((s) => s.classList.toggle("active", s.id === "step-" + step));
  window.scrollTo({ top: 0, behavior: "smooth" });
  requestAnimationFrame(() => $$(`#step-${step} .js-plotly-plot`).forEach((p) => Plotly.Plots.resize(p)));
}
$$("#steps button").forEach((b) => b.addEventListener("click", () => goto(b.dataset.step)));
$$("[data-goto]").forEach((b) => b.addEventListener("click", () => goto(b.dataset.goto)));

// "Learn more" pages are not in the navigation: open them from buttons, return with Back
let learnReturn = "home";
$$("[data-learn]").forEach((b) => b.addEventListener("click", () => {
  learnReturn = $(".step.active").id.replace("step-", "");
  goto(b.dataset.learn);
  if (b.dataset.learn === "learn-complexity") drawRho();
  if (b.dataset.anchor) setTimeout(() => document.getElementById(b.dataset.anchor).scrollIntoView({ behavior: "smooth" }), 50);
}));
$$("[data-back]").forEach((b) => b.addEventListener("click", () => goto(learnReturn)));

// ---------------------------------------------------------------- plotting
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function layout(extra = {}) {
  const grid = css("--border");
  const axis = { gridcolor: grid, zerolinecolor: grid, linecolor: grid, automargin: true };
  return Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, -apple-system, Segoe UI, sans-serif", size: 13, color: css("--text") },
    margin: { l: 60, r: 16, t: 36, b: 50 },
    legend: { orientation: "h", y: 1.02, yanchor: "bottom", x: 0, font: { size: 12 } },
    xaxis: { ...axis }, yaxis: { ...axis },
    hovermode: "closest",
  }, extra, {
    xaxis: Object.assign({ ...axis }, extra.xaxis || {}),
    yaxis: Object.assign({ ...axis }, extra.yaxis || {}),
  });
}
const plotCfg = { responsive: true, displaylogo: false, toImageButtonOptions: { format: "png", scale: 3 } };
function plot(id, traces, lay) {
  const el = document.getElementById(id);
  if (el.dataset.empty) { el.innerHTML = ""; delete el.dataset.empty; }
  Plotly.react(id, traces, layout(lay), plotCfg).then(() => typeof ensureExports === "function" && ensureExports());
}
function emptyPlot(id, text) {
  const el = document.getElementById(id);
  Plotly.purge(el);
  el.innerHTML = `<p class="hint">${text}</p>`;
  el.dataset.empty = "1";
}
const unitLabel = (u) => (u === "ns" ? "ns" : u + "s");

// Sequential ramp for Green models: sage #BDC4A7 -> #2A7F62 -> #254D32 (cheapest -> most accurate method)
const RAMP = [[189, 196, 167], [42, 127, 98], [37, 77, 50]];
function greenColor(t) {
  t = Math.min(Math.max(t, 0), 1) * (RAMP.length - 1);
  const i = Math.min(Math.floor(t), RAMP.length - 2), f = t - i;
  const c = RAMP[i].map((v, k) => Math.round(v + (RAMP[i + 1][k] - v) * f));
  return `rgb(${c.join(",")})`;
}
// Decision methods on the Pareto set, in menu order (greenbox.green.DECISION_METHODS)
const METHODS = {
  cheapest: { label: "Cheapest", math: "min C" },
  epsilon: { label: "ε-constraint", math: "min C subject to E ≤ ε" },
  weighted: { label: "Weighted sum (p = 1)", math: "min w<sub>E</sub>E + w<sub>C</sub>C" },
  euclidean: { label: "Euclidean (p = 2)", math: "min √((w<sub>E</sub>E)² + (w<sub>C</sub>C)²)" },
  tchebycheff: { label: "Tchebycheff (p = ∞)", math: "min max(w<sub>E</sub>E, w<sub>C</sub>C)" },
  accurate: { label: "Most accurate", math: "min E" },
};
const METHOD_KEYS = Object.keys(METHODS);
const methodShade = (k) => 0.25 + 0.75 * Math.max(METHOD_KEYS.indexOf(k), 0) / (METHOD_KEYS.length - 1);
function modelColor(m) { return greenColor(m.method ? methodShade(m.method) : 0.6); }

// ---------------------------------------------------------------- data step
function columnOptions(withNone) {
  const opts = withNone ? [`<option value="">— none (time series) —</option>`] : [`<option value="">— choose —</option>`];
  for (const [id, f] of Object.entries(S.files)) {
    opts.push(`<optgroup label="${esc(f.name)}">` +
      f.columns.map((c) => `<option value="${id}::${esc(c.name)}">${esc(c.name)} (${c.n})</option>`).join("") + "</optgroup>");
  }
  return opts.join("");
}

function refreshSelects() {
  for (const [sel, none] of [["#sel-u", true], ["#sel-y", false], ["#sel-uv", true], ["#sel-yv", false]]) {
    const el = $(sel), prev = el.value;
    el.innerHTML = columnOptions(none);
    if ([...el.options].some((o) => o.value === prev)) el.value = prev;
  }
  $("#file-list").innerHTML = Object.values(S.files).map((f) =>
    `<li><b>${esc(f.name)}</b><span>${f.columns.length} column(s) · ${f.columns[0]?.n ?? 0} samples</span></li>`).join("");
}

function guessColumns(fileId) {
  const cols = S.files[fileId].columns.map((c) => c.name);
  const find = (re) => cols.find((c) => re.test(c));
  const u = find(/^(u|uest|u_est|input|x|in)\b/i) ?? (cols.length > 1 ? cols[0] : "");
  const y = find(/^(y|yest|y_est|output|out)\b/i) ?? cols[cols.length > 1 ? 1 : 0];
  if (u !== undefined) $("#sel-u").value = u ? `${fileId}::${u}` : "";
  $("#sel-y").value = `${fileId}::${y}`;
  const uv = find(/^(uval|u_val|uv)\b/i), yv = find(/^(yval|y_val|yv)\b/i);
  if (yv) {
    setMode("separate");
    if (uv) $("#sel-uv").value = `${fileId}::${uv}`;
    $("#sel-yv").value = `${fileId}::${yv}`;
  }
}

async function addFile(name, bytes) {
  const fileId = "f" + S.nextFile++;
  setStatus("busy", `Reading ${name}…`);
  try {
    const desc = await Engine.call("load_file", { fileId, name, bytes });
    if (!desc.columns.length) throw new Error("no numeric columns found");
    S.files[fileId] = desc;
    refreshSelects();
    if (Object.keys(S.files).length === 1 || !$("#sel-y").value) guessColumns(fileId);
    setStatus("ready", "Ready");
    updatePreview();
    return fileId;
  } catch (e) {
    setStatus("error", `Could not read ${name}`);
    alert(`Could not read ${name}:\n${e.message}`);
  }
}

// Learn page: recommended structure of each family for each example (tools/tune_examples.py)
(function renderExampleStructures() {
  const ex = JSON.parse($("#examples").textContent);
  const fams = ["ARX", "ARMAX", "FIR", "NARX", "NARMAX", "Volterra"];
  const cell = (s) => !s ? "–" :
    `d${s.degree} · n<sub>y</sub>${s.lag_y} · n<sub>u</sub>${s.lag_u}${s.lag_e ? ` · n<sub>e</sub>${s.lag_e}` : ""}<br><small>R² ${s.r2.toFixed(3)} · ${s.terms} terms</small>`;
  $("#example-structures-table").innerHTML =
    `<thead><tr><th>Example</th>${fams.map((f) => `<th>${f}</th>`).join("")}</tr></thead><tbody>` +
    Object.values(ex).filter((e) => e.structures).map((e) =>
      `<tr><td>${esc(e.name)}</td>${fams.map((f) => `<td>${cell(e.structures[f])}</td>`).join("")}</tr>`).join("") + "</tbody>";
})();

// Source (citation) of the loaded example, shown under the data plot and under the results;
// cleared when the user opens a file of their own.
function setExample(ex) {
  S.example = ex || null;
  $$(".data-source").forEach((el) => {
    el.hidden = !S.example;
    el.innerHTML = S.example ? `<b>Data source</b> · ${esc(S.example.name)}: ${S.example.source}`
      + (S.example.url ? ` · <a href="${esc(S.example.url)}" target="_blank" rel="noopener">original data ↗</a>` : "") : "";
  });
  refreshDataExports();
}

// Figures that show measured signals: with an example loaded the data belong to a third party, so the
// "Data (CSV)" button sends the user to the original source instead of exporting the samples.
const MEASURED_PLOTS = ["plot-data", "plot-val"];
function refreshDataExports() {
  MEASURED_PLOTS.forEach((id) => {
    const gd = document.getElementById(id), bar = gd && gd.previousElementSibling;
    const b = bar && bar.classList.contains("export-bar") && bar.querySelector("button[data-x=csv]");
    if (!b) return;
    b.textContent = S.example ? "Data: original source ↗" : "Data (CSV)";
    b.title = S.example ? `These data belong to the ${S.example.name} data set: get them from the original source and cite it` : "";
  });
}

$("#file-input").addEventListener("change", async (ev) => {
  if (ev.target.files.length) setExample(null);
  for (const f of ev.target.files) await addFile(f.name, new Uint8Array(await f.arrayBuffer()));
  ev.target.value = "";
});

$$("[data-example]").forEach((b) => b.addEventListener("click", async () => {
  const ex = JSON.parse($("#examples").textContent)[b.dataset.example];
  const id = await addFile(ex.file, new TextEncoder().encode(ex.text));
  if (!id) return;
  setExample(ex);
  const s = ex.setup;
  setMode(s.mode);
  $("#sel-u").value = `${id}::${s.u}`;
  $("#sel-y").value = `${id}::${s.y}`;
  if (s.uv) $("#sel-uv").value = `${id}::${s.uv}`;
  if (s.yv) $("#sel-yv").value = `${id}::${s.yv}`;
  $("#continuation").checked = !!s.continuation;
  $("#pp-norm").value = s.normalise;
  $("#pp-ntrain").value = s.n_train ?? "";
  $("#pp-step").value = 1; $("#pp-start").value = 0; $("#pp-stop").value = "";
  if (s.structure) applyStructure(s.structure);
  const idf = s.identify || {};
  $$("input[name=struct-mode]").forEach((r) => (r.checked = r.value === (idf.structure || "search")));
  updateMethodVisibility();
  updatePreview();
}));

function setMode(mode) {
  $$("input[name=mode]").forEach((r) => (r.checked = r.value === mode));
  document.body.classList.toggle("mode-separate", mode === "separate");
  document.body.classList.toggle("mode-split", mode === "split");
}
$$("input[name=mode]").forEach((r) => r.addEventListener("change", () => { setMode(r.value); updatePreview(); }));
setMode("split");

function ref(sel) {
  const v = $(sel).value;
  if (!v) return null;
  const i = v.indexOf("::");
  return { file: v.slice(0, i), col: v.slice(i + 2) };
}

function dataSpec() {
  const mode = $("input[name=mode]:checked").value;
  return {
    mode, u: ref("#sel-u"), y: ref("#sel-y"), u_val: ref("#sel-uv"), y_val: ref("#sel-yv"),
    continuation: $("#continuation").checked,
    start: +$("#pp-start").value || 0, stop: +$("#pp-stop").value || null, step: +$("#pp-step").value || 1,
    normalise: $("#pp-norm").value, n_train: +$("#pp-ntrain").value || null,
  };
}

const updatePreview = debounce(async () => {
  updateRunState();
  $("#lin-result").innerHTML = "";
  const spec = dataSpec();
  if (!spec.y || (spec.mode === "separate" && !spec.y_val)) return;
  try {
    const p = await Engine.call("preview", { spec });
    S.dataInfo = p;
    $("#data-summary").textContent =
      `${p.n_train} estimation samples · ${p.n_val} validation samples` +
      (spec.mode === "split" || p.continuation ? " · validation continues the estimation record" : " · independent validation record");
    drawData(p);
  } catch (e) {
    $("#data-summary").textContent = "⚠ " + e.message;
  }
}, 200);
["#sel-u", "#sel-y", "#sel-uv", "#sel-yv", "#continuation", "#pp-start", "#pp-stop", "#pp-step", "#pp-norm", "#pp-ntrain"]
  .forEach((s) => $(s).addEventListener("input", updatePreview));

function drawData(p) {
  const sr = p.series, traces = [];
  const sameAxis = p.continuation;
  const off = sameAxis ? p.n_train : 0;
  const hasU = !!sr.u_train;
  const add = (s, offset, name, color, yaxis, dash) => s && traces.push({
    x: s.i.map((i) => i + offset), y: s.v, name, mode: "lines", yaxis,
    line: { color, width: 1.4, dash }, hovertemplate: "%{x}: %{y:.4g}<extra>" + name + "</extra>",
  });
  const cE = css("--data"), cV = css("--accent");
  add(sr.u_train, 0, "u · estimation", cE, "y", undefined);
  add(sr.u_val, off, "u · validation", cV, "y", undefined);
  add(sr.y_train, 0, "y · estimation", cE, hasU ? "y2" : "y", undefined);
  add(sr.y_val, off, "y · validation", cV, hasU ? "y2" : "y", undefined);
  const lay = {
    showlegend: true,
    xaxis: { title: { text: sameAxis ? "sample k" : "sample k (each record from 0)" }, anchor: hasU ? "y2" : "y" },
    yaxis: hasU ? { domain: [0.56, 1], title: { text: "u(k)" } } : { title: { text: "y(k)" } },
    shapes: sameAxis ? [{ type: "line", x0: p.n_train, x1: p.n_train, yref: "paper", y0: 0, y1: 1,
      line: { dash: "dash", color: css("--muted"), width: 1.2 } }] : [],
  };
  if (hasU) lay.yaxis2 = { domain: [0, 0.44], title: { text: "y(k)" }, gridcolor: css("--border"), zerolinecolor: css("--border"), automargin: true };
  plot("plot-data", traces, lay);
}

// ---------------------------------------------------------------- linearity
const LIN_LABEL = { "linear": "Linear", "weakly nonlinear": "Weakly nonlinear", "nonlinear": "Nonlinear" };
function linearityHTML(L) {
  const advice = L.label === "linear" ? "A linear model (ARX, FIR) should be enough."
    : "Use a nonlinear family: NARX/NARMAX, or Volterra when no output feedback is wanted.";
  return `<span class="verdict ${L.label.replace(" ", "-")}">${LIN_LABEL[L.label]}</span><span class="hint">${advice}</span>` +
    `<p class="lin-text">${esc(L.text)}</p>`;
}
$("#lin-run").addEventListener("click", async () => {
  const btn = $("#lin-run");
  btn.disabled = true;
  setStatus("busy", "Checking linearity…");
  try {
    const L = await Engine.call("linearity", { spec: dataSpec() }, (m, f) => ($("#lin-result").innerHTML = `<p class="hint">${esc(m)} (${Math.round(f * 100)}%)</p>`));
    $("#lin-result").innerHTML = linearityHTML(L);
    setStatus("ready", "Ready");
  } catch (e) {
    $("#lin-result").innerHTML = `<p class="bad">⚠ ${esc(e.message)}</p>`;
    setStatus("error", "Linearity check failed");
  } finally { btn.disabled = false; }
});

// ---------------------------------------------------------------- structure step
function renderFamilies() {
  $("#family-cards").innerHTML = S.info.families.map((f) => `
    <label class="choice"><input type="radio" name="family" value="${f.key}" ${f.key === S.family ? "checked" : ""}>
      <strong>${esc(f.label)}</strong><small>${esc(f.description)}</small></label>`).join("");
  $$("input[name=family]").forEach((r) => r.addEventListener("change", () => {
    selectFamily(r.value, true);
    const rec = S.example && S.example.structures && S.example.structures[r.value];
    if (rec) applyStructure({ family: r.value, ...rec });  // tuned for this example (tools/tune_examples.py)
  }));
}

function selectFamily(key, applyDefaults) {
  S.family = key;
  const f = S.info.families.find((x) => x.key === key);
  $$("input[name=family]").forEach((r) => (r.checked = r.value === key));
  const fields = [["#st-degree", f.degree, f.default_degree], ["#st-lagy", f.lag_y, f.default_lag_y],
    ["#st-lagu", null, f.default_lag_u], ["#st-lage", f.lag_e, f.default_lag_e]];
  for (const [sel, fixed, def] of fields) {
    const el = $(sel);
    el.disabled = fixed !== null && fixed !== undefined;
    if (el.disabled) el.value = fixed;
    else if (applyDefaults) el.value = def;
  }
  if (applyDefaults) { $("#st-ulag0").checked = f.default_u_lag0; $("#st-const").checked = f.default_constant; }
  $("#id-slag").value = f.search_max_lag;
  $("#id-sdeg").disabled = f.degree !== null && f.degree !== undefined;
  const hasNoise = f.lag_e === null || f.lag_e === undefined;
  $$(".noise-only").forEach((e) => (e.style.display = hasNoise ? "" : "none"));
  updateCandidates();
}

function applyStructure(s) {
  selectFamily(s.family, true);
  if (s.u_lag0 !== undefined) $("#st-ulag0").checked = s.u_lag0;
  for (const [k, sel] of [["degree", "#st-degree"], ["lag_y", "#st-lagy"], ["lag_u", "#st-lagu"], ["lag_e", "#st-lage"]])
    if (s[k] !== undefined && !$(sel).disabled) $(sel).value = s[k];
  updateCandidates();
}

function structureCfg() {
  return {
    family: S.family, degree: +$("#st-degree").value, lag_y: +$("#st-lagy").value, lag_u: +$("#st-lagu").value,
    lag_e: +$("#st-lage").value, u_lag0: $("#st-ulag0").checked, constant: $("#st-const").checked,
  };
}

const updateCandidates = debounce(async () => {
  if (!S.ready) return;
  try {
    const c = await Engine.call("candidates", { cfg: structureCfg() });
    S.candidates = c;
    $("#cand-count").textContent = `${c.n_process} process term${c.n_process === 1 ? "" : "s"}` +
      (c.n_noise ? ` + ${c.n_noise} noise` : "") + ` · max lag ${c.max_lag}`;
    $("#cand-list").innerHTML = c.terms.slice(0, 3000).map((t) => `<span>${esc(t)}</span>`).join("");
    const big = c.n_process > 3000;
    $("#cand-count").classList.toggle("bad", big);
  } catch (e) { $("#cand-count").textContent = "⚠ " + e.message; }
}, 200);
["#st-degree", "#st-lagy", "#st-lagu", "#st-lage", "#st-ulag0", "#st-const"].forEach((s) => $(s).addEventListener("input", updateCandidates));

// ---------------------------------------------------------------- complexity step
function rhoFor(key) {
  const B = +$("#cx-bits").value || 64, k = +$("#cx-kexp").value || 1.585;
  switch (key) {
    case "karatsuba": return B ** k / B;
    case "schoolbook": return B;
    case "toom3": return B ** (Math.log(5) / Math.log(3)) / B;
    case "schonhage": return Math.log2(B) * Math.log2(Math.log2(B));
    case "uniform": return 1;
    case "calibrated": return S.calibration ? S.calibration.t_mult_ns / S.calibration.t_add_ns : NaN;
  }
}

function renderCostCards() {
  const cards = S.info.cost_models.map((m) => ({ key: m.key, label: m.label, formula: m.formula }));
  cards.push({ key: "calibrated", label: "Calibrated on this computer",
    formula: S.calibration ? `${S.calibration.engine} · ${S.calibration.platform}` : "Run the calibration below first" });
  $("#cost-cards").innerHTML = cards.map((c) => {
    const dis = c.key === "calibrated" && !S.calibration;
    const rho = rhoFor(c.key);
    return `<label class="choice ${dis ? "disabled" : ""}"><input type="radio" name="cost" value="${c.key}" ${c.key === S.cost ? "checked" : ""} ${dis ? "disabled" : ""}>
      <strong>${esc(c.label)}${c.key === "calibrated" ? " · default" : ""}</strong><small>${esc(c.formula)}</small>
      <span class="tag">ρ = ${Number.isFinite(rho) ? fmt(rho, 4) : "–"}</span></label>`;
  }).join("");
  $$("input[name=cost]").forEach((r) => r.addEventListener("change", () => { S.cost = r.value; }));
}
["#cx-bits", "#cx-kexp"].forEach((s) => $(s).addEventListener("input", () => S.info && renderCostCards()));

function detectOS() {
  const ua = navigator.userAgent;
  const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  const os = /Mac/i.test(p + ua) ? "macOS" : /Win/i.test(p + ua) ? "Windows" : /Linux/i.test(p + ua) ? "Linux" : (p || "unknown OS");
  const br = window.GB_NATIVE ? "GreenBox desktop app" : /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "browser";
  return `${os} · ${br} · ${navigator.hardwareConcurrency || "?"} threads`;
}

function showArithmetic(c) {
  const L = c.latency, T = c.throughput, use = c.use || "latency";
  const warn = c.spread > 0.1 ? `<div class="warning" style="grid-column:1/-1">Repeated timings differ by ${Math.round(100 * c.spread)}%: other programs may be running. Close them and calibrate again.</div>` : "";
  $("#cal-result").innerHTML = `
    <div class="stat"><b>${fmt(c.t_add_ns, 3)} ns</b><span>t₊ · one addition</span></div>
    <div class="stat"><b>${fmt(c.t_mult_ns, 3)} ns</b><span>t× · one multiplication</span></div>
    <div class="stat"><b>${fmt(c.rho, 3)}</b><span>ρ = t× / t₊</span></div>
    <div class="stat"><label style="color:inherit">Weight from
      <select id="cal-use"><option value="latency" ${use === "latency" ? "selected" : ""}>latency (one operation)</option>
      <option value="throughput" ${use === "throughput" ? "selected" : ""}>throughput (many in parallel)</option></select></label></div>
    <div class="stat" style="grid-column:1/-1"><span>Latency: + ${fmt(L.add, 3)} ns, × ${fmt(L.mult, 3)} ns (ρ ${fmt(L.mult / L.add, 3)}) ·
      throughput: + ${fmt(T.add, 3)} ns, × ${fmt(T.mult, 3)} ns (ρ ${fmt(T.mult / T.add, 3)}) ·
      ${esc(c.engine)} · ${esc(c.platform)} · ${esc(c.when || "")}</span></div>${warn}`;
  $("#cal-use").addEventListener("change", (e) => {
    const src = e.target.value === "latency" ? L : T;
    saveCalibration({ ...c, use: e.target.value, t_add_ns: src.add, t_mult_ns: src.mult, rho: src.mult / src.add });
  });
  plot("plot-cal", [
    { x: ["latency", "throughput"], y: [L.add, T.add], type: "bar", name: "addition", marker: { color: greenColor(0.55) } },
    { x: ["latency", "throughput"], y: [L.mult, T.mult], type: "bar", name: "multiplication", marker: { color: css("--contrast") } },
  ], { barmode: "group", yaxis: { title: { text: "ns per operation" } } });
}

function showCalibration(c) {
  if (!c) { $("#cal-result").innerHTML = ""; return; }
  if (c.kind === "arithmetic") { showArithmetic(c); return; }
  const warn = !c.valid ? `<div class="warning" style="grid-column:1/-1">The fit gave a non-positive cost, so it cannot be used as a weight. Run again with a longer duration, or with the other engine.</div>`
    : c.r2 < 0.9 ? `<div class="warning" style="grid-column:1/-1">Low R² (${fmtFixed(c.r2, 3)}): the timings are noisy. Close other programs and use a longer duration.</div>` : "";
  $("#cal-result").innerHTML = `
    <div class="stat"><b>${fmt(c.t_add_ns, 3)} ns</b><span>t₊ per addition</span></div>
    <div class="stat"><b>${fmt(c.t_mult_ns, 3)} ns</b><span>t× per multiplication</span></div>
    <div class="stat"><b>${fmt(c.rho, 3)}</b><span>ρ = t× / t₊</span></div>
    <div class="stat"><b>${fmtFixed(c.r2, 4)}</b><span>R² of the fit</span></div>
    <div class="stat" style="grid-column:1/-1"><span>${esc(c.engine)} · ${esc(c.platform)} · ${esc(c.when || "")}</span></div>${warn}`;
  const pts = c.points || [];
  if (pts.length) {
    const fitted = pts.map((p) => c.t0_ns + c.t_add_ns * p[0] + c.t_mult_ns * p[1]);
    const lo = Math.min(...fitted, ...pts.map((p) => p[2])), hi = Math.max(...fitted, ...pts.map((p) => p[2]));
    plot("plot-cal", [
      { x: [lo, hi], y: [lo, hi], mode: "lines", line: { color: css("--muted"), dash: "dot", width: 1 }, name: "ideal", hoverinfo: "skip" },
      { x: fitted, y: pts.map((p) => p[2]), mode: "markers", name: "structures",
        marker: { color: pts.map((p) => p[1]), colorscale: [[0, greenColor(0.1)], [1, greenColor(1)]], size: 8,
          colorbar: { title: { text: "b" }, thickness: 10 } },
        text: pts.map((p) => `a = ${p[0]}, b = ${p[1]}`), hovertemplate: "%{text}<br>fit %{x:.2f} ns · measured %{y:.2f} ns<extra></extra>" },
    ], { xaxis: { title: { text: "fitted t₀ + t₊a + t×b (ns/sample)" } }, yaxis: { title: { text: "measured (ns/sample)" } }, showlegend: false });
  }
}

function saveCalibration(c) {
  S.calibration = c;
  try { localStorage.setItem("greenbox.calibration", JSON.stringify(c)); } catch (_) { /* storage blocked */ }
  if (c && c.valid) S.cost = "calibrated";
  renderCostCards();
  showCalibration(c);
}

$("#cal-run").addEventListener("click", async () => {
  const btn = $("#cal-run");
  btn.disabled = true;
  setStatus("busy", "Calibrating…");
  try {
    const engine = $("#cal-engine").value;
    const c = await Engine.call("calibrate", { engine, seconds: +$("#cal-dur").value },
      (m, f) => ($("#cal-progress").textContent = `${m} (${Math.round(f * 100)}%)`));
    c.platform = detectOS();
    c.when = new Date().toLocaleString();
    $("#cal-progress").textContent = "";
    saveCalibration(c);
    setStatus("ready", "Ready");
  } catch (e) {
    setStatus("error", "Calibration failed");
    $("#cal-progress").textContent = "⚠ " + e.message;
  } finally { btn.disabled = false; }
});

$("#cal-manual").addEventListener("click", () => {
  const ta = +$("#cal-tadd").value, tm = +$("#cal-tmult").value;
  if (!(ta > 0 && tm > 0)) { alert("Enter two positive times."); return; }
  saveCalibration({ engine: "manual entry", platform: detectOS(), t0_ns: 0, t_add_ns: ta, t_mult_ns: tm,
    rho: tm / ta, r2: NaN, valid: true, points: [], when: new Date().toLocaleString() });
});

// ---------------------------------------------------------------- identification
function parseMethods() {
  return $$("#id-methods input:checked").map((b) => b.value);
}
const WEIGHTED = ["weighted", "euclidean", "tchebycheff"];  // the methods that use w_E
const manyWeights = () => (S.results?.config?.green_weights?.length || 0) > 1;
const wTag = (m) => (WEIGHTED.includes(m.method) && manyWeights() ? `, w<sub>E</sub> = ${+m.w_E}` : "");
const methodLabel = (m) => (METHODS[m.method]?.label ?? m.method) + wTag(m);
const methodMath = (m) => METHODS[m.method]?.math ?? "";

// accuracy weights of the p = 1, 2, ∞ methods: from, to, step (one weight when from = to)
function weightValues() {
  const a = +$("#id-gw-from").value, b = +$("#id-gw-to").value, s = +$("#id-gw-step").value;
  if (!(a >= 0 && a <= 1 && b >= 0 && b <= 1)) throw new Error("The accuracy weights must lie between 0 and 1.");
  if (b <= a || !(s > 0)) return [a];
  const n = Math.floor((b - a) / s + 1e-9);
  if (n > 20) throw new Error("At most 21 accuracy weights: use a larger step.");
  const ws = Array.from({ length: n + 1 }, (_, i) => +(a + i * s).toFixed(4));
  if (b - ws[ws.length - 1] > 1e-9) ws.push(b);
  return ws;
}
function showWeights() {
  let txt;
  try {
    const ws = weightValues();
    txt = ws.length === 1 ? `One weight: w<sub>E</sub> = ${ws[0]}, w<sub>C</sub> = ${+(1 - ws[0]).toFixed(4)}.`
      : `${ws.length} weights: w<sub>E</sub> = ${ws.join(", ")}. Each p = 1, 2, ∞ method gives one model per weight.`;
  } catch (e) { txt = `<span class="bad">${esc(e.message)}</span>`; }
  $("#id-gw-list").innerHTML = txt;
}
["#id-gw-from", "#id-gw-to", "#id-gw-step"].forEach((s) => $(s).addEventListener("input", showWeights));
$$(".gw-presets button").forEach((b) => b.addEventListener("click", () => {
  const [a, z, s] = b.dataset.gw.split(",");
  $("#id-gw-from").value = a; $("#id-gw-to").value = z; $("#id-gw-step").value = s;
  showWeights();
}));
showWeights();

// models shown in the results: "all" = every distinct Green model once, or the models of one method.
// Models picked by several methods or weights share model_key.
const modelKey = (m) => m.model_key || m.name;
function viewGroups() {
  const R = S.results, groups = new Map();
  R.models.forEach((m, i) => {
    if (S.view !== "all" && m.method !== S.view) return;
    if (!groups.has(modelKey(m))) groups.set(modelKey(m), { m, i, members: [] });
    groups.get(modelKey(m)).members.push(m);
  });
  return [...groups.values()].sort((a, b) => a.m.complexity - b.m.complexity || a.i - b.i);
}
// "Weighted sum (p = 1) (w_E 0.25, 0.5) · Euclidean (p = 2) (w_E 0.5)"
function chosenBy(members, html = true) {
  return METHOD_KEYS.filter((k) => members.some((m) => m.method === k)).map((k) => {
    const ws = members.filter((m) => m.method === k).map((m) => +m.w_E);
    const w = WEIGHTED.includes(k) && manyWeights() ? ` (${html ? "w<sub>E</sub>" : "w_E"} ${ws.join(", ")})` : "";
    return METHODS[k].label + w;
  }).join(" · ");
}

function updateMethodVisibility() {
  const manual = $("input[name=struct-mode]:checked").value === "manual";
  document.body.classList.toggle("struct-manual", manual);
  const gsel = $("#id-gsel").value;
  document.body.classList.toggle("green-classical", gsel === "classical");
  document.body.classList.toggle("green-simulation", gsel === "simulation");
}
$$("input[name=struct-mode]").forEach((r) => r.addEventListener("change", updateMethodVisibility));
$("#id-gsel").addEventListener("change", updateMethodVisibility);
updateMethodVisibility();

function runConfig() {
  const c = S.calibration;
  return {
    ...structureCfg(),
    run_standard: false,  // the GUI offers only the Green identification
    structure_search: $("input[name=struct-mode]:checked").value === "search",
    search_max_degree: +$("#id-sdeg").value, search_max_lag: +$("#id-slag").value, target_r2: +$("#id-target").value,
    holdout_fraction: +$("#id-hold").value,
    drop_dependent: !$("#id-keepdep").checked,
    min_terms: +$("#id-min").value, max_terms: +$("#id-max").value,
    noise_terms: $$(".noise-only")[0].style.display === "none" ? 0 : +$("#id-noise").value,
    els_iterations: +$("#id-els").value,
    green_methods: parseMethods(), green_weights: weightValues(), green_epsilon: +$("#id-geps").value,
    green_weight: (() => { const ws = weightValues(); return ws[Math.floor(ws.length / 2)]; })(),
    residual_lags: +$("#id-rlags").value,
    green_selection: $("#id-gsel").value, green_min_r2: +$("#id-gmin").value,
    cost_model: S.cost, bits: +$("#cx-bits").value, mult_exponent: +$("#cx-kexp").value,
    t_add_ns: c ? c.t_add_ns : 0, t_mult_ns: c ? c.t_mult_ns : 0,
    calibration: c ? { engine: c.engine, platform: c.platform, rho: c.rho, r2: c.r2, when: c.when } : {},
    emission_mg_per_s: +$("#cx-emis").value, ref_mult_seconds: +$("#cx-tref").value,
  };
}

function updateRunState() {
  const ok = S.ready && $("#sel-y").value && ($("input[name=mode]:checked").value === "split" || $("#sel-yv").value);
  $("#run-btn").disabled = !ok;
  $("#lin-run").disabled = !ok || !$("#sel-u").value;
  $("#run-msg").textContent = !S.ready ? "Waiting for the Python runtime…" : ok ? "" : "Choose the data first (step 1).";
  updateToolbar();
}

// toolbar follows the workflow: actions are enabled only when they make sense, and the
// guide on the right says what to do next
function updateToolbar() {
  const tb = (k) => document.querySelector(`[data-tb=${k}]`);
  if (!tb("run")) return;
  const hasData = !!(S.ready && $("#sel-y").value && ($("input[name=mode]:checked").value === "split" || $("#sel-yv").value));
  const hasResults = !!S.results;
  tb("run").disabled = !hasData;
  tb("run").title = hasData ? "Identify the Green models (one per decision method) with the current settings (steps 2–4)"
    : "Load data first: open a data file or choose an example";
  tb("save").disabled = !hasResults;
  tb("save").title = hasResults ? "Save all results (models, metrics, settings) as a JSON file" : "Run an identification first";
  tb("open").disabled = tb("calibrate").disabled = $("#tb-example").disabled = !S.ready;
  const guide = !S.ready ? "wait while GreenBox starts"
    : !hasData ? "load data: Open data file… or Load example (step 1)"
    : !hasResults ? "review steps 2–4 (structure, complexity, identification), then press Run identification"
    : "inspect the results (step 5), export figures and tables, or Save results…";
  $("#tb-guide").textContent = guide;
}

$("#run-btn").addEventListener("click", async () => {
  let cfg;
  try { cfg = runConfig(); } catch (e) { alert(e.message); return; }
  if (!cfg.green_methods.length) { alert("Choose at least one decision method."); return; }
  const btn = $("#run-btn");
  btn.disabled = true;
  setStatus("busy", "Identifying…");
  const t0 = performance.now();
  $("#run-msg").textContent = "Starting…";
  try {
    const res = await Engine.call("identify", { spec: dataSpec(), cfg }, (m, f) => {
      $("#run-bar").style.width = `${Math.round(f * 100)}%`;
      $("#run-msg").textContent = m;
    });
    res.config = cfg; res.data_spec = dataSpec();
    S.results = res; S.selected = 0; S.view = "all";
    $("#run-msg").textContent = `Finished in ${((performance.now() - t0) / 1000).toFixed(1)} s.`;
    $("#results-tab").disabled = false;
    updateToolbar();
    setStatus("ready", "Ready");
    renderResults();
    goto("results");
  } catch (e) {
    setStatus("error", "Identification failed");
    $("#run-msg").textContent = "⚠ " + e.message;
  } finally { btn.disabled = false; $("#run-bar").style.width = "0"; }
});

// ---------------------------------------------------------------- results
function renderResults() {
  const R = S.results;
  const cm = R.cost_model;
  // each Green model has its own structure; the header shows that of the reference (most accurate) model
  const ref = R.models.find((m) => m.name === R.reference) || R.models[R.models.length - 1];
  const st = { ...R.structure, ...((ref && ref.structure) || {}) };
  const fam = S.info.families.find((f) => f.key === st.family);
  $("#res-cost-label").textContent = `· ${fam ? fam.label : st.family}, most accurate model: degree ${st.degree}, lags y ${st.lag_y} / u ${st.lag_u}` +
    ` · cost model: ${cm.label} (ρ = ${fmt(cm.rho, 4)})`;
  $("#warnings").innerHTML = R.warnings.map((w) => `<div class="warning">⚠ ${esc(w)}</div>`).join("") +
    (R.notes || []).map((w) => `<div class="note">ℹ ${esc(w)}</div>`).join("");
  $("#lin-card").style.display = R.linearity ? "" : "none";
  if (R.linearity) $("#lin-result-res").innerHTML = linearityHTML(R.linearity);
  renderSearch();
  if (!S.view || (S.view !== "all" && !R.models.some((m) => m.method === S.view))) S.view = "all";
  renderView();
  renderSummary();
  renderValidation();
  renderTradeoff();
  renderPareto();
  renderModel();
}

function renderSearch() {
  const s = S.results.search || (S.results.green_pool && S.results.green_pool.search);
  $("#search-card").style.display = s ? "" : "none";
  if (!s) return;
  const sel = s.rows.find((r) => r.selected);
  $("#search-msg").innerHTML = `${s.rows.length} structures evaluated by free-run R² on the test part. ` +
    (s.reached === undefined ? `Last structure kept by the search: degree ${sel.degree}, lags y ${sel.lag_y} / u ${sel.lag_u}; each Green model keeps its own structure.`
      : s.reached ? `Selected: degree ${sel.degree}, lags y ${sel.lag_y} / u ${sel.lag_u} (test R² ${fmt(sel.holdout_r2)} ≥ target ${s.target_r2}).`
      : `<span class="bad">No structure reached the target R² ${s.target_r2}</span>; the best one is used (test R² ${fmt(s.best_holdout_r2)}).`);
  const rows = [...s.rows].sort((a, b) => (b.holdout_r2 ?? -1e9) - (a.holdout_r2 ?? -1e9)).slice(0, 10);
  $("#search-table").innerHTML = `<thead><tr><th>Degree</th><th>Output lags</th><th>Input lags</th><th>Candidates</th><th>Terms</th><th>Test R²</th></tr></thead><tbody>` +
    rows.map((r) => `<tr class="${r.selected ? "selected" : ""}"><td>${r.degree}${r.selected ? " ✓" : ""}</td><td>${r.lag_y}</td><td>${r.lag_u}</td><td>${r.candidates}</td><td>${r.terms}</td><td>${fmt(r.holdout_r2)}</td></tr>`).join("") +
    `</tbody>`;
}

function nameHTML(g) {
  const m = g.m;
  if (m.kind !== "green") return esc(m.name);
  const sub = g.members.length > 1 && new Set(g.members.map((x) => x.method)).size > 1 ? "picked by these methods" : methodMath(m);
  return `Green · ${chosenBy(g.members)}<span class="model-sub">${sub}</span>`;
}

function renderView() {
  const R = S.results, present = METHOD_KEYS.filter((k) => R.models.some((m) => m.method === k));
  const n = new Set(R.models.filter((m) => m.kind === "green").map(modelKey)).size;
  $("#res-view").innerHTML = `<button class="btn small${S.view === "all" ? " on" : ""}" data-view="all">All methods · ${n} distinct models</button>` +
    present.map((k) => `<button class="btn small${S.view === k ? " on" : ""}" data-view="${k}">${METHODS[k].label}</button>`).join("");
  $$("#res-view button").forEach((b) => b.addEventListener("click", () => {
    S.view = b.dataset.view;
    const gs = viewGroups(), cur = modelKey(R.models[S.selected]);
    S.selected = (gs.find((g) => modelKey(g.m) === cur) || gs[0]).i;
    renderView(); renderSummary(); renderValidation(); renderTradeoff(); renderPareto(); renderModel();
  }));
}

function renderSummary() {
  const R = S.results, ref = R.reference, hasRef = !!ref;
  const head = ["Model", "Terms", "NRMSE", "R²", "Adds", "Mults", `Complexity`, ...(hasRef ? ["ΔC vs most accurate", "ΔNRMSE vs most accurate"] : []),
    "Time/sample", "CO₂e/sample", "Pareto"];
  const selKey = modelKey(R.models[S.selected]);
  const rows = viewGroups().map((g) => {
    const m = g.m, i = g.i, dC = m.complexity_reduction_pct, dE = m.nrmse_change_pct;
    const isRef = R.models.some((x) => x.name === ref && modelKey(x) === modelKey(m));
    return `<tr data-i="${i}" class="${modelKey(m) === selKey ? "selected" : ""}">
      <td><span class="swatch" style="background:${modelColor(m)}"></span>${nameHTML(g)}</td>
      <td>${m.n_terms}</td><td>${fmt(m.metrics.NRMSE)}</td><td>${fmt(m.metrics.R2)}</td>
      <td>${m.n_add}</td><td>${m.n_mult}</td><td>${fmt(m.complexity, 5)}</td>
      ${hasRef ? `<td class="${dC > 0 ? "good" : ""}">${isRef ? "reference" : dC === null || dC === undefined ? "–" : fmtFixed(dC, 1) + " %"}</td>
      <td class="${dE > 0 ? "bad" : dE < 0 ? "good" : ""}">${isRef ? "reference" : dE === null || dE === undefined ? "–" : (dE > 0 ? "+" : "") + fmtFixed(dE, 1) + " %"}</td>` : ""}
      <td>${fmt(m.time_ns_per_sample, 3)} ns</td><td>${fmt(m.carbon_mg_per_sample, 3)} mg</td>
      <td>${m.pareto ? "★" : ""}</td></tr>`;
  }).join("");
  $("#summary").innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows}</tbody>`;
  $$("#summary tbody tr").forEach((tr) => tr.addEventListener("click", () => {
    S.selected = +tr.dataset.i; renderSummary(); renderValidation(); renderModel(); renderTradeoff(); renderPareto();
  }));
}

function valX() {
  const R = S.results;
  return R.y_val.map((_, i) => i + R.val_offset);
}

function renderValidation() {
  const R = S.results, x = valX(), sel = R.models[S.selected];
  const traces = [{ x, y: R.y_val, name: "measured", mode: "lines", line: { color: css("--data"), width: 1.6 } }];
  const ref = R.models.find((m) => m.name === R.reference);
  const show = [ref, sel].filter((m, i, a) => m && a.indexOf(m) === i);
  for (const m of show) {
    traces.push({ x, y: m.yhat, name: m.name, mode: "lines",
      line: { color: modelColor(m), width: 1.6, dash: m === ref && m !== sel ? "dash" : "solid" } });
  }
  plot("plot-val", traces, { xaxis: { title: { text: "sample k" } }, yaxis: { title: { text: "y(k)" } } });
}

function renderTradeoff() {
  const R = S.results, ms = R.models.filter((m) => m.metrics.NRMSE !== null);
  const pareto = ms.filter((m) => m.pareto).sort((a, b) => a.complexity - b.complexity);
  const traces = [{
    x: pareto.map((m) => m.complexity), y: pareto.map((m) => m.metrics.NRMSE), mode: "lines", name: "Pareto front",
    line: { shape: "hv", color: css("--muted"), dash: "dot", width: 1.2 }, hoverinfo: "skip",
  }];
  const gs = viewGroups().filter((g) => g.m.kind === "green" && g.m.metrics.NRMSE !== null);
  const greens = gs.map((g) => g.m), selKey = modelKey(R.models[S.selected]);
  traces.push({
    x: greens.map((m) => m.complexity), y: greens.map((m) => m.metrics.NRMSE), mode: "markers", name: "Green",
    text: gs.map((g) => `Green · ${chosenBy(g.members)}<br>${g.m.n_terms} terms`),
    marker: { size: greens.map((m) => (modelKey(m) === selKey ? 15 : 10)), color: greens.map(modelColor),
      line: { color: css("--surface"), width: 1 } },
    hovertemplate: "%{text}<br>C = %{x:.4g}<br>NRMSE = %{y:.4g}<extra></extra>",
  });
  plot("plot-trade", traces, { xaxis: { title: { text: `complexity per sample (${unitLabel(R.cost_model.unit)})` } },
    yaxis: { title: { text: "validation NRMSE" } } });
}

// Pareto front of the admissible candidates (test error against complexity) and the model
// each decision method picks from it
function renderPareto() {
  const R = S.results, p = R.green_pool;
  const gs = viewGroups().filter((g) => g.m.kind === "green" && g.m.selection?.method === "simulation");
  if (!p || !gs.length) { emptyPlot("plot-sweep", "The Pareto front is drawn when the Green models are chosen by free-run simulation."); return; }
  const err = (r2) => Math.sqrt(Math.max(1 - r2, 0));
  const adm = p.complexity.map((_, i) => i).filter((i) => p.admissible[i] && p.holdout_r2[i] !== null);
  if (!adm.length) { emptyPlot("plot-sweep", "No admissible candidate: no Pareto front."); return; }
  const front = [];
  [...adm].sort((a, b) => p.complexity[a] - p.complexity[b] || p.holdout_r2[b] - p.holdout_r2[a]).forEach((i) => {
    if (!front.length || err(p.holdout_r2[i]) < err(p.holdout_r2[front[front.length - 1]]) - 1e-12) front.push(i);
  });
  const selKey = modelKey(R.models[S.selected]);
  const traces = [
    { x: adm.map((i) => p.complexity[i]), y: adm.map((i) => err(p.holdout_r2[i])), mode: "markers", name: "admissible candidates",
      marker: { color: css("--muted"), size: 5, opacity: 0.35 }, hoverinfo: "skip" },
    { x: front.map((i) => p.complexity[i]), y: front.map((i) => err(p.holdout_r2[i])), mode: "lines+markers", name: "Pareto front",
      line: { shape: "hv", color: css("--muted"), dash: "dot", width: 1.2 }, marker: { color: css("--muted"), size: 6 }, hoverinfo: "skip" },
  ];
  const hover = (g) => `${chosenBy(g.members)}<br>${g.m.n_terms} terms<br>C = %{x:.4g}<br>test error = %{y:.4g}<extra></extra>`;
  if (gs.length <= 6) {  // one legend entry per model
    traces.push(...gs.map((g) => ({ x: [g.m.complexity], y: [err(g.m.holdout_r2)], mode: "markers", name: chosenBy(g.members, false),
      marker: { size: modelKey(g.m) === selKey ? 16 : 11, color: modelColor(g.m), line: { color: css("--surface"), width: 1 } },
      hovertemplate: hover(g) })));
  } else {  // many models (several weights): one legend entry, the methods on hover
    traces.push({ x: gs.map((g) => g.m.complexity), y: gs.map((g) => err(g.m.holdout_r2)), mode: "markers", name: "Green models (methods on hover)",
      marker: { size: gs.map((g) => (modelKey(g.m) === selKey ? 16 : 11)), color: gs.map((g) => modelColor(g.m)),
        line: { color: css("--surface"), width: 1 } },
      text: gs.map((g) => chosenBy(g.members)), hovertemplate: "%{text}<br>C = %{x:.4g}<br>test error = %{y:.4g}<extra></extra>" });
  }
  plot("plot-sweep", traces, { xaxis: { title: { text: `complexity per sample (${unitLabel(R.cost_model.unit)})` } },
    yaxis: { title: { text: "test error E = √(1 − R²)" } } });
}

const isNoise = (m, i) => m.codes[i].some((c) => Math.floor(c / 1000) === 3);

function equationText(m, which = "process") {
  const idx = m.terms.map((_, i) => i).filter((i) => (which === "noise") === isNoise(m, i));
  if (!idx.length) return "";
  let s = idx.map((i) => {
    const th = m.theta[i], t = m.terms[i];
    return `${th < 0 ? " − " : " + "}${Math.abs(th).toPrecision(6)}${t === "1" ? "" : " · " + t}`;
  }).join("\n      ").trimStart();
  if (s.startsWith("+ ")) s = s.slice(2);
  else if (s.startsWith("− ")) s = "−" + s.slice(2);
  return which === "noise" ? `      ${s.startsWith("−") ? s : "+ " + s}` : `y(k) = ${s}`;
}

function renderModel() {
  const R = S.results, m = R.models[S.selected];
  $("#model-title").innerHTML = `<span class="swatch" style="background:${modelColor(m)}"></span>` +
    (m.kind !== "green" ? esc(m.name) : S.view === "all" ? `Green model · ${chosenBy(R.models.filter((x) => modelKey(x) === modelKey(m)))}`
      : `Green model · ${methodLabel(m)} <span class="hint">(${methodMath(m)})</span>`);
  const nNoise = m.codes.filter((_, i) => isNoise(m, i)).length;
  $("#model-stats").innerHTML = [
    [nNoise ? `${m.n_terms - nNoise} + ${nNoise}` : m.n_terms, nNoise ? "process + noise terms" : "terms"], [fmt(m.metrics.NRMSE), "validation NRMSE (free run)"], [fmt(m.metrics_osa_train.NRMSE), "training NRMSE (one step)"],
    [`${m.n_add} + ${m.n_mult}`, nNoise ? "additions + multiplications (process model)" : "additions + multiplications per sample"], [fmt(m.complexity, 5), `complexity (${unitLabel(R.cost_model.unit)})`],
    [`${fmt(m.time_ns_per_sample, 3)} ns`, "est. time per sample"], [`${fmt(m.carbon_mg_validation, 3)} mg`, `CO₂e for ${R.y_val.length} validation samples`],
  ].map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
  $("#equation").textContent = equationText(m);
  const noiseEq = equationText(m, "noise");
  $("#noise-eq").style.display = noiseEq ? "" : "none";
  $("#noise-equation").textContent = noiseEq;
  const totErr = m.err.reduce((a, b) => a + b, 0);
  $("#model-table").innerHTML = `<thead><tr><th>#</th><th style="text-align:left">Term</th><th style="text-align:left">Part</th><th>Coefficient θ</th><th>Std. dev.</th><th>ERR (%)</th></tr></thead><tbody>` +
    m.terms.map((t, i) => `<tr class="${isNoise(m, i) ? "noise-row" : ""}"><td>${i + 1}</td><td class="term">${esc(t)}</td><td style="text-align:left">${isNoise(m, i) ? "noise" : "process"}</td><td>${fmt(m.theta[i], 6)}</td><td>${fmt(m.std[i], 3)}</td><td>${fmtFixed(100 * m.err[i], 4)}</td></tr>`).join("") +
    `<tr><td></td><td class="term"><b>Σ ERR</b></td><td></td><td></td><td></td><td><b>${fmtFixed(100 * totErr, 4)}</b></td></tr></tbody>`;
  renderResiduals(m);
  renderSize(m);
  ensureExports();
}

function renderResiduals(m) {
  const rt = m.residual_tests, names = { "r_ee": "r<sub>εε</sub>", "r_e(ee)": "r<sub>ε(εε)</sub>", "r_ue": "r<sub>uε</sub>", "r_u2e": "r<sub>u²'ε</sub>", "r_u2e2": "r<sub>u²'ε²</sub>" };
  const keys = Object.keys(rt.tests);
  const pal = [css("--data"), greenColor(0.55), css("--contrast"), greenColor(1), css("--muted")];
  const traces = keys.map((k, i) => ({ x: rt.lags, y: rt.tests[k], name: names[k] || k, mode: "lines", line: { width: 1.3, color: pal[i % pal.length] },
    visible: i < 3 ? true : "legendonly" }));
  const b = rt.bound;
  plot("plot-resid", traces, {
    xaxis: { title: { text: "lag τ" } }, yaxis: { title: { text: "correlation" }, range: [-1, 1] },
    shapes: [b, -b].map((v) => ({ type: "line", xref: "paper", x0: 0, x1: 1, y0: v, y1: v, line: { dash: "dash", color: css("--muted"), width: 1 } })),
  });
}

function renderSize(m) {
  if (m.kind === "green" && m.selection.method === "simulation") {
    const p = S.results.green_pool, sel = m.selection.index, all = p.complexity.map((_, i) => i);
    $("#sel-title").innerHTML = `Model choice: ${methodLabel(m)}, ${methodMath(m)} over the admissible candidates` +
      ` <span class="hint">(E and C scaled to 0–1${["weighted", "euclidean", "tchebycheff"].includes(m.method) ? `; w<sub>E</sub> = ${fmtFixed(m.w_E, 2)}, w<sub>C</sub> = ${fmtFixed(1 - m.w_E, 2)}`
        : m.method === "epsilon" ? `; ε = ${fmtFixed(m.epsilon, 2)}` : ""})</span>`;
    const finite = all.filter((i) => p.holdout_r2[i] !== null);
    const lo = Math.max(Math.min(...finite.map((i) => p.holdout_r2[i])), p.r2_limit - 0.4, -1);
    const pts = (ids, name, marker) => ({
      x: ids.map((i) => p.complexity[i]), y: ids.map((i) => p.holdout_r2[i]), mode: "markers", name, marker,
      customdata: ids.map((i) => `${p.n_terms[i]} terms · score ${fmt(m.selection.cost[i], 3)}<br>${p.terms[i].map(esc).join(" + ")}`),
      hovertemplate: "complexity %{x:.4g}<br>test R² %{y:.4f}<br>%{customdata}<extra></extra>",
    });
    plot("plot-size", [
      pts(finite.filter((i) => !p.admissible[i]), "not admissible", { color: css("--muted"), size: 6, opacity: 0.5 }),
      pts(finite.filter((i) => p.admissible[i]), "admissible", { color: greenColor(0.6), size: 8 }),
      pts([sel], "selected", { symbol: "x", size: 14, color: greenColor(1), line: { width: 2 } }),
    ], {
      xaxis: { title: { text: "complexity per sample" } }, yaxis: { title: { text: "test R² (free run)" }, range: [lo, 1 + 0.03 * (1 - lo)] },
      shapes: [
        { type: "line", xref: "paper", x0: 0, x1: 1, y0: p.r2_limit, y1: p.r2_limit, line: { dash: "dash", color: css("--muted"), width: 1 } },
      ],
    });
  } else {
    const s = m.selection;
    $("#sel-title").innerHTML = `Number of terms: ${methodLabel(m)}, ${methodMath(m)} with E the unexplained output variance`;
    plot("plot-size", [
      { x: s.k, y: s.cost, mode: "lines+markers", name: "score, lower is better", line: { color: greenColor(0.9) }, marker: { size: 5 } },
      { x: s.k, y: s.cumulative_err, mode: "lines", name: "cumulative ERR", yaxis: "y2", line: { color: css("--muted"), dash: "dot" } },
      { x: [s.k_selected], y: [s.cost[s.k.indexOf(s.k_selected)]], mode: "markers", name: `selected: ${s.k_selected}`,
        marker: { symbol: "x", size: 14, color: css("--contrast"), line: { width: 2 } } },
    ], { xaxis: { title: { text: "number of terms k" } }, yaxis: { title: { text: "score" } },
      yaxis2: { title: { text: "Σ ERR" }, overlaying: "y", side: "right", showgrid: false, range: [0, 1.02], automargin: true } });
  }
}

// ---------------------------------------------------------------- exports
function download(name, data, type = "text/plain") {
  if (window.GB_NATIVE) {
    const args = typeof data === "string" ? { name, text: data } : { name, b64: bytesToB64(data) };
    NativeEngine.py("save_file", args).then((p) => p && setStatus("ready", `Saved ${p}`)).catch((e) => alert(e.message));
    return;
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([data], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
const csvCell = (v) => (typeof v === "string" && /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v ?? "");

$("#dl-json").addEventListener("click", () => {
  // with an example loaded, leave out the measured signal (third-party data: see the source link)
  const r = S.example ? { ...S.results, y_val: undefined, data_source: { name: S.example.name, url: S.example.url } } : S.results;
  download("greenbox_results.json", JSON.stringify(r, null, 1), "application/json");
});
$("#dl-csv").addEventListener("click", () => {
  const h = ["model", "kind", "method", "w_E", "model_key", "terms", "NRMSE", "RMSE", "R2", "additions", "multiplications", "complexity",
    "complexity_reduction_pct", "nrmse_change_pct", "time_ns_per_sample", "carbon_mg_per_sample", "pareto"];
  const rows = S.results.models.map((m) => [m.name, m.kind, m.method ?? "", WEIGHTED.includes(m.method) ? m.w_E : "", m.model_key ?? "", m.n_terms, m.metrics.NRMSE, m.metrics.RMSE, m.metrics.R2,
    m.n_add, m.n_mult, m.complexity, m.complexity_reduction_pct ?? "", m.nrmse_change_pct ?? "", m.time_ns_per_sample,
    m.carbon_mg_per_sample, m.pareto]);
  download("greenbox_summary.csv", [h, ...rows].map((r) => r.map(csvCell).join(",")).join("\n"), "text/csv");
});
$("#dl-model").addEventListener("click", () => {
  const m = S.results.models[S.selected];
  const rows = [["term", "coefficient", "std", "err", ...m.codes[0].map((_, j) => `code${j + 1}`)],
    ...m.terms.map((t, i) => [t, m.theta[i], m.std[i], m.err[i], ...m.codes[i]])];
  download(`greenbox_${m.name.replace(/[^\w.]+/g, "_")}.csv`, rows.map((r) => r.map(csvCell).join(",")).join("\n"), "text/csv");
});
$("#copy-eq").addEventListener("click", async () => {
  const mm = S.results.models[S.selected];
  const txt = equationText(mm) + (equationText(mm, "noise") ? "\n" + equationText(mm, "noise") : "");
  try { await navigator.clipboard.writeText(txt); $("#copy-eq").textContent = "Copied ✓"; }
  catch (_) { download("equation.txt", txt); }
  setTimeout(() => ($("#copy-eq").textContent = "Copy equation"), 1500);
});

// Redraw plots in the new palette when the OS theme changes
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (S.dataInfo) drawData(S.dataInfo);
  if (S.results) renderResults();
  if (S.calibration) showCalibration(S.calibration);
});

// ---------------------------------------------------------------- home demo
const DEMO = JSON.parse($("#demo").textContent);
function demoModel(method, w) {
  return DEMO.models.find((m) => m.kind === "green" && m.method === method && (!WEIGHTED.includes(method) || +m.w_E === w));
}
const demoMethod = () => $("#demo-methods .on")?.dataset.method || "weighted";
const demoWeight = () => +(+$("#demo-w").value).toFixed(2);
function drawDemo() {
  const key = demoMethod(), w = demoWeight(), m = demoModel(key, w), ref = demoModel("accurate");
  const weighted = WEIGHTED.includes(key);
  $("#demo-w").disabled = !weighted;
  $("#demo-wrow").classList.toggle("off", !weighted);
  $("#demo-wtext").innerHTML = `${METHODS[key].label} <span>(${METHODS[key].math}` +
    `${weighted ? `, w<sub>E</sub> = ${w}, w<sub>C</sub> = ${+(1 - w).toFixed(2)}` : ""})</span>`;
  const save = 100 * (1 - m.complexity / ref.complexity);
  $("#demo-stats").innerHTML = [
    m === ref ? ["0%", "less computation (this is the most accurate model)", "accent"]
      : save >= 0 ? [`${save.toFixed(0)}%`, "less computation than the most accurate model", "accent"]
      : [`${(m.complexity / ref.complexity).toFixed(1)}×`, "the computation of the most accurate model"],
    [fmt(m.metrics.R2, 3), `validation R² · most accurate ${fmt(ref.metrics.R2, 3)}`],
    [`${m.n_add} + ${m.n_mult}`, `additions + multiplications · most accurate ${ref.n_add} + ${ref.n_mult}`],
    [m.n_terms, `terms · most accurate ${ref.n_terms}`],
  ].map(([v, l, c]) => `<div class="kpi ${c || ""}"><b>${v}</b><span>${l}</span></div>`).join("");
  const f = DEMO.front;
  plot("plot-demo-trade", [
    { x: f.complexity, y: f.error, mode: "lines+markers", name: "Pareto front", line: { shape: "hv", color: css("--muted"), dash: "dot", width: 1.2 },
      marker: { color: css("--muted"), size: 6 }, hoverinfo: "skip" },
    { x: [m.complexity], y: [Math.sqrt(Math.max(1 - m.holdout_r2, 0))], mode: "markers", name: METHODS[key].label,
      marker: { size: 16, color: modelColor(m), line: { color: css("--text"), width: 1.5 } } },
  ], { xaxis: { title: { text: "operations per sample" } }, yaxis: { title: { text: "test error E = √(1 − R²)" } } });
  const x = DEMO.y_val.map((_, i) => i + DEMO.offset);
  plot("plot-demo-sim", [
    { x, y: DEMO.y_val, name: "measured (noisy)", mode: "lines", line: { color: css("--data"), width: 1.3 } },
    { x, y: ref.yhat, name: "most accurate", mode: "lines", line: { color: css("--contrast"), width: 1.4, dash: "dash" } },
    { x, y: m.yhat, name: "Green", mode: "lines", line: { color: modelColor(m), width: 2 } },
  ], { xaxis: { title: { text: "sample k" } }, yaxis: { title: { text: "y(k)" } } });
  $("#demo-eq-title").textContent = `Identified Green model (${m.n_terms} terms)`;
  $("#demo-eq").textContent = equationText(m);
}
function drawTrueSystem() {
  const parts = DEMO.true.map(({ theta, term }) => `${theta < 0 ? " − " : " + "}${Math.abs(theta)} · ${term}`);
  let s = parts.join("\n       ").trimStart();
  s = s.startsWith("+ ") ? s.slice(2) : "−" + s.slice(2);
  $("#demo-true").textContent = `y(k) = ${s}\n       + noise (σ = ${DEMO.noise_std})`;
}
$("#demo-methods").innerHTML = METHOD_KEYS.map((k) =>
  `<button class="btn small${k === "weighted" ? " on" : ""}" data-method="${k}">${METHODS[k].label}</button>`).join("");
$$("#demo-methods button").forEach((b) => b.addEventListener("click", () => {
  $$("#demo-methods button").forEach((x) => x.classList.toggle("on", x === b));
  drawDemo();
}));
$("#demo-w").addEventListener("input", drawDemo);

function drawRho() {
  const B = Array.from({ length: 60 }, (_, i) => 2 ** (2 + i * 10 / 59));
  const lines = [
    ["Schoolbook", (b) => b], ["Karatsuba", (b) => b ** 0.585], ["Toom–Cook 3", (b) => b ** (Math.log(5) / Math.log(3) - 1)],
    ["Schönhage–Strassen", (b) => Math.log2(b) * Math.log2(Math.max(Math.log2(b), 2))], ["Uniform", () => 1],
  ];
  const cols = [css("--contrast"), greenColor(1), greenColor(0.6), "#376996", css("--muted")];
  plot("plot-rho", lines.map(([n, f], i) => ({ x: B, y: B.map(f), name: n, mode: "lines", line: { color: cols[i], width: 2 } })).concat([
    { x: [64, 64], y: [0.8, 5000], mode: "lines", name: "B = 64 (double)", line: { color: css("--muted"), dash: "dot", width: 1 } }]),
  { xaxis: { title: { text: "word length B (bits)" }, type: "log" }, yaxis: { title: { text: "ρ = multiplication / addition cost" }, type: "log" } });
}

// ---------------------------------------------------------------- exports: figures, tables, equations
const slug = (s) => (s || "greenbox").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 60) || "greenbox";
function headingFor(el) {
  for (let n = el; n && n !== document.body; n = n.parentElement) {
    const h = n.querySelector(":scope > h2, :scope > h3, :scope > .row > h2, :scope > .row > h3");
    if (h) return h.textContent.trim();
  }
  return el.id;
}
function exportBar(el, label, items) {
  if (!el || (el.previousElementSibling && el.previousElementSibling.classList.contains("export-bar"))) return;
  const bar = document.createElement("div");
  bar.className = "export-bar";
  bar.innerHTML = `<span>${label}</span>` + items.map(([k, t]) => `<button type="button" class="xb" data-x="${k}">${t}</button>`).join("");
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-x]");
    if (!b) return;
    const fn = items.find(([k]) => k === b.dataset.x)[2];
    Promise.resolve(fn()).catch((err) => alert("Export failed: " + err.message));
  });
  el.parentNode.insertBefore(bar, el);
}

// figures --------------------------------------------------------------------------------
const PDF_CHARS = { "−": "-", "–": "-", "—": "-", "ρ": "rho", "θ": "theta", "ε": "e", "τ": "tau",
  "Σ": "Sum", "₊": "+", "₋": "-", "≤": "<=", "≥": ">=", "★": "*", "→": "->", "′": "'",
  " ": " ", " ": " ", " ": " ", " ": " " };
// the standard PDF fonts only cover Latin-1: map everything else to plain equivalents
const pdfText = (s) => [...s.replace(/[​-‍﻿]/g, "")]
  .map((c) => PDF_CHARS[c] ?? (c >= "₀" && c <= "₉" ? String(c.charCodeAt(0) - 0x2080) : c.charCodeAt(0) > 255 ? "?" : c)).join("");

// Graphics go through svg2pdf; every text is written by jsPDF at the position, size, colour and
// angle computed by the browser (svg2pdf spaces large text badly).
// Every exported figure carries a small credit line (like Our World in Data); the figure on screen is not changed.
const CREDIT = "Generated by GreenBox software";
function creditedFigure(gd) {
  const text = CREDIT;
  const lay = gd.layout || {};
  const margin = { ...(lay.margin || {}) };
  margin.b = (margin.b ?? 40) + 22;
  const note = { text, xref: "paper", yref: "paper", x: 1, y: 0, xanchor: "right", yanchor: "top", yshift: -(margin.b - 15),
    showarrow: false, font: { size: 9, color: "#7a807b" } };
  return { data: gd.data, layout: { ...lay, margin, annotations: [...(lay.annotations || []), note] } };
}

async function plotToPDF(gd, width, height, id) {
  const url = await Plotly.toImage(creditedFigure(gd), { format: "svg", width, height });
  const box = document.createElement("div");
  box.style.cssText = "position:absolute;left:-10000px;top:0";
  box.innerHTML = decodeURIComponent(url.split(",")[1]);
  document.body.appendChild(box);
  try {
    const svg = box.querySelector("svg");
    const root = svg.getScreenCTM().inverse();
    const texts = [];
    svg.querySelectorAll("text").forEach((t) => {
      const cs = getComputedStyle(t);
      if (cs.visibility !== "hidden" && cs.display !== "none") {
        const size = parseFloat(cs.fontSize) || 12, m = root.multiply(t.getScreenCTM());
        const em = (v) => (String(v || "0").endsWith("em") ? parseFloat(v) * size : parseFloat(v) || 0);
        const anchor = t.getAttribute("text-anchor") || cs.textAnchor || "start";
        const x0 = parseFloat(t.getAttribute("x") || 0);
        let y = parseFloat(t.getAttribute("y") || 0) + em(t.getAttribute("dy"));
        // Plotly writes multi-line labels as <tspan class="line"> children, each with its own x and dy
        const lines = [...t.children].filter((c) => c.classList && c.classList.contains("line"));
        const parts = lines.length ? lines.map((l, i) => { if (i) y += em(l.getAttribute("dy") || "1.3em");
          return { str: l.textContent, x: parseFloat(l.getAttribute("x") ?? x0), y }; }) : [{ str: t.textContent, x: x0, y }];
        for (const part of parts) {
          const str = pdfText(part.str || "");
          if (!str.trim()) continue;
          const p = new DOMPoint(part.x, part.y).matrixTransform(m);
          texts.push({ str, x: p.x, y: p.y, size: size * Math.hypot(m.a, m.b), ux: m.a / Math.hypot(m.a, m.b), uy: m.b / Math.hypot(m.a, m.b),
            fill: cs.fill, bold: +cs.fontWeight >= 600, k: anchor === "middle" ? 0.5 : anchor === "end" ? 1 : 0,
            opacity: +(cs.fillOpacity || 1) * +(cs.opacity || 1) });
        }
      }
      t.remove();
    });
    const pdf = new jspdf.jsPDF({ orientation: width >= height ? "landscape" : "portrait", unit: "pt", format: [width, height] });
    await svg2pdf.svg2pdf(svg, pdf, { x: 0, y: 0, width, height });
    for (const t of texts) {
      if (t.opacity < 0.05) continue;
      const rgb = (t.fill.match(/\d+(\.\d+)?/g) || [0, 0, 0]).slice(0, 3).map(Number);
      pdf.setTextColor(rgb[0], rgb[1], rgb[2]);
      pdf.setFont("helvetica", t.bold ? "bold" : "normal");
      pdf.setFontSize(t.size);
      // align along the text direction ourselves: jsPDF shifts rotated text horizontally
      const w = pdf.getTextWidth(t.str) * t.k;
      const angle = (Math.atan2(t.uy, t.ux) * 180) / Math.PI;
      pdf.text(t.str, t.x - w * t.ux, t.y - w * t.uy, { angle: -angle, baseline: "alphabetic" });
    }
    return new Uint8Array(pdf.output("arraybuffer"));
  } finally { box.remove(); }
}

function figureCSV(gd) {
  const cols = [];
  (gd.data || []).forEach((t, i) => {
    const name = (t.name || `trace ${i + 1}`).replace(/<[^>]+>/g, "");
    if (t.x) cols.push([`${name} - x`, t.x]);
    if (t.y) cols.push([`${name} - y`, t.y]);
    if (t.marker && Array.isArray(t.marker.color) && t.marker.color.every((v) => typeof v === "number")) cols.push([`${name} - colour value`, t.marker.color]);
  });
  const n = Math.max(0, ...cols.map(([, v]) => v.length));
  const rows = [cols.map(([h]) => h)];
  for (let r = 0; r < n; r++) rows.push(cols.map(([, v]) => (v[r] === undefined || v[r] === null ? "" : v[r])));
  return rows.map((r) => r.map(csvCell).join(",")).join("\n");
}

function figureExports(id) {
  const gd = document.getElementById(id);
  if (!gd || gd.dataset.empty) return;
  const name = () => slug(headingFor(gd));
  const size = () => [Math.max(gd.clientWidth, 480), Math.max(gd.clientHeight, 300)];
  exportBar(gd, "Figure", [
    ["csv", "Data (CSV)", () => (S.example && MEASURED_PLOTS.includes(id)
      ? window.open(S.example.url, "_blank", "noopener")
      : download(name() + ".csv", figureCSV(gd), "text/csv"))],
    ["pdf", "PDF", async () => { const [w, h] = size(); download(name() + ".pdf", await plotToPDF(gd, w, h, id), "application/pdf"); }],
    ["svg", "SVG", async () => { const [w, h] = size(); const u = await Plotly.toImage(creditedFigure(gd), { format: "svg", width: w, height: h });
      download(name() + ".svg", decodeURIComponent(u.split(",")[1]), "image/svg+xml"); }],
    ["png", "PNG", async () => { const [w, h] = size(); const u = await Plotly.toImage(creditedFigure(gd), { format: "png", width: w, height: h, scale: 3 });
      download(name() + ".png", Uint8Array.from(atob(u.split(",")[1]), (c) => c.charCodeAt(0)), "image/png"); }],
  ]);
}

// tables ---------------------------------------------------------------------------------
// cell text with subscripts kept: plain "w_E" for CSV, "$w_{E}$" for LaTeX
function cellText(c, latex = false) {
  const walk = (n) => [...n.childNodes].map((k) => {
    if (k.nodeType === 3) return latex ? latexText(k.nodeValue) : k.nodeValue;
    if (k.nodeType !== 1) return "";
    const inner = walk(k);
    if (k.tagName === "SUB") return latex ? `$_{${k.textContent}}$` : `_${k.textContent}`;
    if (k.tagName === "SUP") return latex ? `$^{${k.textContent}}$` : `^${k.textContent}`;
    if (k.classList.contains("model-sub")) return ` (${inner.trim()})`;
    if (k.classList.contains("swatch")) return "";
    return inner;
  }).join("");
  return walk(c).replace(/\s*\n\s*/g, " ").replace(/\s+/g, " ").trim();
}
function tableCSV(t) { return [...t.rows].map((r) => [...r.cells].map((c) => csvCell(cellText(c))).join(",")).join("\n"); }

function latexText(s) {
  const map = { "\\": "\\textbackslash{}", "&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#", "_": "\\_", "{": "\\{", "}": "\\}",
    "~": "\\textasciitilde{}", "^": "\\textasciicircum{}", "²": "$^2$", "³": "$^3$", "★": "$\\star$", "ρ": "$\\rho$", "θ": "$\\theta$",
    "ε": "$\\varepsilon$", "Σ": "$\\Sigma$", "Δ": "$\\Delta$", "×": "$\\times$", "−": "$-$", "–": "--", "·": "$\\cdot$", "₊": "$_+$",
    "≥": "$\\geq$", "≤": "$\\leq$", "✓": "$\\checkmark$", "₂": "$_2$", "₀": "$_0$",
    "µ": "$\\mu$", "Ω": "$\\Omega$" };
  return [...s].map((c) => map[c] ?? c).join("");
}
function termLatex(t) {
  if (t.trim() === "1") return "1";
  return t.split(" * ").map((f) => f.replace(/\^(\d+)$/, "^{$1}")).join("\\,");
}
function numLatex(v) {
  const s = (+v).toPrecision(6);
  const m = s.match(/^(-?[\d.]+)e([+-]\d+)$/);
  return m ? `${m[1].replace(/\.?0+$/, "")}\\times 10^{${+m[2]}}` : s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}
function tableLatex(t, caption) {
  const rows = [...t.rows].map((r) => [...r.cells].map((c) => (c.classList.contains("term") && cellText(c) !== "Σ ERR"
    ? `$${termLatex(cellText(c))}$` : cellText(c, true))));
  const ncol = Math.max(...rows.map((r) => r.length));
  const align = [...(t.rows[1] || t.rows[0]).cells].map((c, i) => (i === 0 || c.classList.contains("term") ? "l" : "r")).join("");
  const line = (r) => "    " + [...r, ...Array(ncol - r.length).fill("")].join(" & ") + " \\\\";
  return ["% requires \\usepackage{booktabs}", "\\begin{table}[htbp]", "  \\centering", `  \\caption{${latexText(caption)}}`,
    `  \\label{tab:${slug(caption)}}`, `  \\begin{tabular}{${align}}`, "    \\toprule", line(rows[0]), "    \\midrule",
    ...rows.slice(1).map(line), "    \\bottomrule", "  \\end{tabular}", "\\end{table}", ""].join("\n");
}
function tableExports(id, caption) {
  const t = document.getElementById(id);
  if (!t || !t.rows.length) return;
  const anchor = t.closest(".table-wrap") || t;
  const cap = typeof caption === "function" ? caption : () => caption;
  exportBar(anchor, "Table", [
    ["csv", "CSV", () => download(slug(cap()) + ".csv", tableCSV(t), "text/csv")],
    ["tex", "LaTeX", () => download(slug(cap()) + ".tex", tableLatex(t, cap()), "application/x-tex")],
  ]);
}

// equations ------------------------------------------------------------------------------
function equationLatex(terms, label, lhs = "y(k)", perLine = 3) {
  if (!terms.length) return "";
  const parts = terms.map(({ theta, term }, i) => {
    const sign = theta < 0 ? "-" : i === 0 ? "" : "+";
    const body = term === "1" ? numLatex(Math.abs(theta)) : `${numLatex(Math.abs(theta))}\\,${termLatex(term)}`;
    return `${sign} ${body}`.trim();
  });
  const lines = [];
  for (let i = 0; i < parts.length; i += perLine) lines.push(parts.slice(i, i + perLine).join(" "));
  return ["% requires \\usepackage{amsmath}", "\\begin{align}", `  ${lhs} ={}& ${lines[0]}` + (lines.length > 1 ? " \\nonumber\\\\" : ""),
    ...lines.slice(1).map((l, i) => `  &${l}` + (i < lines.length - 2 ? " \\nonumber\\\\" : "")),
    `  \\label{eq:${slug(label)}}`, "\\end{align}", ""].join("\n");
}
const modelTerms = (m, which) => m.terms.map((term, i) => ({ term, theta: m.theta[i], noise: isNoise(m, i) }))
  .filter((t) => (which === "noise") === t.noise);

function equationExports(id, getText, getLatex, name) {
  const el = document.getElementById(id);
  if (!el) return;
  exportBar(el, "Equation", [
    ["txt", "TXT", () => download(slug(name()) + ".txt", getText() + "\n", "text/plain")],
    ["tex", "LaTeX", () => download(slug(name()) + ".tex", getLatex(), "application/x-tex")],
    ["copy", "Copy LaTeX", async () => {
      try { await navigator.clipboard.writeText(getLatex()); setStatus("ready", "LaTeX copied to the clipboard"); }
      catch (_) { download(slug(name()) + ".tex", getLatex(), "application/x-tex"); } // clipboard blocked: save instead
    }],
  ]);
}

function ensureExports() {
  ["plot-data", "plot-cal", "plot-val", "plot-trade", "plot-sweep", "plot-resid", "plot-size",
    "plot-demo-trade", "plot-demo-sim", "plot-rho"].forEach((id) => { const g = document.getElementById(id); if (g && g.data) figureExports(id); });
  refreshDataExports();
  if (S.results) {
    tableExports("summary", "Summary of the identified models");
    tableExports("model-table", () => `Terms of the ${S.results.models[S.selected].name}`);
    if (S.results.search) tableExports("search-table", "Structure search");
    const cur = () => S.results.models[S.selected];
    const nm = () => (cur().kind === "green" ? `green_model_${cur().method}` : slug(cur().name));
    equationExports("equation", () => equationText(cur()), () => equationLatex(modelTerms(cur(), "process"), nm()), nm);
    equationExports("noise-equation", () => equationText(cur(), "noise"),
      () => equationLatex(modelTerms(cur(), "noise"), nm() + "_noise", "e\\text{-terms}"), () => nm() + "_noise_model");
  }
  equationExports("demo-eq", () => $("#demo-eq").textContent,
    () => equationLatex(modelTerms(demoModel(demoMethod()), "process"), "home_green_model"), () => "home_green_model");
  equationExports("demo-true", () => $("#demo-true").textContent,
    () => equationLatex(DEMO.true.map(({ theta, term }) => ({ theta, term })), "home_true_system"), () => "home_true_system");
}

// ---------------------------------------------------------------- desktop menu hooks
window.GB = {
  goto,
  learn: (page) => { learnReturn = $(".step.active").id.replace("step-", ""); goto(page); if (page === "learn-complexity") drawRho(); },
  async openData() {
    if (!S.ready) return;
    const fileId = "f" + S.nextFile++;
    setStatus("busy", "Reading file…");
    try {
      const desc = await NativeEngine.py("open_dialog", { fileId });
      if (!desc) { setStatus("ready", "Ready"); return; }
      setExample(null);
      S.files[fileId] = desc;
      refreshSelects();
      if (Object.keys(S.files).length === 1 || !$("#sel-y").value) guessColumns(fileId);
      setStatus("ready", "Ready");
      goto("data");
      updatePreview();
    } catch (e) { setStatus("error", "Could not read the file"); alert(e.message); }
  },
  loadExample(key) { const s = $("#tb-example"); s.value = key; s.dispatchEvent(new Event("change")); },
  run() { document.querySelector("[data-tb=run]").click(); },
  calibrate() { document.querySelector("[data-tb=calibrate]").click(); },
  save(kind) {
    if (!S.results) { alert("Run an identification first: there are no results to save yet."); return; }
    $({ json: "#dl-json", csv: "#dl-csv", model: "#dl-model", equation: "#copy-eq" }[kind]).click();
  },
};

// ---------------------------------------------------------------- desktop toolbar
document.body.classList.add("native");  // same program look in the browser and in the desktop app
if (window.GB_NATIVE) {
  $$("button").forEach((b) => { if (/^Download /.test(b.textContent)) b.textContent = b.textContent.replace(/^Download /, "Save "); });
}
$$("[data-tb]").forEach((b) => b.addEventListener("click", () => {
  const k = b.dataset.tb;
  if (k === "open") { goto("data"); window.GB_NATIVE ? GB.openData() : $("#file-input").click(); }
  if (k === "run") { goto("identify"); if (!$("#run-btn").disabled) $("#run-btn").click(); }
  if (k === "calibrate") {
    goto("complexity");
    const card = $("#calib-card");
    setTimeout(() => { card.scrollIntoView({ behavior: "smooth", block: "start" }); card.classList.add("flash");
      setTimeout(() => card.classList.remove("flash"), 1600); }, 60);
  }
  if (k === "save") GB.save("json");
}));
$("#tb-example").addEventListener("change", (e) => {
  const key = e.target.value;
  e.target.value = "";
  if (!key) return;
  goto("data");
  document.querySelector(`[data-example=${key}]`).click();
});

// ---------------------------------------------------------------- boot
(async function boot() {
  try {
    const stored = localStorage.getItem("greenbox.calibration");
    if (stored) S.calibration = JSON.parse(stored);
  } catch (_) { /* storage unavailable */ }
  updateRunState();
  drawTrueSystem();
  drawDemo();
  try {
    await Engine.start();
    setStatus("loading", window.GB_NATIVE ? "Starting…" : "Loading Python and NumPy (first time can take ~10 s)…");
    const sources = JSON.parse($("#py-sources").textContent);
    S.info = await Engine.call("init", { sources, assets: window.GB_ASSETS || null });
    window.GB_ASSETS = null; // free the embedded copy once the engine has it
    S.ready = true;
    setStatus("ready", "Ready");
    renderFamilies();
    selectFamily(S.family, true);
    if (S.calibration && !S.calibration.valid) S.calibration = null;
    if (S.calibration) S.cost = "calibrated";
    renderCostCards();
    showCalibration(S.calibration);
    $("#cal-run").disabled = false;
    updateRunState();
    // the default cost is the calibration of this computer: calibrate once on first start
    if (!S.calibration) $("#cal-run").click();
  } catch (e) {
    console.error(e);
    setStatus("error", window.GB_ASSETS === undefined ? "Could not start Python. This version needs an internet connection (or use the offline folder)." : "Could not start Python.");
  }
})();
