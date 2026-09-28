"""
Build the merged master template (v2) without touching either parent.

v2 = Lunoviq_Master_Financial_Model.xlsx (Data Feed, balance-sheet plugs, named ranges)
   + a schema fix: <definedNames> moved before <calcPr> in workbook.xml. The
     generated template had them in the wrong order, so Microsoft Excel
     treated the file as corrupt and automation could not open it. This is
     the only change to workbook.xml, and names and calc settings are unchanged.
   + the two edits made in Financial_Analysis_and_Valuation_Fixed.xlsx:
       1. 00_Dashboard I37:L40 scenario-aware formulas and I56 health check
       2. 07_Analysis_Scenarios rows 134-156: unused "FTE & Contractor Staffing"
          block cleared (values/formulas, its A134:H134 merge and B155:B156 CF)

The patch is applied at XML level so the charts, styles and named ranges are
copied byte-for-byte. openpyxl is not used to save because it re-serialises charts.
The Dashboard formulas are read from the _Fixed file rather than hardcoded, so
the source of every changed cell can be traced.

Usage:
    python tools/build_master.py [--out templates/Lunoviq_Master_Financial_Model_v2.xlsx]
"""
import argparse
import html
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xlsx_inspect import cells  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "templates" / "Lunoviq_Master_Financial_Model.xlsx"
FIXED = ROOT / "Financial_Analysis_and_Valuation_Fixed.xlsx"

DASH_CELLS = [c + str(r) for r in range(37, 41) for c in "IJKL"] + ["I56"]
FTE_ROWS = range(134, 157)


def sheet_part(z, name):
    wb = z.read("xl/workbook.xml").decode()
    rels = z.read("xl/_rels/workbook.xml.rels").decode()
    rid = re.search(r'<(?:x:)?sheet [^>]*name="%s"[^>]*r:id="([^"]+)"' % re.escape(name), wb).group(1)
    rel = re.search(r'<Relationship [^>]*Id="%s"[^>]*>' % rid, rels).group(0)
    t = re.search(r'Target="([^"]+)"', rel).group(1).lstrip("/")
    return t if t.startswith("xl/") else "xl/" + t


def set_formula(xml, ref, formula):
    """Replace the <x:f> text of an existing formula cell; fail loudly if absent."""
    pat = re.compile(r'(<x:c r="%s"[^>]*>)<x:f>.*?</x:f>(.*?</x:c>)' % ref, re.S)
    body = html.escape(formula.lstrip("="), quote=False)
    new, n = pat.subn(lambda m: m.group(1) + "<x:f>" + body + "</x:f>" + m.group(2), xml, count=1)
    if n != 1:
        raise SystemExit("formula cell %s not found" % ref)
    return new


def clear_rows(xml, rows):
    """Empty every cell in the given rows, keeping only its style index."""
    cleared = 0

    def repl(m):
        nonlocal cleared
        ref, attrs = m.group(1), m.group(2)
        if int(re.sub(r"[A-Z]", "", ref)) not in rows:
            return m.group(0)
        s = re.search(r'\ss="\d+"', attrs)
        if m.group(3) is not None:
            cleared += 1
        return '<x:c r="%s"%s />' % (ref, s.group(0) if s else "")

    xml = re.sub(r'<x:c r="([A-Z]+\d+)"([^>]*?)(?:\s*/>|>(.*?)</x:c>)', repl, xml, flags=re.S)
    return xml, cleared


def fix_workbook_order(xml):
    """OOXML CT_Workbook requires definedNames to precede calcPr."""
    calc = re.search(r"<x:calcPr[^>]*/>", xml)
    names = re.search(r"<x:definedNames>.*?</x:definedNames>", xml, re.S)
    if not calc or not names or names.start() < calc.start():
        return xml
    xml = xml.replace(names.group(0), "", 1)
    return xml.replace(calc.group(0), names.group(0) + calc.group(0), 1)


def remove_block(xml, pattern, what):
    new, n = re.subn(pattern, "", xml, flags=re.S)
    if n != 1:
        raise SystemExit("expected exactly one %s, found %d" % (what, n))
    return new


def build(out):
    fixed = cells(FIXED)["00_Dashboard"]
    zin = zipfile.ZipFile(BASE)
    dash, scen = sheet_part(zin, "00_Dashboard"), sheet_part(zin, "07_Analysis_Scenarios")

    patched = {"xl/workbook.xml": fix_workbook_order(zin.read("xl/workbook.xml").decode())}
    x = zin.read(dash).decode()
    for ref in DASH_CELLS:
        x = set_formula(x, ref, fixed[ref][1])
    patched[dash] = x

    x = zin.read(scen).decode()
    x, n = clear_rows(x, set(FTE_ROWS))
    x = remove_block(x, r'<x:mergeCell ref="A134:H134"\s*/>', "A134:H134 merge")
    x = remove_block(x, r'<x:conditionalFormatting sqref="B155:B156">.*?</x:conditionalFormatting>',
                     "B155:B156 conditional format")
    m = re.search(r'<x:mergeCells count="(\d+)"', x)
    if m:
        x = x.replace(m.group(0), '<x:mergeCells count="%d"' % (int(m.group(1)) - 1), 1)
    patched[scen] = x

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = patched[item.filename].encode() if item.filename in patched else zin.read(item.filename)
            zout.writestr(item, data)
    print("Written %s  (dashboard cells: %d, FTE cells cleared: %d)" % (out, len(DASH_CELLS), n))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "templates" / "Lunoviq_Master_Financial_Model_v2.xlsx"))
    a = ap.parse_args()
    if Path(a.out).resolve() in (BASE.resolve(), FIXED.resolve()):
        raise SystemExit("refusing to overwrite a parent workbook")
    build(a.out)
