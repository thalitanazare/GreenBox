// GreenBox compute engine: Pyodide (Python in WebAssembly) + JS microbenchmark.
// Runs inside a Web Worker; falls back to the main thread when workers are
// unavailable (the page then calls self.GreenBoxEngine.handle directly).

const PYODIDE_URL = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";

self.GreenBoxEngine = (() => {
  let pyodide = null;
  let api = null;

  // Offline build: the Pyodide files arrive as strings (base64 for binaries) and are served
  // to Pyodide through fetch/importScripts, so nothing is downloaded and no file:// access is needed.
  function installOffline(assets) {
    const files = {};
    for (const [name, v] of Object.entries(assets)) {
      if (v.b64 !== undefined) {
        const bin = atob(v.b64), bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        files[name] = bytes;
      } else files[name] = new TextEncoder().encode(v.text);
    }
    const nameOf = (u) => { const n = String(u).split("?")[0].split("/").pop(); return files[n] ? n : null; };
    const realFetch = self.fetch.bind(self);
    self.fetch = async (u, opts) => {
      const n = nameOf(u && u.url ? u.url : u);
      if (!n) return realFetch(u, opts);
      const type = n.endsWith(".wasm") ? "application/wasm" : n.endsWith(".json") ? "application/json" : "application/octet-stream";
      return new Response(files[n], { status: 200, headers: { "Content-Type": type } });
    };
    // Evaluate bundled scripts from their text: Safari refuses blob: scripts in workers of file:// pages.
    const run = (n) => (0, eval)(new TextDecoder().decode(files[n]) + "\n//# sourceURL=" + n);
    if (typeof self.importScripts === "function") {
      const realImport = self.importScripts.bind(self);
      self.importScripts = (...urls) => urls.forEach((u) => (nameOf(u) ? run(nameOf(u)) : realImport(u)));
    }
    return { indexURL: "https://greenbox.offline/", loadPyodideScript: () => run("pyodide.js") };
  }

  async function init(sources, assets) {
    let indexURL = PYODIDE_URL;
    if (assets) {
      const off = installOffline(assets);
      indexURL = off.indexURL;
      if (typeof loadPyodide === "undefined") off.loadPyodideScript();
    } else if (typeof loadPyodide === "undefined") {
      self.importScripts(PYODIDE_URL + "pyodide.js");
    }
    self.GreenBoxOffline = !!assets;
    pyodide = await loadPyodide({ indexURL });
    await pyodide.loadPackage(["numpy"]);
    for (const [path, src] of Object.entries(sources)) {
      const full = "/home/pyodide/" + path;
      pyodide.FS.mkdirTree(full.substring(0, full.lastIndexOf("/")));
      pyodide.FS.writeFile(full, src);
    }
    api = pyodide.pyimport("greenbox.webapi");
    return api.info();
  }

  // ---- JavaScript microbenchmark (same structures as greenbox/calibration.py)
  function rng(seed) {
    let s = seed >>> 0 || 1;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  function structures(maxTerms, extras) {
    const r = rng(7);
    const out = [];
    for (let a = 1; a <= maxTerms; a++) {
      for (const extra of extras) {
        const degs = new Array(a).fill(1);
        for (let i = 0; i < extra; i++) degs[Math.floor(r() * a)] += 1;
        out.push({ a, b: degs.reduce((p, q) => p + q, 0), degs });
      }
    }
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(r() * (i + 1));
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  }

  // Minimal WebAssembly module, written byte by byte: pure f64 add / mul timing.
  // lat_add(n, x, k): 16 dependent x = x + k per loop pass, n passes  -> latency of one add
  // lat_mul(n, x, k): same with *                                     -> latency of one mul
  // thr_add / thr_mul: 8 independent chains, 2 ops each per pass       -> throughput
  function arithModule() {
    const uleb = (v) => { const o = []; do { let b = v & 0x7f; v >>>= 7; if (v) b |= 0x80; o.push(b); } while (v); return o; };
    const str = (s) => [...uleb(s.length), ...[...s].map((c) => c.charCodeAt(0))];
    const vec = (items) => [...uleb(items.length), ...items.flat()];
    const section = (id, body) => [id, ...uleb(body.length), ...body];
    const GET = 0x20, SET = 0x21, TEE = 0x22, ADD = 0xa0, MUL = 0xa2;
    const loopEnd = [GET, 0, 0x41, 1, 0x6b, TEE, 0, 0x0d, 0, 0x0b]; // n -= 1; continue while n != 0
    const lat = (op) => {
      const body = [0x03, 0x40];
      for (let i = 0; i < 16; i++) body.push(GET, 1, GET, 2, op, SET, 1);
      return [0x00, ...body, ...loopEnd, GET, 1, 0x0b]; // no extra locals
    };
    const thr = (op) => {
      const body = [];
      for (let i = 3; i < 11; i++) body.push(GET, 1, SET, i);
      body.push(0x03, 0x40);
      for (let r = 0; r < 2; r++) for (let i = 3; i < 11; i++) body.push(GET, i, GET, 2, op, SET, i);
      body.push(...loopEnd, GET, 3);
      for (let i = 4; i < 11; i++) body.push(GET, i, ADD);
      return [0x01, 0x08, 0x7c, ...body, 0x0b]; // 8 extra f64 locals
    };
    const funcs = [lat(ADD), lat(MUL), thr(ADD), thr(MUL)];
    const bytes = [0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00,
      ...section(1, vec([[0x60, ...vec([[0x7f], [0x7c], [0x7c]]), ...vec([[0x7c]])]])),
      ...section(3, vec(funcs.map(() => [0]))),
      ...section(7, vec(["lat_add", "lat_mul", "thr_add", "thr_mul"].map((n, i) => [...str(n), 0x00, i]))),
      ...section(10, vec(funcs.map((f) => [...uleb(f.length), ...f])))];
    return new WebAssembly.Instance(new WebAssembly.Module(new Uint8Array(bytes))).exports;
  }

  function measureArithmetic(targetMs = 120, repeats = 7, progress = () => {}) {
    const m = arithModule();
    const cases = [["lat_add", 1.0, 1e-9, 16], ["lat_mul", 1.0, 1.0000000001, 16],
                   ["thr_add", 1.0, 1e-9, 16], ["thr_mul", 1.0, 1.0000000001, 16]];
    const out = {}; let sink = 0, step = 0;
    for (const [name, x, k, perPass] of cases) {
      const f = m[name];
      let n = 1 << 16;
      for (let w = 0; w < 5; w++) sink += f(n, x, k);              // warm up / tier up
      for (;;) { const t0 = performance.now(); sink += f(n, x, k); if (performance.now() - t0 > targetMs / 4) break; n *= 2; }
      n = Math.round(n * 4 * 1.2);
      const per = [];
      for (let r = 0; r < repeats; r++) {
        const t0 = performance.now(); sink += f(n, x, k); const dt = performance.now() - t0;
        per.push((dt * 1e6) / (n * perPass));
        progress(`${name} ${r + 1}/${repeats}`, (++step) / (cases.length * repeats));
      }
      per.sort((p, q) => p - q);
      out[name] = { min: per[0], median: per[Math.floor(repeats / 2)] };
    }
    out.sink = sink;
    return out;
  }

  // coefficients are written as constants, as the GreenBox simulator does
  function compile(degs, seed, c) {
    const r = rng(seed + 11);
    const terms = degs.map((d, i) => {
      const f = [];
      for (let k = 0; k < d; k++) f.push("x" + Math.floor(r() * 4));
      return `${c[i + 1]}*${f.join("*")}`;
    });
    const body =
      "let s = 0;\n" +
      "for (let k = 0; k < n; k++) {\n" +
      "  const x0 = X0[k], x1 = X1[k], x2 = X2[k], x3 = X3[k];\n" +
      `  s += ${c[0]} + ${terms.join(" + ")};\n` +
      "}\nreturn s;";
    return new Function("X0", "X1", "X2", "X3", "n", body);
  }

  function calibrateJS(minMs, progress) {
    const n = 4096;
    const r = rng(3);
    const X = [0, 1, 2, 3].map(() => Float64Array.from({ length: n }, () => 0.5 + r()));
    const sts = structures(10, [0, 2, 5, 9]);
    const points = [];
    let sink = 0;
    sts.forEach((st, idx) => {
      const c = Array.from({ length: st.a + 1 }, () => +(r() * 2 - 1).toFixed(6));
      const f = compile(st.degs, idx, c);
      for (let w = 0; w < 30; w++) sink += f(X[0], X[1], X[2], X[3], n); // JIT warm-up
      const reps = [];
      for (let rep = 0; rep < 5; rep++) {
        let loops = 0;
        const t0 = performance.now();
        let el = 0;
        do {
          sink += f(X[0], X[1], X[2], X[3], n);
          loops++;
          el = performance.now() - t0;
        } while (el < minMs);
        reps.push((el * 1e6) / (loops * n));
      }
      reps.sort((p, q) => p - q);
      points.push([st.a, st.b, reps[2]]);
      progress(`Timing structure ${idx + 1}/${sts.length}`, (idx + 1) / sts.length);
    });
    if (sink === 42) console.log(sink); // keep results alive
    return points;
  }

  async function handle(msg, post) {
    const { id, cmd, args } = msg;
    const progress = (m, f) => post({ type: "progress", id, msg: m, frac: f });
    try {
      let out;
      switch (cmd) {
        case "init":
          out = await init(args.sources, args.assets);
          break;
        case "load_file":
          if (args.name.toLowerCase().endsWith(".mat")) {
            progress("Loading SciPy to read .mat files…", 0.3);
            try { await pyodide.loadPackage(["scipy"]); }
            catch (e) { throw new Error("Reading .mat files needs SciPy, which is downloaded from the internet (not included in the offline version). Export the data to CSV, or connect to the internet."); }
          }
          out = api.load_file(args.fileId, args.name, args.bytes);
          break;
        case "preview":
          out = api.preview(JSON.stringify(args.spec));
          break;
        case "linearity":
          out = api.linearity(JSON.stringify(args.spec), progress);
          break;
        case "candidates":
          out = api.candidates(JSON.stringify(args.cfg));
          break;
        case "identify":
          out = api.identify(JSON.stringify(args.spec), JSON.stringify(args.cfg), progress);
          break;
        case "calibrate": {
          if (args.engine === "arith") {
            const r = measureArithmetic(Math.max(40, args.seconds * 2000), 7, progress);
            const spread = Math.max(...["lat_add", "lat_mul", "thr_add", "thr_mul"].map((k) => r[k].median / r[k].min - 1));
            const lat = { add: r.lat_add.min, mult: r.lat_mul.min }, thr = { add: r.thr_add.min, mult: r.thr_mul.min };
            out = JSON.stringify({
              kind: "arithmetic", engine: "Processor arithmetic (WebAssembly, 64-bit float)", use: "latency",
              latency: lat, throughput: thr, spread, t0_ns: 0, t_add_ns: lat.add, t_mult_ns: lat.mult,
              rho: lat.mult / lat.add, r2: null, valid: lat.add > 0 && lat.mult > 0, points: [],
            });
          } else if (args.engine === "python") {
            out = api.calibrate_python(args.seconds, progress);
          } else {
            const pts = calibrateJS(args.seconds * 1000, progress);
            out = api.fit_calibration(JSON.stringify(pts), "JavaScript (JIT)");
          }
          break;
        }
        default:
          throw new Error("unknown command " + cmd);
      }
      post({ type: "result", id, ok: true, result: JSON.parse(out) });
    } catch (e) {
      let text = String((e && e.message) || e);
      const m = text.match(/(\w+Error: .*)\s*$/);
      if (m) text = m[1];
      post({ type: "result", id, ok: false, error: text });
    }
  }

  return { handle, measureArithmetic, calibrateJS };
})();

if (typeof WorkerGlobalScope !== "undefined" && self instanceof WorkerGlobalScope) {
  self.onmessage = (e) => self.GreenBoxEngine.handle(e.data, (m) => self.postMessage(m));
}
