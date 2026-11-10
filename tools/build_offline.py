"""Build the offline folder: dist/GreenBox-offline/ (+ a .zip of it).

    python tools/build_offline.py

The folder runs without internet by double-clicking GreenBox.html. Pyodide
(Python + NumPy) and Plotly are bundled; the Pyodide files are stored inside
a plain <script> (lib/pyodide-assets.js) because browsers refuse to fetch
WebAssembly from file:// pages. SciPy (47 MB, only needed for .mat files) is
not bundled. Downloads are cached in tools/.cache/.
"""

from __future__ import annotations

import base64
import json
import shutil
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_web  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "tools" / ".cache"
OUT = ROOT / "dist" / "GreenBox-offline"
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/"
PLOTLY = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
TEXT = ["pyodide.js", "pyodide.asm.js"]
BINARY = ["pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json",
          "numpy-1.26.4-cp312-cp312-pyodide_2024_0_wasm32.whl"]

README = """GreenBox - offline version
==========================

Open GreenBox.html with a double click (Chrome, Edge or Firefox recommended).
No internet connection is needed. Keep the "lib" folder next to GreenBox.html.

The first start takes ~10-20 s while Python is unpacked in the browser.
.mat files need an internet connection (SciPy is not bundled);
CSV / TXT / DAT files work offline.
"""


def cached(url: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[1]
    if not path.exists():
        print("downloading", url)
        urllib.request.urlretrieve(url, path)
    return path


def build() -> Path:
    html_path = build_web.build()
    html = html_path.read_text(encoding="utf-8")
    cdn_tag = f'<script src="{PLOTLY}"></script>'
    if cdn_tag not in html:
        raise SystemExit("Plotly CDN tag not found in the page")
    html = html.replace(cdn_tag, '<script src="lib/plotly.min.js"></script>\n'
                                 '<script src="lib/pyodide-assets.js"></script>')

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "lib").mkdir(parents=True)
    (OUT / "GreenBox.html").write_text(html, encoding="utf-8")
    shutil.copy(cached(PLOTLY), OUT / "lib" / "plotly.min.js")

    assets = {}
    for name in TEXT:
        assets[name] = {"text": cached(PYODIDE + name).read_text(encoding="utf-8")}
    for name in BINARY:
        assets[name] = {"b64": base64.b64encode(cached(PYODIDE + name).read_bytes()).decode("ascii")}
    js = "self.GB_ASSETS = " + json.dumps(assets).replace("</", "<\\/") + ";\n"
    (OUT / "lib" / "pyodide-assets.js").write_text(js, encoding="utf-8")
    (OUT / "LEIA-ME.txt").write_text(README, encoding="utf-8")

    zip_path = shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    return Path(zip_path)


if __name__ == "__main__":
    z = build()
    size = sum(f.stat().st_size for f in OUT.rglob("*")) / 1e6
    print(f"wrote {OUT.relative_to(ROOT)} ({size:.0f} MB) and {z.relative_to(ROOT)} ({z.stat().st_size / 1e6:.0f} MB)")
