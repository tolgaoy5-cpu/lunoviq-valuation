"""
Regression tests for the XBRL mapping fixes found in the 12-company run
(2026-09-24). Each test builds a tiny synthetic companyfacts payload that
reproduces one real failure.
"""
import pytest

from lunoviq import drivers
from lunoviq.providers import sec_xbrl
from lunoviq.schema import DataPoint, FinancialStatements


def flow(fy_end, val, form="10-K", days=364):
    import datetime as dt
    end = dt.date.fromisoformat(fy_end)
    return {"start": (end - dt.timedelta(days=days)).isoformat(), "end": fy_end, "val": val,
            "form": form, "filed": fy_end}


def inst(date, val, form="10-K"):
    return {"end": date, "val": val, "form": form, "filed": date}


def facts(**tags):
    return {"entityName": "TEST", "cik": 1,
            "facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items()}}}


ENDS = ["2023-12-31", "2024-12-31", "2025-12-31"]


def test_tag_switch_keeps_recent_years():
    """NVDA: revenue moved from RevenueFromContract... to Revenues; balance sheet must follow."""
    f = facts(RevenueFromContractWithCustomerExcludingAssessedTax=[flow(ENDS[0], 100)],
              Revenues=[flow(ENDS[1], 120), flow(ENDS[2], 150)],
              Assets=[inst(e, 1000 + i) for i, e in enumerate(ENDS)])
    assert sorted(sec_xbrl.fiscal_year_ends(f)) == [2023, 2024, 2025]
    series, used, years = sec_xbrl.build_feed(f, 3)
    assert years == [2023, 2024, 2025]
    assert [series["Revenue"][y] for y in years] == [100, 120, 150]
    assert series["Total Assets (reported)"][2025] == 1002
    assert " | " in used["Revenue"]


def test_open_year_from_10q_is_excluded():
    """AMZN: a 12-month flow in a 10-Q must not create an unreported fiscal year."""
    f = facts(Revenues=[flow(e, 100 + i) for i, e in enumerate(ENDS)] +
              [flow("2026-06-30", 200, form="10-Q")])
    _, _, years = sec_xbrl.build_feed(f, 3)
    assert years == [2023, 2024, 2025]


def test_no_10k_revenue_raises_data_error():
    """XOM: SEC companyfacts without any 10-K revenue."""
    with pytest.raises(sec_xbrl.DataError):
        sec_xbrl.build_feed(facts(Revenues=[flow("2026-06-30", 1, form="10-Q", days=90)]), 3)


def _bs(extra=None):
    tags = dict(Revenues=[flow(e, 100) for e in ENDS],
                CashAndCashEquivalentsAtCarryingValue=[inst(e, 10) for e in ENDS],
                AccountsReceivableNetCurrent=[inst(e, 20) for e in ENDS],
                PropertyPlantAndEquipmentNet=[inst(e, 50) for e in ENDS],
                Assets=[inst(e, 200) for e in ENDS],
                Liabilities=[inst(e, 120) for e in ENDS],
                StockholdersEquity=[inst(e, 75) for e in ENDS],      # 5 = non-controlling interest
                AccountsPayableCurrent=[inst(e, 15) for e in ENDS],
                LongTermDebt=[inst(e, 60) for e in ENDS],
                CommonStockValue=[inst(e, 5) for e in ENDS],
                AdditionalPaidInCapital=[inst(e, 10) for e in ENDS],
                RetainedEarningsAccumulatedDeficit=[inst(e, 40) for e in ENDS])
    tags.update(extra or {})
    return facts(**tags)


def test_plugs_close_with_missing_components_and_nci():
    """AAPL (no DTL), GOOGL (no inventory), PEP (NCI outside parent equity)."""
    s, _, years = sec_xbrl.build_feed(_bs(), 3)
    for y in years:
        g = lambda k: s[k].get(y) or 0
        assets = sum(g(k) for k in ("Cash & Equivalents", "Accounts Receivable", "Inventory",
                                    "PP&E (net)", "Other Assets (plug)"))
        liab = sum(g(k) for k in ("Accounts Payable", "Total Debt", "Deferred Tax Liability",
                                  "Other Liabilities (plug)"))
        eq = sum(g(k) for k in ("Common Equity", "Retained Earnings", "Other Equity (plug)"))
        assert assets == 200 and liab == 120 and eq == 80 and assets == liab + eq


def test_cash_uses_one_securities_tag():
    """TSLA reports the same securities as MarketableSecuritiesCurrent and ShortTermInvestments."""
    s, _, _ = sec_xbrl.build_feed(_bs(dict(MarketableSecuritiesCurrent=[inst(e, 7) for e in ENDS],
                                           ShortTermInvestments=[inst(e, 7) for e in ENDS])), 3)
    assert s["Cash & Equivalents"][2025] == 17


def test_operating_expense_reconciliation():
    """AMZN: categories outside COGS/SG&A are captured so EBIT = reported operating income."""
    f = _bs(dict(CostOfGoodsAndServicesSold=[flow(e, 40) for e in ENDS],
                 SellingGeneralAndAdministrativeExpense=[flow(e, 10) for e in ENDS],
                 DepreciationDepletionAndAmortization=[flow(e, 5) for e in ENDS],
                 OperatingIncomeLoss=[flow(e, 20) for e in ENDS]))
    s, used, _ = sec_xbrl.build_feed(f, 3)
    other = s["Other Operating Expense"][2025]
    assert 100 - 40 - 10 - other - 5 == 20
    assert "mutabakat" in used["Other Operating Expense"]


def test_current_tax_components_summed():
    f = _bs(dict(CurrentFederalTaxExpenseBenefit=[flow(e, 6) for e in ENDS],
                 CurrentStateAndLocalTaxExpenseBenefit=[flow(e, 1) for e in ENDS],
                 CurrentForeignTaxExpenseBenefit=[flow(e, 2) for e in ENDS]))
    s, _, _ = sec_xbrl.build_feed(f, 3)
    assert s["Current Income Tax"][2025] == 9


# ---------------------------------------------------------------- drivers
def _st(**series):
    items = {k: {y: DataPoint(v[i], "USD", "t", "") for i, y in enumerate([2023, 2024, 2025])}
             for k, v in series.items()}
    return FinancialStatements("T", "T", "1", "USD", [2023, 2024, 2025], items)


def test_growth_fades_to_terminal():
    st = _st(revenue=[100, 121, 144])                      # 20% CAGR
    g = drivers.revenue_growth(st, 0.025).value
    assert g[0] == pytest.approx(0.2 + (0.025 - 0.2) / 5, abs=1e-3) and g[-1] == pytest.approx(0.025)


def test_growth_cap():
    g = drivers.revenue_growth(_st(revenue=[100, 200, 400]), 0.025)
    assert "capped" in g.method and g.value[0] < 0.40


def test_working_capital_capex_life_payout():
    st = _st(revenue=[100, 100, 200], cogs=[50, 50, 100], receivables=[10, 10, 20],
             inventory=[5, 5, 10], payables=[8, 8, 16], capex=[5, 5, 20], ppe_net=[40, 40, 80],
             d_and_a=[4, 4, 8], dividends=[2, 2, 4], net_income=[10, 10, 20])
    d = drivers.build(st, 0.025)
    assert d["drv_DSO"].value == pytest.approx(36.5) and d["drv_DPO"].value == pytest.approx(58.4)
    assert d["drv_CapexPct"].value == pytest.approx((0.05 + 0.05 + 0.10) / 3, abs=1e-4)
    assert d["drv_LifeExisting"].value == 10 and d["drv_Payout"].value == pytest.approx(0.2)
    assert d["hist_TaxBasisPPE"].value == [0.04, 0.04, 0.08]


# ---------------------------------------------------------------- interest double-count rules
def test_net_interest_and_nonop_exclude_interest_income():
    """KO: interest income moves from non-operating income into net interest, so the
    forecast (which earns interest on cash) does not count it twice."""
    st = _st(revenue=[100, 100, 100], cogs=[40, 40, 40], sga=[20, 20, 20], d_and_a=[5, 5, 5],
             interest_expense=[4, 4, 4], interest_income=[1, 1, 1], pretax_income=[33, 33, 33],
             income_tax_total=[7, 7, 7], current_tax=[7, 7, 7], net_income=[26, 26, 26])
    d = drivers.below_ebit(st)
    assert d["hist_InterestExpense"].value == pytest.approx([-0.003] * 3)   # -(4 - 1), in $000
    # EBIT 35, net interest 3 -> non-operating = 33 - (35 - 3) = 1, i.e. excluding interest income
    assert d["hist_NonOpIncome"].value == pytest.approx([0.001] * 3)


def test_unreported_interest_means_no_extra_forecast_interest():
    """AAPL: no separate interest expense / income -> both stay inside non-operating
    income; forecast debt and cash rates are 0 (WACC unaffected)."""
    from lunoviq import market_inputs
    st = _st(revenue=[100, 100, 100], total_debt=[50, 50, 50])
    kd = DataPoint(0.055, "ratio", "t", "d")
    fin = market_inputs.financing(st, kd, offline=True)
    assert fin["fin_DebtRate"].value == 0.0 and fin["fin_CashRate"].value == 0.0
    assert fin["fin_RevolverRate"].value == 0.055
