"""
Market and cost-of-capital inputs, each derived from free data with its
method recorded. Policy parameters (beta window, credit spread, tax bounds)
come from config [wacc] so they are visible and adjustable, not buried in code.

The exit multiple is the company's industry EV/EBITDA (Damodaran, US). Terminal
growth stays at the template's standard 2.5% and is flagged for review.
"""
import statistics

from . import config, units
from .schema import AUTO, MISSING, DataPoint


def _returns(hist):
    by_month = {}
    for d, c in zip(hist.dates, hist.closes):
        by_month[d[:7]] = c                          # keep last observation per month
    months = sorted(by_month)
    return {m: by_month[m] / by_month[p] - 1 for p, m in zip(months, months[1:])}


def beta(stock_hist, market_hist, months):
    rs, rm = _returns(stock_hist), _returns(market_hist)
    common = sorted(set(rs) & set(rm))[-months:]
    if len(common) < 24:
        return DataPoint(None, "x", stock_hist.source, "", "fewer than 24 overlapping months", MISSING)
    x = [rm[m] for m in common]
    y = [rs[m] for m in common]
    b = statistics.covariance(x, y) / statistics.variance(x)
    lo, hi = config.get("wacc.beta_bounds", [0.3, 2.5])
    note = "OLS slope, %d monthly returns vs %s (%s to %s)" % (
        len(common), market_hist.ticker, common[0], common[-1])
    if config.get("wacc.beta_adjustment", "blume") == "blume":
        note += "; Blume-adjusted from raw %.4f (0.67 x raw + 0.33)" % b
        b = 0.67 * b + 0.33
    if not lo <= b <= hi:
        note += "; OUTSIDE bounds %s-%s, review" % (lo, hi)
    return DataPoint(round(b, 4), "x", stock_hist.source, common[-1], note, AUTO)


def effective_tax_rate(st):
    lo, hi = config.get("wacc.tax_rate_bounds", [0.0, 0.35])
    fallback = config.get("wacc.tax_rate_fallback", 0.21)
    rates = []
    for y in st.fiscal_years:
        tax, pre = st.value("income_tax_total", y), st.value("pretax_income", y)
        if tax is not None and pre and pre > 0:
            rates.append(tax / pre)
    if rates:
        avg = sum(rates) / len(rates)
        if lo <= avg <= hi:
            return DataPoint(round(avg, 4), "ratio", "SEC EDGAR companyfacts",
                             str(st.fiscal_years[-1]),
                             "avg IncomeTaxExpenseBenefit / pre-tax income, %d yrs" % len(rates), AUTO)
    return DataPoint(fallback, "ratio", "config wacc.tax_rate_fallback", "",
                     "effective rate unavailable or out of bounds -> statutory fallback", AUTO)


def interest_coverage(st):
    """EBIT / interest expense for the latest fiscal year (EBIT = reported operating income)."""
    y = st.fiscal_years[-1]
    ebit = st.value("operating_income", y)
    if ebit is None and st.value("pretax_income", y) is not None:
        ebit = st.value("pretax_income", y) + abs(st.value("interest_expense", y) or 0)
    interest = abs(st.value("interest_expense", y) or 0)
    if ebit is None:
        return None
    return float("inf") if interest == 0 else ebit / interest


def cost_of_debt(st, rf, ratings=None):
    """Pre-tax cost of debt = risk-free + default spread of the synthetic rating
    implied by interest coverage (Damodaran). Falls back to a flat spread if the
    rating table is unavailable."""
    cov = interest_coverage(st)
    if ratings and cov is not None:
        table, asof = ratings
        for lo, hi, rating, spread in table:
            if lo < cov <= hi or (cov == float("inf") and hi >= 100000):
                return DataPoint(round(rf.value + spread, 4), "ratio",
                                 "Damodaran synthetic rating (%s) + risk-free" % asof, rf.as_of,
                                 "interest coverage %.1fx -> %s, spread %.2f%%"
                                 % (min(cov, 99999), rating, spread * 100), AUTO)
    spread = config.get("wacc.credit_spread", 0.01)
    return DataPoint(round(rf.value + spread, 4), "ratio", "derived", rf.as_of,
                     "rating table unavailable -> risk-free + %.2f%% flat spread" % (spread * 100), AUTO)


