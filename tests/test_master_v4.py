"""Regression tests for master template v4 (tools/build_master_v4.py)."""
import pytest

from conftest import ROOT
from xlsx_inspect import cells, defined_names, diff
import build_master_v4

V3 = ROOT / "Lunoviq_Master_Financial_Model_v3.xlsx"
V4 = ROOT / "Lunoviq_Master_Financial_Model_v4.xlsx"


def test_v4_build_reproducible(tmp_path):
    out = tmp_path / "v4.xlsx"
    build_master_v4.build(str(out))
    assert cells(out) == cells(V4)


def test_v4_changes_are_exactly_the_intended_ones():
    a, b = cells(V3), cells(V4)
    changed = {s: set(diff(a[s], b[s])) for s in a}
    expected = {}
    for (sheet, ref) in build_master_v4.CELLS:
        expected.setdefault(sheet, set()).add(ref)
    sens = {r for r, v in a["05_Sensitivity"].items()
            if v[0] == "F" and build_master_v4.TERMINAL_OLD in v[1]}
    expected["05_Sensitivity"] = sens
    assert {s: v for s, v in changed.items() if v} == expected
    for r in sens:
        assert b["05_Sensitivity"][r][1] == a["05_Sensitivity"][r][1].replace(
            build_master_v4.TERMINAL_OLD, build_master_v4.TERMINAL_NEW)
    assert set(defined_names(V4)) - set(defined_names(V3)) == set(build_master_v4.NEW_NAMES)


@pytest.mark.excel
def test_v4_demo_recalc(tmp_path):
    import openpyxl
    from recalc import recalc
    from lunoviq.valuation_checks import dcf_checks
    out = recalc(V4, tmp_path / "v4.xlsx")
    c = cells(out)
    d = c["04_DCF_Valuation"]
    assert float(d["G9"][2]) == pytest.approx(max(float(d["F9"][2]),
                                                  float(c["03_3_Statement_Model"]["I16"][2])))
    assert c["00_Dashboard"]["I57"][2] == "OK" and c["05_Sensitivity"]["B51"][2] == "OK"
    chk = dcf_checks(openpyxl.load_workbook(out, data_only=True))
    assert chk["ev_match"]
