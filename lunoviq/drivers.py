"""
Forecast drivers derived from the company's own history, replacing the
template's demo-company values. Each driver is a DataPoint (value may be a
5-year list) with the method recorded; policy bounds live in config [drivers].

Standard mechanical defaults, not forecasts of the analyst's view:
    revenue growth   recent CAGR fading linearly to the terminal growth rate by year 5
    capex            average capex / revenue over the available history
    working capital  DSO / DIO / DPO of the latest fiscal year, held flat
    asset lives      net PP&E / D&A (existing and new assets)
    tax basis PP&E   equal to book PP&E (no opening timing difference)
    payout           average dividends / net income
    debt             held flat (no scheduled repayment or new borrowing)
"""
from . import config
from .schema import AUTO, DataPoint

SRC = "SEC EDGAR companyfacts"
YEARS = 5


def _clamp(v, bounds):
    lo, hi = bounds
    return max(lo, min(hi, v))


def _dp(value, unit, st, method):
    return DataPoint(value, unit, SRC, "FY%s" % st.fiscal_years[-1], method, AUTO)


def revenue_growth(st, terminal):
    ys = [y for y in st.fiscal_years if st.value("revenue", y)]
    if len(ys) < 2:
        return None
    n = ys[-1] - ys[0]
    g0 = (st.value("revenue", ys[-1]) / st.value("revenue", ys[0])) ** (1 / n) - 1
    cap = config.get("drivers.growth_bounds", [-0.20, 0.40])
    g0c = _clamp(g0, cap)
    path = [round(g0c + (terminal - g0c) * t / YEARS, 4) for t in range(1, YEARS + 1)]
    note = "%d-yr revenue CAGR %.1f%%%s, linear fade to terminal %.1f%% by year 5" % (
        n, g0 * 100, " (capped to %.0f%%)" % (g0c * 100) if g0c != g0 else "", terminal * 100)
    return _dp(path, "ratio", st, note)


def working_capital_days(st):
    y = st.fiscal_years[-1]
    rev, cogs = st.value("revenue", y), st.value("cogs", y)
    out = {}
    for name, item, base, base_name in (("drv_DSO", "receivables", rev, "revenue"),
                                        ("drv_DIO", "inventory", cogs, "COGS"),
                                        ("drv_DPO", "payables", cogs, "COGS")):
        bal = st.value(item, y)
        if base and bal is not None:
            days = round(bal / base * 365, 1)
            out[name] = _dp(days, "days", st, "%s / %s x 365, FY%s, held flat" % (item, base_name, y))
        else:
            out[name] = _dp(0.0, "days", st, "%s or %s not reported -> 0 days" % (item, base_name))
    return out


def capex_pct(st):
    pairs = [(st.value("capex", y), st.value("revenue", y)) for y in st.fiscal_years]
    ratios = [c / r for c, r in pairs if c is not None and r]
    if not ratios:
        return None
    return _dp(round(sum(ratios) / len(ratios), 4), "ratio", st,
               "average capex / revenue over %d yrs (%s)" % (
                   len(ratios), ", ".join("%.1f%%" % (x * 100) for x in ratios)))


def asset_life(st):
    y = st.fiscal_years[-1]
    ppe, da = st.value("ppe_net", y), st.value("d_and_a", y)
    if not (ppe and da):
        return None
    raw = ppe / da
    life = round(_clamp(raw, config.get("drivers.asset_life_bounds", [5, 40])), 1)
    note = "net PP&E / D&A FY%s = %.1f yrs%s" % (y, raw, "" if life == round(raw, 1) else " (bounded)")
    return _dp(life, "years", st, note)


def tax_basis(st):
    vals = [None if st.value("ppe_net", y) is None else st.value("ppe_net", y) / 1000
            for y in st.fiscal_years]
    return _dp(vals, "USD 000s", st, "set equal to book net PP&E (no opening timing difference)")


def payout(st):
    ratios = []
    for y in st.fiscal_years:
        d, ni = st.value("dividends", y), st.value("net_income", y)
        if ni and ni > 0:
            ratios.append((d or 0) / ni)
    if not ratios:
        return _dp(0.0, "ratio", st, "no positive net income -> 0 payout")
    v = round(_clamp(sum(ratios) / len(ratios), [0.0, 1.5]), 4)
    return _dp(v, "ratio", st, "average dividends paid / net income over %d yrs" % len(ratios))


def build(st, terminal_growth):
    """{named_range: DataPoint}; list values fill multi-year ranges in order."""
    out = {}
    g = revenue_growth(st, terminal_growth)
    if g:
        out["gm_RevenueGrowth"] = g
    out.update(working_capital_days(st))
    for name, fn in (("drv_CapexPct", capex_pct), ("drv_Payout", payout)):
        dp = fn(st)
        if dp:
            out[name] = dp
    life = asset_life(st)
    if life:
        out["drv_LifeExisting"] = life
        out["drv_LifeNew"] = DataPoint(life.value, "years", SRC, life.as_of,
                                       "same as existing assets (%s)" % life.method, AUTO)
    if st.latest("ppe_net") is not None:
        out["hist_TaxBasisPPE"] = tax_basis(st)
    flat = "debt held flat - no scheduled repayment/borrowing (standard simplification)"
    out["drv_DebtRepayment"] = DataPoint(0.0, "USD 000s", "policy", "", flat, AUTO)
    out["drv_NewBorrowing"] = DataPoint(0.0, "USD 000s", "policy", "", flat, AUTO)
    return out
