"""
Build master template v4 from v3 (v3 is not modified).

Valuation-method fixes (5-year horizon kept):
  1. Normalised terminal year. The terminal value was based on the 2030 free
     cash flow, which still carried that year's capex, e.g. heavy AI capex
     with growth already at 2.5%: an implied "over-invest forever". A new
     column 04_DCF_Valuation!G6:G11 holds the terminal-year FCF = 2030 EBITDA
     - 2030 taxes - maintenance capex + 2030 working-capital cash, where
     maintenance capex = the smaller of 2030 capex and 2030 D&A:
       capex > D&A (still investing ahead, e.g. MSFT): steady state -> D&A
       D&A > capex (legacy assets still running off in 2030, e.g. AMZN with
       ~5-year lives): capex is already maintenance level; using the
       transitional D&A would overstate it.
     Every terminal-value formula (DCF E29, B48 and all 05_Sensitivity grids)
     now uses $G$11. The explicit 2030 cash flow (F11) is unchanged.
  2. Equity bridge. Net debt (E34) now also subtracts non-operating
     investments and adds minority interest:
         E34 = Debt - Cash - Non-operating Assets + Minority Interest
     The inputs are 01_Inputs_Historicals!C68:C69, named val_NonOpAssets and
     val_MinorityInterest (default 0, so the demo company is unaffected). All
     users of E34 (DCF, sensitivity, comparables) stay consistent automatically.

Applied at XML level; charts, styles and all other cells are byte-identical.

Usage:
    python tools/build_master_v4.py [--out Lunoviq_Master_Financial_Model_v4.xlsx]
"""
import argparse
import html
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_master import sheet_part  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Lunoviq_Master_Financial_Model_v3.xlsx"
INP, DCF, SENS = "01_Inputs_Historicals", "04_DCF_Valuation", "05_Sensitivity"
IS = "'03_3_Statement_Model'"

# (sheet, ref) -> (kind, content, style); kind: "f" formula, "s" string, "n" number
CELLS = {
    (INP, "A68"): ("s", "Non-operating Assets", 833),
    (INP, "B68"): ("s", "$000", 833),
    (INP, "C68"): ("n", 0, 1031),
    (INP, "A69"): ("s", "Minority Interest", 833),
    (INP, "B69"): ("s", "$000", 833),
    (INP, "C69"): ("n", 0, 1031),
    (DCF, "G6"): ("s", "Terminal (norm.)", 848),
    (DCF, "G7"): ("f", "F7", 1081),
    (DCF, "G8"): ("f", "F8", 1081),
    # maintenance capex = smaller of 2030 capex and 2030 D&A (both negative -> MAX)
    (DCF, "G9"): ("f", "MAX(F9,%s!I16)" % IS, 1081),
    (DCF, "G10"): ("f", "F10", 1081),
    (DCF, "G11"): ("f", "SUM(G7:G10)", 1051),
    (DCF, "G12"): ("s", "capex = min(capex, D&A)", 839),
    (DCF, "E29"): ("f", "($G$11*(1+$B$28)/($B$29-$B$28))*F24", 1116),
    (DCF, "B48"): ("f", "$G$11*(1+$B$28)", None),
    (DCF, "D31"): ("s", "Less: Non-operating Assets", 909),
    (DCF, "E31"): ("f", "'01_Inputs_Historicals'!C68", 1117),
    (DCF, "D32"): ("s", "Add: Minority Interest", 909),
    (DCF, "E32"): ("f", "'01_Inputs_Historicals'!C69", 1117),
    (DCF, "D34"): ("s", "Net Debt (adj.)", 909),
    (DCF, "E34"): ("f", "%s!D65-%s!D57-E31+E32" % (IS, IS), 1117),
}
NEW_NAMES = {"val_NonOpAssets": "'01_Inputs_Historicals'!$C$68",
             "val_MinorityInterest": "'01_Inputs_Historicals'!$C$69"}
TERMINAL_OLD, TERMINAL_NEW = "'04_DCF_Valuation'!$F$11", "'04_DCF_Valuation'!$G$11"


def set_cell(xml, ref, kind, content, style):
    pat = re.compile(r'<x:c r="%s"([^>]*?)(?:\s*/>|>.*?</x:c>)' % ref, re.S)
    m = pat.search(xml)
    if not m:
        raise SystemExit("cell %s not found" % ref)
    if style is None:
        s = re.search(r'\ss="(\d+)"', m.group(1))
        style = s.group(1) if s else None
    st = ' s="%s"' % style if style is not None else ""
    if kind == "f":
        body = '%s><x:f>%s</x:f></x:c>' % (st, html.escape(content, quote=False))
    elif kind == "s":
        body = '%s t="str"><x:v>%s</x:v></x:c>' % (st, html.escape(content, quote=False))
    else:
        body = '%s t="n"><x:v>%s</x:v></x:c>' % (st, content)
    return xml[:m.start()] + '<x:c r="%s"' % ref + body + xml[m.end():]


def build(out, src=SRC):
    zin = zipfile.ZipFile(src)
    parts = {name: sheet_part(zin, name) for name in (INP, DCF, SENS)}
    xml = {name: zin.read(p).decode() for name, p in parts.items()}
    for (sheet, ref), (kind, content, style) in CELLS.items():
        xml[sheet] = set_cell(xml[sheet], ref, kind, content, style)
    n_term = xml[SENS].count(TERMINAL_OLD)
    if n_term < 100:
        raise SystemExit("expected >=100 terminal references in %s, found %d" % (SENS, n_term))
    xml[SENS] = xml[SENS].replace(TERMINAL_OLD, TERMINAL_NEW)

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
    print("Written %s  (%d cells, %d terminal refs in %s, %d names)" % (out, len(CELLS), n_term, SENS, len(NEW_NAMES)))
    return n_term


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "Lunoviq_Master_Financial_Model_v4.xlsx"))
    a = ap.parse_args()
    if Path(a.out).resolve() == SRC.resolve():
        raise SystemExit("refusing to overwrite v3")
    build(a.out)
