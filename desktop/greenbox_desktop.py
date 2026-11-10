"""GreenBox desktop application.

The same interface as the browser version, shown in a native window (pywebview),
with all calculations in native Python (NumPy/SciPy) and native File menus and dialogs.
Run from source:  python desktop/greenbox_desktop.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from pathlib import Path

import webview
from webview.menu import Menu, MenuAction, MenuSeparator

if getattr(sys, "frozen", False):                      # inside the PyInstaller bundle
    RESOURCES = Path(sys._MEIPASS)
else:                                                  # running from the repository
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    RESOURCES = Path(__file__).resolve().parent / "build"

from greenbox import __version__, webapi  # noqa: E402

APP_NAME = "GreenBox"
ARITH_EXE = "gb_arith.exe" if os.name == "nt" else "gb_arith"  # compiled by build.py
DATA_TYPES = ("Data files (*.csv;*.txt;*.dat;*.tsv;*.mat)", "All files (*.*)")
EXAMPLES = [("dc_motor", "DC motor"), ("silverbox", "Silverbox"),
            ("emps", "EMPS (positioning system)"), ("lanl_three_story", "Three-story structure (LANL)"), ("wiener_hammerstein", "Wiener–Hammerstein")]


def storage_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    path = base / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


class Api:
    """Methods called from JavaScript as window.pywebview.api.<name>(...)."""

    def __init__(self):
        self._window = None

    # -- bridge ---------------------------------------------------------------------------
    def call(self, call_id, cmd, args_json):
        try:
            args = json.loads(args_json)
            return json.dumps({"ok": True, "result": self._dispatch(call_id, cmd, args)})
        except Exception as exc:  # reported in the interface
            return json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def _progress(self, call_id):
        def report(msg, frac):
            self._window.evaluate_js(f"window.__gbProgress({int(call_id)}, {json.dumps(str(msg))}, {float(frac)})")
        return report

    def _dispatch(self, call_id, cmd, a):
        prog = self._progress(call_id)
        if cmd == "init":
            return json.loads(webapi.info())
        if cmd == "load_file":
            return json.loads(webapi.load_file(a["fileId"], a["name"], base64.b64decode(a["b64"])))
        if cmd == "preview":
            return json.loads(webapi.preview(json.dumps(a["spec"])))
        if cmd == "candidates":
            return json.loads(webapi.candidates(json.dumps(a["cfg"])))
        if cmd == "linearity":
            return json.loads(webapi.linearity(json.dumps(a["spec"]), prog))
        if cmd == "identify":
            return json.loads(webapi.identify(json.dumps(a["spec"]), json.dumps(a["cfg"]), prog))
        if cmd == "calibrate":  # model evaluation in Python, here native CPython
            res = json.loads(webapi.calibrate_python(a["seconds"], prog))
            res["engine"] = f"Model evaluation in Python {sys.version.split()[0]} (native)"
            return res
        if cmd == "calibrate_native":  # processor arithmetic in native machine code, bundled at build time
            exe = RESOURCES / ARITH_EXE
            if not exe.exists():
                return {"available": False}
            prog("Measuring one addition and one multiplication in native code…", 0.1)
            from greenbox.calibration import run_pure
            return run_pure(exe, "Processor arithmetic (native machine code, C -O2, 64-bit float)")
        if cmd == "fit_calibration":
            return json.loads(webapi.fit_calibration(json.dumps(a["points"]), a["engine"]))
        if cmd == "save_file":
            data = base64.b64decode(a["b64"]) if "b64" in a else a["text"].encode("utf-8")
            return self._save(a["name"], data)
        if cmd == "open_dialog":
            return self._open(a["fileId"])
        raise ValueError(f"unknown command {cmd}")

    # -- native dialogs -------------------------------------------------------------------
    SAVE_TYPES = {".json": "JSON files (*.json)", ".csv": "CSV files (*.csv)", ".pdf": "PDF documents (*.pdf)",
                  ".svg": "SVG images (*.svg)", ".png": "PNG images (*.png)", ".tex": "LaTeX files (*.tex)",
                  ".txt": "Text files (*.txt)"}

    def _save(self, name, data: bytes):
        kind = self.SAVE_TYPES.get(Path(name).suffix.lower(), "All files (*.*)")
        path = self._window.create_file_dialog(webview.FileDialog.SAVE, save_filename=name, file_types=(kind,))
        if not path:
            return None
        path = path[0] if isinstance(path, (list, tuple)) else path
        Path(path).write_bytes(data)
        return str(path)

    def _open(self, file_id):
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False, file_types=DATA_TYPES)
        if not paths:
            return None
        path = Path(paths[0])
        return json.loads(webapi.load_file(file_id, path.name, path.read_bytes()))


def build_menu(window):
    js = window.evaluate_js
    return [
        Menu("File", [
            MenuAction("Open data file…", lambda: js("GB.openData()")),
            Menu("Load example", [MenuAction(label, (lambda k=key: js(f"GB.loadExample('{k}')"))) for key, label in EXAMPLES]),
            MenuSeparator(),
            MenuAction("Save results (JSON)…", lambda: js("GB.save('json')")),
            MenuAction("Save summary (CSV)…", lambda: js("GB.save('csv')")),
            MenuAction("Save selected model (CSV)…", lambda: js("GB.save('model')")),
        ]),
        Menu("Model", [
            MenuAction("Run identification", lambda: js("GB.run()")),
            MenuAction("Calibrate this computer…", lambda: js("GB.calibrate()")),
        ]),
        Menu("View", [
            MenuAction("Home", lambda: js("GB.goto('home')")),
            MenuAction("1 · Data", lambda: js("GB.goto('data')")),
            MenuAction("2 · Model structure", lambda: js("GB.goto('structure')")),
            MenuAction("3 · Complexity", lambda: js("GB.goto('complexity')")),
            MenuAction("4 · Identification", lambda: js("GB.goto('identify')")),
            MenuAction("5 · Results", lambda: js("GB.goto('results')")),
        ]),
        Menu("Help", [
            MenuAction("About the model families", lambda: js("GB.learn('learn-models')")),
            MenuAction("About the complexity criteria", lambda: js("GB.learn('learn-complexity')")),
            MenuSeparator(),
            MenuAction("About GreenBox", lambda: js(
                f"alert('GreenBox {__version__} – polynomial system identification with a low-complexity (Green) criterion.\\n\\n"
                "Based on Nazaré, Mahmoodi & Nepomuceno, OCEANS 2023.\\nCalculations run locally on this computer.')")),
        ]),
    ]


def self_test(window, out_path):
    """GREENBOX_SELFTEST=<file>: run two examples and a calibration, write a report, quit."""
    js = window.evaluate_js
    report = {}
    try:
        for _ in range(600):
            if js("typeof S !== 'undefined' && S.ready"):
                break
            time.sleep(0.1)
        report["ready"] = bool(js("S.ready"))
        for key in ("emps", "silverbox"):
            js(f"S.results = null; document.querySelector('#data-summary').textContent = ''; "
               f"document.querySelector('[data-example={key}]').click()")
            for _ in range(300):
                if "validation samples" in (js("document.querySelector('#data-summary').textContent") or ""):
                    break
                time.sleep(0.1)
            js("document.querySelector('#run-btn').click()")
            for _ in range(1200):
                if js("S.results !== null"):
                    break
                time.sleep(0.1)
            report[key] = {"run": js("document.querySelector('#run-msg').textContent"),
                           "models": json.loads(js("JSON.stringify(S.results.models.map((m) => "
                                                   "[m.name, m.n_terms, m.metrics.R2]))")),
                           "warnings": json.loads(js("JSON.stringify(S.results.warnings)")),
                           "source": js("document.querySelector('#step-results .data-source').textContent")}
        report["example_structures_rows"] = js("document.querySelectorAll('#example-structures-table tbody tr').length")
        js("document.querySelector('[data-step=complexity]').click(); document.querySelector('#cal-result').innerHTML = ''; "
           "document.querySelector('#cal-run').click()")
        for _ in range(600):
            txt = js("document.querySelector('#cal-result').textContent") or ""
            if "Latency" in txt:
                break
            time.sleep(0.1)
        report["calibration"] = txt
        report["calibration_engine"] = js("S.calibration && S.calibration.engine")
    except Exception as exc:
        report["error"] = repr(exc)
    Path(out_path).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    window.destroy()


def screenshots(window, out_dir):
    """GREENBOX_SCREENSHOTS=<folder>: load an example, visit every step, save one PNG per step, quit
    (documentation figures; needs Pillow)."""
    from PIL import ImageGrab

    js = window.evaluate_js
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def wait(cond, n=600):
        for _ in range(n):
            if js(cond):
                return True
            time.sleep(0.1)
        return False

    def shot(name):
        time.sleep(1.2)  # let plots finish drawing
        x, y, w, h = window.x, window.y, window.width, window.height
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(out / f"{name}.png")

    try:
        window.on_top = True
        wait("typeof S !== 'undefined' && S.ready")
        shot("01_home")
        js("document.querySelector('[data-example=dc_motor]').click()")
        wait("(document.querySelector('#data-summary').textContent || '').includes('validation samples')")
        js("document.querySelector('[data-step=data]').click()")
        shot("02_data")
        js("document.querySelector('[data-step=structure]').click()")
        shot("03_structure")
        js("document.querySelector('[data-step=complexity]').click()")
        shot("04_complexity")
        js("document.querySelector('[data-step=identify]').click()")
        shot("05_identify")
        js("document.querySelector('#run-btn').click()")
        wait("S.results !== null", 1200)
        js("document.querySelector('[data-step=results]').click()")
        shot("06_results")
        js("window.scrollTo(0, document.body.scrollHeight / 3)")
        shot("07_results_models")
    finally:
        window.destroy()


def main():
    api = Api()
    window = webview.create_window(
        f"{APP_NAME} {__version__}", url=str(RESOURCES / "GreenBox.html"), js_api=api,
        width=1360, height=900, min_size=(900, 600), background_color="#EEF3EC", text_select=True)
    api._window = window
    test_out = os.environ.get("GREENBOX_SELFTEST")
    shots_out = os.environ.get("GREENBOX_SCREENSHOTS")
    task = (self_test, test_out) if test_out else (screenshots, shots_out) if shots_out else None
    webview.start(
        (lambda: threading.Thread(target=task[0], args=(window, task[1]), daemon=True).start()) if task else None,
        menu=build_menu(window), private_mode=False, storage_path=str(storage_dir()), http_server=True)


if __name__ == "__main__":
    main()
