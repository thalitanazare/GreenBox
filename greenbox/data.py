"""Loading and preparing input/output data."""

from __future__ import annotations

import io
import re

import numpy as np


def _separator(lines):
    """Column separator shared by the first lines: tab, semicolon, comma, or None (whitespace)."""
    sample = lines[:20]
    for sep in ("\t", ";", ","):
        counts = {ln.count(sep) for ln in sample}
        if min(counts) > 0 and len(counts) <= 2:
            return sep
    return None


def _number(s, decimal_comma):
    s = s.strip().strip('"').strip("'")
    if not s:
        return np.nan
    if decimal_comma:
        s = s.replace(",", ".")
    return float(s)


def read_table(text: str):
    """Parse CSV / TSV / semicolon / whitespace-separated numeric text.

    One signal per column, one sample per row. Returns (column_names, 2-D array).
    Non-numeric lines at the top are header lines (the first one gives the column
    names); lines starting with # or % are comments. A semicolon-separated file may
    use a decimal comma (1,5;2,25). Empty columns are dropped, and a file with the
    signals in rows (a few long rows) is transposed.
    """
    text = text.lstrip("\ufeff")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "%"))]
    if not lines:
        raise ValueError("file is empty")
    sep = _separator(lines)
    decimal_comma = sep in (";", "\t", None) and any(re.search(r"\d,\d", ln) for ln in lines[1:20])

    def split(ln):
        parts = ln.split(sep) if sep else ln.split()
        if sep and parts and not parts[-1].strip():  # trailing separator
            parts = parts[:-1]
        return parts

    names, rows = None, []
    for ln in lines:
        parts = split(ln)
        try:
            rows.append([_number(x, decimal_comma) for x in parts])
        except ValueError:
            if not rows and names is None:
                names = [x.strip().strip('"').strip("'") for x in parts]
            continue  # header, units line or text line
    if not rows:
        raise ValueError("no numeric rows found: the file must have one signal per column and one sample per row")
    width = max(len(r) for r in rows)
    arr = np.full((len(rows), width), np.nan)
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    if names is None or len(names) != width:
        names = [f"col{i + 1}" for i in range(width)]
    if arr.shape[0] <= 4 and arr.shape[1] >= 20:  # signals stored in rows
        names, arr = [f"row{i + 1}" for i in range(arr.shape[0])], arr.T
    keep = [j for j in range(arr.shape[1]) if np.isfinite(arr[:, j]).any()]
    return [names[j] for j in keep], arr[:, keep]


def decode(raw: bytes) -> str:
    """Text of a data file saved as UTF-8 (with or without BOM), UTF-16 (Excel "Unicode text") or Latin-1."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def read_mat(raw: bytes):
    """Numeric vectors/matrices from a .mat file, as named columns."""
    from scipy.io import loadmat  # imported lazily: scipy is large in the browser

    d = loadmat(io.BytesIO(raw), squeeze_me=False)
    cols = {}

    def add(name, v):
        if isinstance(v, np.ndarray) and v.dtype.kind in "fiu" and v.ndim <= 2 and v.size > 1:
            v = np.asarray(v, dtype=float)
            if 1 in v.shape:
                cols[name] = v.ravel()
            else:
                if v.shape[0] < v.shape[1]:
                    v = v.T
                for j in range(v.shape[1]):
                    cols[f"{name}[:,{j + 1}]"] = v[:, j]
        elif isinstance(v, np.ndarray) and v.dtype.names:  # struct
            for f in v.dtype.names:
                add(f"{name}.{f}", v[f][0, 0] if v.size == 1 else v[f])
        elif isinstance(v, np.ndarray) and v.dtype == object and v.size == 1:
            add(name, v.item())

    for k, v in d.items():
        if not k.startswith("__"):
            add(k, v)
    return cols


def read_file(name: str, raw: bytes):
    """Return {column_name: 1-D array} for any supported file."""
    if name.lower().endswith(".mat"):
        return read_mat(raw)
    if name.lower().endswith((".xlsx", ".xls", ".ods")):
        raise ValueError("spreadsheet files are not read directly: save the sheet as CSV (one signal per column) and open the CSV")
    names, arr = read_table(decode(raw))
    cols = {}
    for i, n in enumerate(names):
        key = n if n not in cols else f"{n}_{i + 1}"
        cols[key] = arr[:, i]
    return cols


def safe_name(name):
    return re.sub(r"[^\w.\[\]:,-]", "_", name)


def apply_scale(u, y, scale):
    u = None if u is None else (np.asarray(u, dtype=float) - scale["u"][0]) / scale["u"][1]
    return u, (np.asarray(y, dtype=float) - scale["y"][0]) / scale["y"][1]


def prepare(u, y, start=0, stop=None, step=1, normalise="none"):
    """Cut, decimate (keep every ``step``-th sample) and scale the signals.

    ``normalise``: "none" | "maxabs" (divide by max |x|) | "zscore". Returns u, y and the scale info.
    """
    y = np.asarray(y, dtype=float).ravel()
    u = None if u is None else np.asarray(u, dtype=float).ravel()
    n = len(y) if u is None else min(len(u), len(y))
    sl = slice(int(start), n if stop in (None, 0) else min(int(stop), n), max(int(step), 1))
    y = y[sl]
    u = None if u is None else u[sl]
    finite = np.isfinite(y) if u is None else np.isfinite(y) & np.isfinite(u)
    y = y[finite]
    u = None if u is None else u[finite]
    scale = {"u": [0.0, 1.0], "y": [0.0, 1.0]}  # x_scaled = (x - offset) / gain
    if normalise == "maxabs":
        for k, x in (("u", u), ("y", y)):
            if x is not None:
                g = float(np.max(np.abs(x))) or 1.0
                scale[k] = [0.0, g]
    elif normalise == "zscore":
        for k, x in (("u", u), ("y", y)):
            if x is not None:
                scale[k] = [float(np.mean(x)), float(np.std(x)) or 1.0]
    if u is not None:
        u = (u - scale["u"][0]) / scale["u"][1]
    y = (y - scale["y"][0]) / scale["y"][1]
    return u, y, scale
