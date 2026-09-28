"""Regression tests for the v2 master template (built by tools/build_master.py)."""
import re
import zipfile

import pytest

from conftest import ROOT
from xlsx_inspect import cells, defined_names, diff
import build_master

V1 = ROOT / "templates" / "Lunoviq_Master_Financial_Model.xlsx"
V2 = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v2.xlsx"
FIXED = ROOT / "Financial_Analysis_and_Valuation_Fixed.xlsx"
BASE = ROOT / "Financial Analysis and Valuation.xlsx"

# v2 was built from the original source workbooks, which are kept locally and not
# published; the provenance tests run only where those files exist.
needs_originals = pytest.mark.skipif(not (FIXED.exists() and BASE.exists()),
                                     reason="original source workbooks not present")


@pytest.fixture(scope="module")
def books():
    return {k: cells(p) for k, p in dict(v1=V1, v2=V2, fixed=FIXED, base=BASE).items() if p.exists()}


@needs_originals
def test_build_is_reproducible(tmp_path):
    out = tmp_path / "v2.xlsx"
    build_master.build(str(out))
    assert cells(out) == cells(V2)


def test_v2_differs_from_v1_only_where_intended(books):
    changed = {s: diff(books["v1"][s], books["v2"][s]) for s in books["v1"]}
    assert changed["00_Dashboard"] == sorted(build_master.DASH_CELLS)
    assert all(int(re.sub(r"[A-Z]", "", r)) in build_master.FTE_ROWS
               for r in changed["07_Analysis_Scenarios"])
    assert not any(v for s, v in changed.items() if s not in ("00_Dashboard", "07_Analysis_Scenarios"))


@needs_originals
def test_v2_is_union_of_both_parents(books):
    """Every cell differing from the base comes from exactly one parent, unchanged."""
    b, v1, fx, v2 = books["base"], books["v1"], books["fixed"], books["v2"]
    for s in v2:
        from_v1, from_fx = set(diff(b.get(s, {}), v1[s])), set(diff(b.get(s, {}), fx.get(s, {})))
        assert not from_v1 & from_fx, s
        assert set(diff(b.get(s, {}), v2[s])) == from_v1 | from_fx, s
        for ref in from_v1:
            assert v2[s].get(ref, (None, None))[:2] == v1[s].get(ref, (None, None))[:2]
        for ref in from_fx:
            assert v2[s].get(ref, (None, None))[:2] == fx[s].get(ref, (None, None))[:2]


def test_named_ranges_and_charts_preserved():
    assert defined_names(V2) == defined_names(V1)
    z1, z2 = zipfile.ZipFile(V1), zipfile.ZipFile(V2)
    assert z1.namelist() == z2.namelist()
    for n in z1.namelist():
        if "chart" in n or "drawing" in n or n == "xl/styles.xml":
            assert z1.read(n) == z2.read(n), n


def test_workbook_xml_schema_order():
    wb = zipfile.ZipFile(V2).read("xl/workbook.xml").decode()
    assert wb.index("<x:definedNames>") < wb.index("<x:calcPr")


@needs_originals
@pytest.mark.excel
def test_excel_recalc_matches_reference(tmp_path, books):
    """Recalculated demo v2 must reproduce the Excel/WPS values of the _Fixed model."""
    from recalc import recalc
    out = recalc(V2, tmp_path / "v2_recalc.xlsx")
    r = cells(out)
    # Known, explained deviations: Lunoviq's precedent-transaction formula (06!C68
    # and its dependents) and a stale cached value in _Fixed (00!J41).
    expected_diff = {("00_Dashboard", "E40"), ("06_Comparable_Valuation", "C68"),
                     ("06_Comparable_Valuation", "C72"), ("00_Dashboard", "J41")}
    bad = set()
    for s, d in books["fixed"].items():
        for ref, (kind, _, val) in d.items():
            if kind != "F" or ref not in r[s]:
                continue
            got = r[s][ref][2]
            try:
                same = abs(float(val) - float(got)) <= 1e-6 * max(1.0, abs(float(val)))
            except (TypeError, ValueError):
                same = (val or "") == (got or "")
            if not same:
                bad.add((s, ref))
    assert bad == expected_diff
    assert r["00_Dashboard"]["I57"][2] == "OK"
