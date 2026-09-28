"""Regression tests for master template v3 (tools/build_master_v3.py)."""
import pytest

from conftest import ROOT
from xlsx_inspect import cells, diff
import build_master_v3

V2 = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v2.xlsx"
V3 = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v3.xlsx"


def test_v3_build_reproducible(tmp_path):
    out = tmp_path / "v3.xlsx"
    build_master_v3.build(str(out))
    assert cells(out) == cells(V3)


def test_v3_changes_only_sensitivity_cells():
    a, b = cells(V2), cells(V3)
    changed = {s: diff(a[s], b[s]) for s in a}
    expected = sorted(build_master_v3.axis_formulas()) + ["B19", "C19", "D19", "B20", "C20", "D20"]
    assert changed["05_Sensitivity"] == sorted(expected)
    assert not any(v for s, v in changed.items() if s != "05_Sensitivity")


@pytest.mark.excel
def test_v3_keeps_core_valuation(tmp_path):
    from recalc import recalc
    a = cells(recalc(V2, tmp_path / "v2.xlsx"))
    b = cells(recalc(V3, tmp_path / "v3.xlsx"))
    for sheet, ref in [("00_Dashboard", "B%d" % r) for r in range(37, 43)] + [
            ("04_DCF_Valuation", "H48"), ("04_DCF_Valuation", "H49"), ("00_Dashboard", "I57")]:
        assert a[sheet][ref][2] == b[sheet][ref][2], (sheet, ref)
    s = b["05_Sensitivity"]
    assert s["B51"][2] == "OK" and s["B52"][2] == "OK"
    axis = [float(s["A%d" % r][2]) for r in range(7, 13)]
    wacc = float(b["04_DCF_Valuation"]["B29"][2])
    assert axis[0] <= wacc <= axis[-1]
