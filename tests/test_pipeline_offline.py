"""Offline pipeline tests using the cached SEC companyfacts payload (ko.json)."""
import json

import pytest

from conftest import ROOT
from xlsx_inspect import cells
import edgar_feed as EF


@pytest.fixture(scope="module")
def ko():
    facts = json.load(open(ROOT / "data" / "fixtures" / "ko.json"))
    return EF.build_feed(facts, 3)


def test_ko_years_and_core_values(ko):
    series, used, years = ko
    assert years == [2023, 2024, 2025]
    assert series["Revenue"][2025] == 47_941_000_000
    assert series["Total Assets (reported)"][2025] == 104_816_000_000
    assert used["Revenue"] != EF.NOT_FOUND


def test_ko_balance_sheet_plugs_close(ko):
    series, _, years = ko
    for y in years:
        assets = sum(series[k][y] for k in ("Cash & Equivalents", "Accounts Receivable",
                                            "Inventory", "PP&E (net)", "Other Assets (plug)"))
        assert assets == series["Total Assets (reported)"][y]
        equity = sum(series[k][y] for k in ("Common Equity", "Retained Earnings", "Other Equity (plug)"))
        assert equity == series["Total Equity (reported)"][y]


def test_write_model_on_v2_leaves_template_untouched(ko, tmp_path):
    series, used, years = ko
    tpl = ROOT / "Lunoviq_Master_Financial_Model_v2.xlsx"
    before = tpl.read_bytes()
    out = tmp_path / "KO.xlsx"
    EF.write_model(str(tpl), str(out), "COCA COLA CO", "0000021344", series, used, years)
    assert tpl.read_bytes() == before
    c = cells(out)
    assert float(c["01_Inputs_Historicals"]["D18"][1]) == 47_941_000
    assert c["00_Dashboard"]["B12"][1] == "Growth-Margin"


@pytest.mark.excel
def test_ko_recalc_health_ok(ko, tmp_path):
    from recalc import recalc
    series, used, years = ko
    out = tmp_path / "KO.xlsx"
    EF.write_model(str(ROOT / "Lunoviq_Master_Financial_Model_v2.xlsx"), str(out),
                   "COCA COLA CO", "0000021344", series, used, years)
    c = cells(recalc(out))
    assert c["00_Dashboard"]["I57"][2] == "OK"
    bs = c["03_3_Statement_Model"]
    for col in "BCDEFGHI":
        assert abs(float(bs[col + "61"][2]) - float(bs[col + "74"][2])) < 1e-3   # assets == L+E
