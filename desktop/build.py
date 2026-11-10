"""Build the GreenBox desktop application with PyInstaller.

    python desktop/build.py

macOS -> desktop/dist/GreenBox.app (+ GreenBox-macOS.zip)
Windows / Linux -> desktop/dist/GreenBox/ (+ zip); run this script on each system.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD, DIST = HERE / "build", HERE / "dist"
LIBS = {  # bundled so the application works offline
    "plotly.min.js": "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js",
    "jspdf.umd.min.js": "https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js",
    "svg2pdf.umd.min.js": "https://cdn.jsdelivr.net/npm/svg2pdf.js@2.2.3/dist/svg2pdf.umd.min.js",
}

sys.path.insert(0, str(ROOT / "tools"))
import build_web  # noqa: E402


def fetch(url, dest):
    """Download with certifi's certificates when available, falling back to curl."""
    try:
        import ssl

        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(url, context=ctx) as r:
            dest.write_bytes(r.read())
    except Exception:
        subprocess.run(["curl", "-sSfL", url, "-o", str(dest)], check=True)


def interface():
    """The browser page, switched to native mode and with its JavaScript libraries bundled."""
    BUILD.mkdir(exist_ok=True)
    html = build_web.build(BUILD / "GreenBox.html").read_text(encoding="utf-8")  # leaves dist/ untouched
    cache = ROOT / "tools" / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    for name, url in LIBS.items():
        tag = f'<script src="{url}"></script>'
        assert tag in html, f"{name} tag not found"
        html = html.replace(tag, f'<script src="{name}"></script>')
        if not (cache / name).exists():
            fetch(url, cache / name)
        shutil.copy(cache / name, BUILD / name)
    html = html.replace('<script src="svg2pdf.umd.min.js"></script>',
                        '<script src="svg2pdf.umd.min.js"></script>\n<script>window.GB_NATIVE = true;</script>')
    (BUILD / "GreenBox.html").write_text(html, encoding="utf-8", newline="\n")


def arithmetic_benchmark():
    """Compile the processor-arithmetic calibration into native code for the bundle, so the
    application measures its own machine code offline. None when no C compiler is found
    (the application then falls back to the WebAssembly measurement of its window)."""
    from greenbox.calibration import compile_pure

    exe = BUILD / ("gb_arith.exe" if platform.system() == "Windows" else "gb_arith")
    for compiler in ("cc", "clang", "gcc"):
        if shutil.which(compiler):
            try:
                return compile_pure(exe, compiler)
            except RuntimeError as exc:
                print(f"warning: {exc}")
    print("warning: no C compiler found; the calibration will use WebAssembly")
    return None


def main():
    interface()
    sys.path.insert(0, str(ROOT))
    arith = arithmetic_benchmark()
    system = platform.system()
    icon = HERE / "assets" / {"Darwin": "GreenBox.icns", "Windows": "GreenBox.ico"}.get(system, "GreenBox.png")
    sep = ";" if system == "Windows" else ":"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--name", "GreenBox",
           "--icon", str(icon), "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
           "--specpath", str(BUILD), "--paths", str(ROOT),
           *[arg for f in ["GreenBox.html", *LIBS] for arg in ("--add-data", f"{BUILD / f}{sep}.")],
           "--hidden-import", "scipy.io", "--collect-submodules", "greenbox"]
    if arith:
        cmd += ["--add-binary", f"{arith}{sep}."]
    if system == "Darwin":
        cmd += ["--osx-bundle-identifier", "ie.mu.greenbox"]
    subprocess.run(cmd + [str(HERE / "greenbox_desktop.py")], check=True)
    target = DIST / ("GreenBox.app" if system == "Darwin" else "GreenBox")
    archive = shutil.make_archive(str(DIST / f"GreenBox-{ {'Darwin': 'macOS'}.get(system, system) }"), "zip",
                                  DIST, target.name)
    print(f"\nbuilt {target}\narchive {archive}")


if __name__ == "__main__":
    main()