def financing(st, kd, offline=False):
    """Financing-schedule inputs (replace the template's demo values):
        existing debt   effective rate = interest expense / average debt (latest FY)
        cash            3-month Treasury bill
        revolver        rate = pre-tax cost of debt; limit 10% of revenue
        minimum cash    2% of revenue (operating cash, Damodaran's rule of thumb)"""
    y = st.fiscal_years
    rev = st.latest("revenue") or 0
    out = {}
    interest = abs(st.value("interest_expense", y[-1]) or 0)
    debts = [d for d in ((st.value("total_debt", y[-2]) if len(y) > 1 else None), st.value("total_debt", y[-1])) if d]
    eff = interest / (sum(debts) / len(debts)) if (interest and debts) else None
    if not interest:
        # interest expense not reported separately (e.g. AAPL since FY2024): it is part of
        # "other income, net", i.e. already inside the non-operating line carried forward
        out["fin_DebtRate"] = DataPoint(0.0, "ratio", "policy", str(y[-1]),
                                        "interest expense not reported separately: it stays inside non-operating "
                                        "income, so no extra interest on debt (avoids double count); WACC unaffected", AUTO)
    elif eff is not None and 0.005 <= eff <= 0.15:
        out["fin_DebtRate"] = DataPoint(round(eff, 4), "ratio", "SEC EDGAR companyfacts", str(y[-1]),
                                        "interest expense / average total debt (effective rate on existing debt)", AUTO)
    else:
        out["fin_DebtRate"] = DataPoint(kd.value, "ratio", kd.source, kd.as_of,
                                        "effective rate unavailable or outside 0.5-15% -> pre-tax cost of debt", AUTO)
    if st.latest("interest_income"):
        try:
            from .providers.rates import treasury_short_rate
            out["fin_CashRate"] = treasury_short_rate(offline)
        except Exception:
            pass
    else:
        out["fin_CashRate"] = DataPoint(0.0, "ratio", "policy", str(y[-1]),
                                        "interest income not reported separately: it stays inside non-operating "
                                        "income, so no extra interest on cash (avoids double count)", AUTO)
    out["fin_RevolverRate"] = DataPoint(kd.value, "ratio", kd.source, kd.as_of, "revolver priced at pre-tax cost of debt", AUTO)
    out["fin_MinCash"] = DataPoint(round(units.scale(rev * 0.02), 1), units.label(), "derived", str(y[-1]),
                                   "2% of latest revenue (operating cash)", AUTO)
    out["fin_MaxRevolver"] = DataPoint(round(units.scale(rev * 0.10), 1), units.label(), "derived", str(y[-1]),
                                       "10% of latest revenue", AUTO)
    return out


def capital_weights(st, price):
    shares = st.latest("shares_diluted")
    debt = st.latest("total_debt") or 0.0
    if not (shares and price.value):
        m = DataPoint(None, "ratio", "derived", "", "price or share count missing", MISSING)
        return m, m, m
    equity = price.value * shares
    wd = debt / (debt + equity)
    asof = price.as_of
    mcap = DataPoint(equity, "USD", "derived", asof, "price x diluted shares (latest FY)", AUTO)
    return (mcap,
            DataPoint(round(wd, 4), "ratio", "derived", asof,
                      "book total debt / (debt + market cap)", AUTO),
            DataPoint(round(1 - wd, 4), "ratio", "derived", asof, "1 - debt weight", AUTO))


def build(st, prices, rf_providers, erp_provider, offline=False):
    """Return {named_range: DataPoint} for the valuation inputs."""
    out = {}
    q = prices.quote(st.ticker, offline=offline)
    out["ctl_SharePrice"] = q.price

    rf = None
    for p in rf_providers:
        try:
            rf = p.risk_free(offline=offline)
            break
        except Exception as e:                            # try the next source
            last = e
    if rf is None:
        raise RuntimeError("no risk-free source available: %s" % last)
    out["val_RiskFree"] = rf
    out["val_ERP"] = erp_provider.erp(offline=offline)

    months = config.get("wacc.beta_months", 60)
    index = config.get("wacc.market_index", "^GSPC")
    out["val_Beta"] = beta(prices.monthly_history(st.ticker, months, offline=offline),
                           prices.monthly_history(index, months, offline=offline), months)
    try:
        from .providers.rates import DamodaranRatingsProvider
        ratings = DamodaranRatingsProvider().table(offline=offline)
    except Exception:
        ratings = None
    out["val_CostOfDebt"] = cost_of_debt(st, rf, ratings)
    out.update(financing(st, out["val_CostOfDebt"], offline))
    out["drv_TaxRate"] = effective_tax_rate(st)
    mcap, wd, we = capital_weights(st, q.price)
    out["val_DebtWeight"], out["val_EquityWeight"] = wd, we
    out["_market_cap"] = mcap                             # informational, not written
    try:
        from .providers.industry import DamodaranIndustryProvider
        em = DamodaranIndustryProvider().ev_ebitda(st.ticker, offline=offline)
    except Exception:
        em = None
    if em:
        out["val_ExitMultiple"] = em
    return out
