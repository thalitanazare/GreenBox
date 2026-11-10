# GreenBox desktop application

The GreenBox interface in its own window, with native **File**, **Model**, **View** and **Help** menus, the operating system's own *Open* and *Save* dialogs, and all calculations running in native Python (NumPy and SciPy are bundled). It works fully offline, including `.mat` files and the calibration: the *processor arithmetic* calibration runs a small native program compiled in C by `build.py` and shipped inside the application (if no C compiler is found at build time, the application falls back to the WebAssembly measurement of its window, which also runs offline).

The browser version (`dist/GreenBox.html`) and this application share the same interface and the same `greenbox` package.

## Installing on Windows

1. Download `GreenBox-Setup.exe` from the GreenBox record on Zenodo (https://doi.org/10.5281/zenodo.23687749).
2. Run it. No administrator rights are needed; it installs GreenBox for your user and adds it to the Start menu (and, if you tick the option, to the desktop).
3. **First launch:** the installer is not signed with a code-signing certificate, so Windows SmartScreen may show *Windows protected your PC*. Click **More info**, then **Run anyway**.

Nothing else has to be installed: Python, NumPy, SciPy and the native calibration program are inside the application, and the window uses Microsoft Edge WebView2, which is part of Windows 10 and 11. GreenBox can be removed in *Settings → Apps*.

Without installing, `GreenBox-Windows.zip` (same page) can be unzipped anywhere and `GreenBox\GreenBox.exe` run directly.

## Installing on macOS

1. Unzip `GreenBox-macOS.zip`. You get `GreenBox.app`.
2. Move `GreenBox.app` to the *Applications* folder (optional).
3. **First launch:** the application is not signed with an Apple Developer certificate, so macOS reports that it comes from an unidentified developer. Right-click (or Control-click) `GreenBox.app`, choose **Open**, then confirm with **Open**. From then on it opens with a normal double-click.

The first launch takes about 10–15 seconds while the bundled Python starts; later launches are faster.

## Layout

The application looks and behaves like a desktop program: a **toolbar** (Open, Run, Calibrate, Save results), a **navigation pane** on the left with the five steps, **group boxes** for the settings and a **status bar** at the bottom. The browser version keeps its web layout.

## Exporting figures, tables and equations

Every figure, table and equation has its own export bar:

| Item | Formats |
|---|---|
| Figure | **Data (CSV)**: the plotted data, one pair of columns per curve · **PDF** (vector, ready for a paper) · **SVG** · **PNG** (3× resolution) |
| Table | **CSV** · **LaTeX** (`booktabs` table with caption and label) |
| Equation | **TXT** · **LaTeX** (`align` environment, three terms per line, with label) · **Copy LaTeX** |

Files are saved with the operating system's *Save* dialog. In PDF figures, labels are written with the standard PDF fonts, so Greek letters and subscripts become plain text (for example ρ → rho, w<sub>E</sub> → wE); use SVG if you need them exactly as on screen.

## Using the menus

| Menu | Items |
|---|---|
| **File** | Open data… · Load example · Save results (JSON)… · Save summary (CSV)… · Save selected model (CSV)… |
| **Model** | Run identification · Calibrate this computer… |
| **View** | Home · 1 · Data · 2 · Model structure · 3 · Complexity · 4 · Identification · 5 · Results |
| **Help** | About the model families · About the complexity criteria · About GreenBox |

The window can be minimised, maximised, resized and closed as usual. Settings such as the last calibration are kept between sessions.

## Building the application

Each operating system builds its own application, so the build has to be run on that system.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install numpy scipy pywebview pyinstaller
python desktop/build.py
```

| System | Result | Notes |
|---|---|---|
| macOS | `desktop/dist/GreenBox.app` and `GreenBox-macOS.zip` | tested on macOS (Apple M2) |
| Windows | `desktop/dist/GreenBox/GreenBox.exe` and `GreenBox-Windows.zip`; `iscc desktop\installer.iss` then makes `GreenBox-Setup.exe` | tested on Windows 11; a C compiler (e.g. MinGW-w64 gcc: `winget install BrechtSanders.WinLibs.POSIX.UCRT`) is needed **only to build**, the benchmark is linked statically |
| Linux | `desktop/dist/GreenBox/GreenBox` and `GreenBox-Linux.zip` | needs GTK and WebKit2GTK (`pip install pywebview[gtk]`, or the Qt backend); not yet tested |

To run from the source code without building: `python desktop/build.py` once (it prepares the interface in `desktop/build/`), then `python desktop/greenbox_desktop.py`.

## Automatic check

`GREENBOX_SELFTEST=report.json` makes the application run two examples (identification with the default settings) and a calibration, write a report and close. For example, on macOS:

```bash
GREENBOX_SELFTEST=report.json desktop/dist/GreenBox.app/Contents/MacOS/GreenBox
```

## Files

| File | Purpose |
|---|---|
| `greenbox_desktop.py` | the application: window, menus, dialogs and the bridge between the interface and Python |
| `build.py` | prepares the interface for desktop use (Plotly bundled, native mode) and runs PyInstaller |
| `installer.iss` | Inno Setup script for the Windows installer `GreenBox-Setup.exe` |
| `assets/` | application icons (`.icns` for macOS, `.ico` for Windows, `.png` for Linux) and their source |
