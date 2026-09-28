"""
Build master template v6 from v5 (v5 is not modified).

Comparables fixes:
  1. P/E was computed as EV / net income (06!H7:H10 = B/E). P/E is equity
     value / net income; with debt it overstated P/E and the "P / E" implied
     equity value (row 26). New column K "Market Cap" per peer; H7:H10 = K/E.
     The demo peers get K = their EV, so the demo company computes as before.
     Peer rows with missing figures (fewer than 4 peers) return "" and drop
     out of the statistics instead of producing #DIV/0!.
  2. Precedent transactions have no free data source. With no transactions
     entered (06!H41:H45 empty) every derived cell shows "n/a" instead of
     #NUM!/#VALUE!, and the method drops out of the football-field average
     (AVERAGE ignores text) and the health checks. Once deals are entered,
     all formulas compute exactly as in v5.

Usage:
    python tools/build_master_v6.py [--out templates/Lunoviq_Master_Financial_Model_v6.xlsx]
"""
import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_master import sheet_part  # noqa: E402
from build_master_v4 import set_cell  # noqa: E402
from xlsx_inspect import cells as read_cells  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v5.xlsx"
COMP, DASH = "06_Comparable_Valuation", "00_Dashboard"
NO_DEALS = "COUNT($H$41:$H$45)=0"


def cells(current):
    comp = current[COMP]
    f = lambda ref: comp[ref][1].lstrip("=")
    c = {(COMP, "J6"): ("s", "Rationale", 992), (COMP, "K6"): ("s", "Market Cap", 992)}
    for r in range(7, 11):
        c[(COMP, "K%d" % r)] = ("n", comp["B%d" % r][1], 1147)       # demo: market cap = EV
        # a peer row with missing figures yields "" (ignored by MEDIAN/QUARTILE/MIN)
        full = "COUNT($B%d:$E%d,$K%d)<5" % (r, r, r)
        c[(COMP, "F%d" % r)] = ("f", 'IF(%s,"",B%d/C%d)' % (full, r, r), None)
        c[(COMP, "G%d" % r)] = ("f", 'IF(%s,"",B%d/D%d)' % (full, r, r), None)
        c[(COMP, "H%d" % r)] = ("f", 'IF(%s,"",K%d/E%d)' % (full, r, r), None)
    guarded = ["G49", "G50", "G51", "G52", "H49", "H50", "H51", "H52",
               "D58", "F58", "F59", "H58", "H59", "H60", "B68", "C68", "D68", "B76", "B77"]
    for ref in guarded:
        c[(COMP, ref)] = ("f", 'IF(%s,"n/a",%s)' % (NO_DEALS, f(ref)), None)
    for ref in ("J58", "J59", "J60"):
        h = "H" + ref[1:]
        c[(COMP, ref)] = ("f", 'IF(ISNUMBER(%s),%s,"n/a")' % (h, f(ref)), None)
    c[(DASH, "C40")] = ("f", 'IF(ISNUMBER(B40),B40/$B$41-1,"n/a")', None)
    return c


def build(out, src=SRC):
    spec = cells(read_cells(src))
    zin = zipfile.ZipFile(src)
    parts = {n: sheet_part(zin, n) for n in (COMP, DASH)}
    xml = {n: zin.read(p).decode() for n, p in parts.items()}
    for (sheet, ref), (kind, content, style) in spec.items():
        xml[sheet] = set_cell(xml[sheet], ref, kind, content, style)
    patched = {parts[k]: v for k, v in xml.items()}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = patched[item.filename].encode() if item.filename in patched else zin.read(item.filename)
            zout.writestr(item, data)
    print("Written %s  (%d cells)" % (out, len(spec)))
    return spec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "templates" / "Lunoviq_Master_Financial_Model_v6.xlsx"))
    a = ap.parse_args()
    if Path(a.out).resolve() == SRC.resolve():
        raise SystemExit("refusing to overwrite v5")
    build(a.out)
