"""Data files in the formats people bring: one signal per column, one sample per row."""

import numpy as np
import pytest

from greenbox.data import read_file

U = [0.5, -1.25, 2.0, 0.0, 1.5]
Y = [0.1, 0.2, -0.3, 0.4, 0.55]


def rows(fmt_row, header=None):
    lines = [header] if header else []
    lines += [fmt_row(u, y) for u, y in zip(U, Y)]
    return "\n".join(lines) + "\n"


CASES = {
    "comma with header": rows(lambda u, y: f"{u},{y}", "u,y"),
    "comma without header": rows(lambda u, y: f"{u},{y}"),
    "semicolon, decimal point": rows(lambda u, y: f"{u};{y}", "u;y"),
    "semicolon, decimal comma (Excel pt-BR / European)": rows(lambda u, y: f"{u};{y}".replace(".", ","), "u;y"),
    "tab with spaced header": rows(lambda u, y: f"{u}\t{y}", "input (V)\toutput (rad/s)"),
    "tab, decimal comma": rows(lambda u, y: f"{u}\t{y}".replace(".", ","), "u\ty"),
    "spaces": rows(lambda u, y: f"  {u}   {y}", "u y"),
    "trailing separator": rows(lambda u, y: f"{u},{y},", '"u","y",'),
    "quoted values": rows(lambda u, y: f'"{u}","{y}"', '"u","y"'),
    "comments and units line": "# my experiment\n" + rows(lambda u, y: f"{u},{y}", "u,y\nV,rad/s"),
    "Windows line endings": rows(lambda u, y: f"{u},{y}", "u,y").replace("\n", "\r\n"),
    "scientific notation": rows(lambda u, y: f"{u:e},{y:E}", "u,y"),
}


@pytest.mark.parametrize("text", CASES.values(), ids=CASES.keys())
def test_two_columns(text):
    cols = read_file("data.csv", text.encode("utf-8"))
    assert len(cols) == 2, cols.keys()
    u, y = cols.values()
    np.testing.assert_allclose(u, U)
    np.testing.assert_allclose(y, Y)


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "latin-1"])
def test_encodings(encoding):
    text = rows(lambda u, y: f"{u};{y}".replace(".", ","), "entrada (°C);saída")
    cols = read_file("data.txt", text.encode(encoding))
    assert list(cols) == ["entrada (°C)", "saída"]
    np.testing.assert_allclose(cols["saída"], Y)


def test_header_names_and_time_column():
    cols = read_file("d.csv", rows(lambda u, y: f"0.01,{u},{y}", "t,u,y").encode())
    assert list(cols) == ["t", "u", "y"]


def test_signals_in_rows_are_transposed():
    n = 50
    text = ",".join(str(i) for i in range(n)) + "\n" + ",".join(str(2 * i) for i in range(n)) + "\n"
    cols = read_file("d.csv", text.encode())
    assert list(cols) == ["row1", "row2"]
    assert len(cols["row1"]) == n


def test_columns_of_different_length():
    cols = read_file("d.csv", b"uEst,yEst,uVal,yVal\n1,2,3,4\n,,5,6\n")
    np.testing.assert_allclose(cols["uVal"], [3, 5])
    assert np.isnan(cols["uEst"][1])


def test_spreadsheet_is_rejected_with_advice():
    with pytest.raises(ValueError, match="save the sheet as CSV"):
        read_file("data.xlsx", b"PK\x03\x04")


def test_no_numbers():
    with pytest.raises(ValueError, match="one signal per column"):
        read_file("d.csv", b"a,b\nx,y\n")
