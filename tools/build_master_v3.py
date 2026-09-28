"""
Build master template v3 from v2 (v2 is not modified).

Changes, all in 05_Sensitivity, so the sheet works for real companies:
  1. WACC axis (A7:A12, A29:A34, A41:A46) and terminal-growth axis (B6:G6,
     B28:G28, B40:G40) were hard-coded around the demo company's 8.5% WACC /
     2.5% TGR. They now centre on the model's own WACC and TGR, rounded to
     0.5%, in 0.5% steps. Before, any company with a WACC outside 7.5-10% failed
     the "Sensitivity Grid Coverage" check.
  2. Scenario 2030 EBITDA (B20:D20) was revenue x 32%, the demo company's margin.
     It now uses the model's own 2030 EBITDA margin (03_3_Statement_Model I15/I8).
  3. Scenario 2030 revenue (B19:D19) always compounded the unit-economics volume
     growth (row 39), even in Growth-Margin mode. It now uses the growth row of
     the active revenue engine (row 100 in Growth-Margin mode).

The patch is applied at XML level, like build_master.py, so charts, styles and
names are copied byte for byte.

Usage:
    python tools/build_master_v3.py [--out templates/Lunoviq_Master_Financial_Model_v3.xlsx]
"""
import argparse
import html
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_master import sheet_part  # noqa: E402
from xlsx_inspect import cells  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v2.xlsx"
SHEET = "05_Sensitivity"
DCF = "'04_DCF_Valuation'"
INP = "'01_Inputs_Historicals'"
IS = "'03_3_Statement_Model'"


def axis_formulas():
    f = {}
    # primary grid: row 6 (TGR) and column A rows 7-12 (WACC)
    f["A7"] = "ROUND(%s!$B$29/0.005,0)*0.005-0.01" % DCF
    for r in range(8, 13):
        f["A%d" % r] = "A%d+0.005" % (r - 1)
    f["B6"] = "ROUND(%s!$B$28/0.005,0)*0.005-0.01" % DCF
    for prev, col in zip("BCDEF", "CDEFG"):
        f["%s6" % col] = "%s6+0.005" % prev
    # secondary grids mirror the primary axes
    for top, first in ((28, 29), (40, 41)):
        for col in "BCDEFG":
            f["%s%d" % (col, top)] = "%s$6" % col
        for i in range(6):
            f["A%d" % (first + i)] = "$A$%d" % (7 + i)
    return f


def scenario_formulas(current):
    f = {}
    for col in "BCD":
        rev = current["%s19" % col][1].lstrip("=")
        for c in "CDEFG":
            rev = rev.replace("%s!%s39" % (INP, c),
                              'IF(%s!$C$98="Growth-Margin",%s!%s100,%s!%s39)' % (INP, INP, c, INP, c))
        f["%s19" % col] = rev
        f["%s20" % col] = "%s19*(%s!$I$15/%s!$I$8)" % (col, IS, IS)
    return f


def set_cell_formula(xml, ref, formula):
    """Replace a cell's content with a formula and keep its style."""
    pat = re.compile(r'<x:c r="%s"([^>]*?)(?:\s*/>|>.*?</x:c>)' % ref, re.S)
    m = pat.search(xml)
    if not m:
        raise SystemExit("cell %s not found" % ref)
    style = re.search(r'\ss="\d+"', m.group(1))
    new = '<x:c r="%s"%s><x:f>%s</x:f></x:c>' % (ref, style.group(0) if style else "",
                                                  html.escape(formula, quote=False))
    return xml[:m.start()] + new + xml[m.end():]


def build(out, src=SRC):
    current = cells(src)[SHEET]
    changes = {**axis_formulas(), **scenario_formulas(current)}
    zin = zipfile.ZipFile(src)
    part = sheet_part(zin, SHEET)
    x = zin.read(part).decode()
    for ref, formula in changes.items():
        x = set_cell_formula(x, ref, formula)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, x.encode() if item.filename == part else zin.read(item.filename))
    print("Written %s  (%d cells in %s)" % (out, len(changes), SHEET))
    return changes


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "templates" / "Lunoviq_Master_Financial_Model_v3.xlsx"))
    a = ap.parse_args()
    if Path(a.out).resolve() == SRC.resolve():
        raise SystemExit("refusing to overwrite v2")
    build(a.out)
