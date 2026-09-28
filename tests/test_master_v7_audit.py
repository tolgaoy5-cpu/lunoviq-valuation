"""Template v7 regression tests and the independent forecast audit as a test."""
import pytest

from conftest import ROOT
from xlsx_inspect import cells, defined_names, diff
import build_master_v7

V6 = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v6.xlsx"
V7 = ROOT / "templates" / "Lunoviq_Master_Financial_Model_v7.xlsx"
KO = ROOT / "data" / "fixtures" / "ko.json"


def test_v7_build_reproducible(tmp_path):
    out = tmp_path / "v7.xlsx"
    build_master_v7.build(str(out))
    assert cells(out) == cells(V7)


def test_v7_changes_exactly_the_specified_cells():
    a, b = cells(V6), cells(V7)
    changed = {(s, r) for s in a for r in diff(a[s], b[s])}
    assert changed == set(build_master_v7.cells(a))
    assert "70%" not in b["00_Dashboard"]["I39"][1]
    assert set(defined_names(V7)) - set(defined_names(V6)) == set(build_master_v7.NEW_NAMES)


@pytest.mark.excel
def test_forecast_audit_ko_matches_excel(tmp_path):
    """Independent Python recomputation of 190 forecast line-years equals Excel,
    and historical ratios equal the ratios from reported SEC figures."""
    from lunoviq import pipeline
    from lunoviq.audit import audit
    from test_lunoviq import _market
    res = pipeline.run("KO", facts_path=KO, recalc=True, out_root=tmp_path, market=_market(), with_peers=False)
    a = audit(res["model"], res["run_json"])
    assert a["mismatches"] == [] and a["python_balance_sheet_balances"]
    assert a["historical_ratio_issues"] == []
    assert res["outputs"]["errors"] == []
    import json
    summary = json.load(open(res["summary"]))                 # data behind the web UI
    assert summary["audit"]["mismatches"] == 0 and len(summary["years"]) == 8
    assert all(m["key"] for m in summary["methods"]) and summary["grid"]["price"][0][0] is not None
