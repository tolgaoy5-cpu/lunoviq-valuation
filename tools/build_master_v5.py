"""
Build master template v5 from v4 (v4 is not modified).

Net income reconciliation. The income statement ran EBIT -> interest ->
current tax -> net income, so reported net income could not be reproduced:
non-operating items (equity-method income, investment gains, other income),
deferred tax and minority interest had no line. KO FY2025: model 9.8bn vs
reported 13.1bn. v5 adds these lines in rows that were empty (no rows shift):

  03_3_Statement_Model
    row 18  Non-operating Income (net)   hist: 01!B35:D35   forecast: 01!C56:G56
    row 20  EBT = EBIT + non-operating + interest            (was EBIT + interest)
    row 23  Deferred Tax                 hist: 01!B15:D15   (was hard-coded 0)
    row 25  Minority Interest & Other    hist: 01!B16:D16
                                         forecast: -01!C57:G57 x (EBT + total tax)
    row 26  Net Income = EBT + tax + row 25                  (was EBT + tax)

  New inputs (named): hist_NonOpIncome, hist_DeferredTax, hist_MinorityShare,
  drv_NonOpIncome, drv_MinorityPct. All default to 0, so with no data the model
  computes exactly as v4.

The DCF is unaffected: free cash flow starts from EBITDA, and the investments
behind non-operating income are valued in the equity bridge (v4).

Usage:
    python tools/build_master_v5.py [--out templates/Lunoviq_Master_Financial_Model_v5.xlsx]
"""
import argparse
import html
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_master import sheet_part  # noqa: E402
from build_master_v4 import set_cell  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v4.xlsx"
INP, IS = "01_Inputs_Historicals", "03_3_Statement_Model"
I = "'01_Inputs_Historicals'"
HIST, FC = "BCD", "EFGHI"            # 03 columns: 2023A-2025A, 2026E-2030E
INP_FC = "CDEFG"                     # 01 forecast columns 2026E-2030E


def cells():
    c = {}
    # --- 01 inputs
    for row, label in ((15, "Deferred Tax (hist.)"), (16, "Minority Interest & Other (hist.)"),
                       (35, "Non-operating Income (hist.)")):
        c[(INP, "A%d" % row)] = ("s", label, 833)
        for col in "BCD":
            c[(INP, "%s%d" % (col, row))] = ("n", 0, 1031)
    for row, label, unit, style in ((56, "Non-operating Income", "$000", 1030),
                                    (57, "Minority Share of Net Income", "%", 1032)):
        c[(INP, "A%d" % row)] = ("s", label, 833)
        c[(INP, "B%d" % row)] = ("s", unit, 833)
        for col in INP_FC:
            c[(INP, "%s%d" % (col, row))] = ("n", 0, style)
    # --- 03 income statement
    c[(IS, "A18")] = ("s", "Non-operating Income (net)", 839)
    c[(IS, "A25")] = ("s", "Minority Interest & Other", 839)
    for i, col in enumerate(HIST):
        src = "BCD"[i]
        c[(IS, "%s18" % col)] = ("f", "%s!%s35" % (I, src), 1081)
        c[(IS, "%s23" % col)] = ("f", "%s!%s15" % (I, src), 1076)
        c[(IS, "%s25" % col)] = ("f", "%s!%s16" % (I, src), 1081)
    for i, col in enumerate(FC):
        src = INP_FC[i]
        style = 1102 if col == "E" else 1103
        c[(IS, "%s18" % col)] = ("f", "%s!%s56" % (I, src), style)
        c[(IS, "%s25" % col)] = ("f", "-%s!%s57*(%s20+%s24)" % (I, src, col, col), style)
    for col in HIST + FC:
        c[(IS, "%s20" % col)] = ("f", "%s17+%s18+%s19" % (col, col, col), None)
        c[(IS, "%s26" % col)] = ("f", "%s20+%s24+%s25" % (col, col, col), None)
    return c


NEW_NAMES = {
    "hist_NonOpIncome": "'01_Inputs_Historicals'!$B$35:$D$35",
    "hist_DeferredTax": "'01_Inputs_Historicals'!$B$15:$D$15",
    "hist_MinorityShare": "'01_Inputs_Historicals'!$B$16:$D$16",
    "drv_NonOpIncome": "'01_Inputs_Historicals'!$C$56:$G$56",
    "drv_MinorityPct": "'01_Inputs_Historicals'!$C$57:$G$57",
}


def build(out, src=SRC):
    zin = zipfile.ZipFile(src)
    parts = {n: sheet_part(zin, n) for n in (INP, IS)}
    xml = {n: zin.read(p).decode() for n, p in parts.items()}
    spec = cells()
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "templates" / "Lunoviq_Master_Financial_Model_v5.xlsx"))
    a = ap.parse_args()
    if Path(a.out).resolve() == SRC.resolve():
        raise SystemExit("refusing to overwrite v4")
    build(a.out)
