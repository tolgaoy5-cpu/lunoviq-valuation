"""
Build master template v7 from v6 (v6 is not modified).

Analysis-sheet fixes found in the independent audit:
  1. Current / quick ratio used only accounts payable (+revolver) as current
     liabilities, so every other current liability (accrued expenses, short-term
     debt, taxes payable...) was missing: KO showed 5.5x vs ~1.0x reported.
     New inputs 01!B76:D76 "Other Current Assets" and B77:D77 "Other Current
     Liabilities" (historical, from SEC AssetsCurrent / LiabilitiesCurrent);
     07 rows 82-83 include them (forecast years hold the last reported level).
  2. Non-active scenario UFCF was EBITDA x 70% (the demo company's
     conversion). It now uses the model's own 2030 UFCF / EBITDA
     (04!F11/F7): 07!B47:D47 and 00_Dashboard!I39:K39.
  3. Interest on existing debt used the market cost of debt of the WACC
     (01!C62): KO's forecast interest jumped 48% vs reported. New input
     01!C79 "Interest Rate on Existing Debt" (named fin_DebtRate; demo value
     = C62 so the demo computes as before) drives 03!E102:I102. WACC still
     uses the marginal cost C62.

The benchmark peer ranges (07 C:F) are data cells; the pipeline fills them from
the real peer set (lunoviq/excel/presentation.py).

Usage:
    python tools/build_master_v7.py [--out Lunoviq_Master_Financial_Model_v7.xlsx]
"""
import argparse
import html
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_master import sheet_part  # noqa: E402
from build_master_v4 import set_cell  # noqa: E402
from xlsx_inspect import cells as read_cells  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Lunoviq_Master_Financial_Model_v6.xlsx"
INP, AN, DASH = "01_Inputs_Historicals", "07_Analysis_Scenarios", "00_Dashboard"
S, I = "'03_3_Statement_Model'", "'01_Inputs_Historicals'"
CONV = "('04_DCF_Valuation'!$F$11/'04_DCF_Valuation'!$F$7)"
NEW_NAMES = {"fin_DebtRate": "'01_Inputs_Historicals'!$C$79",
             "hist_OtherCurrentAssets": "'01_Inputs_Historicals'!$B$76:$D$76",
             "hist_OtherCurrentLiab": "'01_Inputs_Historicals'!$B$77:$D$77"}


def cells(current):
    c = {(INP, "A76"): ("s", "Other Current Assets (hist.)", 833),
         (INP, "A77"): ("s", "Other Current Liabilities (hist.)", 833)}
    for col in "BCD":
        c[(INP, col + "76")] = ("n", 0, 1031)
        c[(INP, col + "77")] = ("n", 0, 1031)
    for col in "BCDEFGHI":
        src = col if col in "BCD" else "$D"
        oca, ocl = "%s!%s76" % (I, src), "%s!%s77" % (I, src)
        cl = "(%s!%s64+IFERROR(%s!%s125,0)+%s)" % (S, col, S, col, ocl)
        c[(AN, col + "82")] = ("f", "(%s!%s57+%s!%s58+%s!%s59+%s)/%s" % (S, col, S, col, S, col, oca, cl), None)
        c[(AN, col + "83")] = ("f", "(%s!%s57+%s!%s58)/%s" % (S, col, S, col, cl), None)
    for col in "BCD":
        c[(AN, col + "47")] = ("f", "%s45*%s" % (col, CONV), None)
    c[(INP, "A79")] = ("s", "Interest Rate on Existing Debt", 833)
    c[(INP, "B79")] = ("s", "%", 833)
    c[(INP, "C79")] = ("n", current[INP]["C62"][1], 1032)
    for col in "EFGHI":
        c[("03_3_Statement_Model", col + "102")] = ("f", "%s!$C$79" % I, None)
    for col in "IJK":
        f = current[DASH][col + "39"][1].lstrip("=")
        assert "*70%" in f, f
        c[(DASH, col + "39")] = ("f", f.replace("*70%", "*" + CONV), None)
    return c


def build(out, src=SRC):
    spec = cells(read_cells(src))
    zin = zipfile.ZipFile(src)
    parts = {n: sheet_part(zin, n) for n in (INP, AN, DASH, "03_3_Statement_Model")}
    xml = {n: zin.read(p).decode() for n, p in parts.items()}
    for (sheet, ref), (kind, content, style) in spec.items():
        xml[sheet] = set_cell(xml[sheet], ref, kind, content, style)
    wb = zin.read("xl/workbook.xml").decode()
    add = "".join('<x:definedName name="%s">%s</x:definedName>' % (n, html.escape(v, quote=False))
                  for n, v in NEW_NAMES.items())
    wb = wb.replace("</x:definedNames>", add + "</x:definedNames>", 1)
    patched = {parts[k]: v for k, v in xml.items()}
    patched["xl/workbook.xml"] = wb
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = patched[item.filename].encode() if item.filename in patched else zin.read(item.filename)
            zout.writestr(item, data)
    print("Written %s  (%d cells, %d names)" % (out, len(spec), len(NEW_NAMES)))
    return spec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "Lunoviq_Master_Financial_Model_v7.xlsx"))
    a = ap.parse_args()
    if Path(a.out).resolve() == SRC.resolve():
        raise SystemExit("refusing to overwrite v6")
    build(a.out)
