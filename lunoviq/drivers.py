"""
Forecast drivers derived from the company's own history, replacing the
template's demo-company values. Each driver is a DataPoint (value may be a
5-year list) with the method recorded; policy bounds live in config [drivers].

Standard mechanical defaults, not forecasts of the analyst's view:
    revenue growth   5-yr revenue CAGR fading linearly to the terminal growth rate by year 5
    capex            average capex / revenue over 5 years
    scenarios        bear/bull = -/+ one standard deviation of the company's own history
    working capital  DSO / DIO / DPO of the latest fiscal year, held flat
    asset lives      net PP&E / D&A (existing and new assets)
    tax basis PP&E   equal to book PP&E (no opening timing difference)
    payout           average dividends / net income
    equity bridge    non-operating investments and minority interest (latest balance)
    below EBIT       non-operating income, deferred tax, minority share (net income
                     reconciles to the reported figure in every historical year)
    debt             held flat (no scheduled repayment or new borrowing)
"""
from . import config, units
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
    vals = [None if st.value("ppe_net", y) is None else units.scale(st.value("ppe_net", y))
            for y in st.fiscal_years]
    return _dp(vals, units.label(), st, "set equal to book net PP&E (no opening timing difference)")


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


def below_ebit(st):
    """Historical lines between EBIT and reported net income, in the model's
    sign convention ($000; income +, expense -), so that the model's net
    income equals the reported figure in every historical year:
        EBT      = EBIT - interest + non-operating income  (= reported pre-tax)
        tax      = -current tax + deferred tax             (= -reported total tax)
        minority = reported NI - (EBT + tax)               (NCI and anything else)
    """
    k = units.scale
    nonop, deferred, minority, pre_nci, net_int = [], [], [], [], []
    for y in st.fiscal_years:
        v = lambda key: st.value(key, y)
        rev = v("revenue")
        if rev is None:
            return None
        ebit = rev - sum(v(x) or 0 for x in ("cogs", "sga", "other_opex", "d_and_a"))
        # net interest, like the forecast line (debt interest - interest earned on cash)
        interest = abs(v("interest_expense") or 0) - (v("interest_income") or 0)
        pretax, total_tax, cur = v("pretax_income"), v("income_tax_total"), v("current_tax") or 0
        n = (pretax - (ebit - interest)) if pretax is not None else 0.0
        d = -(total_tax - cur) if total_tax is not None else 0.0
        ebt, tax = ebit - interest + n, -cur + d
        ni = v("net_income")
        m = (ni - (ebt + tax)) if ni is not None else 0.0
        nonop.append(k(n)); deferred.append(k(d)); minority.append(k(m)); pre_nci.append(ebt + tax)
        net_int.append(k(-interest))
    avg_nonop = sum(nonop) / len(nonop)
    shares = [-m * units.DIV / p for m, p in zip(minority, pre_nci) if p > 0]
    pct = _clamp(sum(shares) / len(shares), [0.0, 0.3]) if shares else 0.0
    fy = "FY%s-FY%s" % (st.fiscal_years[0], st.fiscal_years[-1])
    has_ii = any(st.value("interest_income", y) for y in st.fiscal_years)
    return {
        "hist_InterestExpense": _dp(net_int, units.label(), st,
                                    "net interest = -(interest expense - interest income), %s%s"
                                    % (fy, "" if has_ii else "; interest income not reported separately")),
        "hist_NonOpIncome": _dp(nonop, units.label(), st,
                                "reported pre-tax income - (EBIT - net interest), %s" % fy),
        "hist_DeferredTax": _dp(deferred, units.label(), st, "-(reported total tax - current tax), %s" % fy),
        "hist_MinorityShare": _dp(minority, units.label(), st,
                                  "reported net income - (EBT + tax): minority interest & other, %s" % fy),
        "drv_NonOpIncome": _dp([round(avg_nonop, 1)] * YEARS, units.label(), st,
                               "average historical non-operating income, held flat - review"),
        "drv_MinorityPct": _dp([round(pct, 4)] * YEARS, "ratio", st,
                               "average minority share of pre-minority net income (bounded 0-30%)"),
    }


def scenario_deltas(st):
    """Bear/bull ranges from the company's own history (one standard deviation):
    revenue growth +/- sigma of annual growth; COGS margin +/- relative sigma."""
    import statistics
    ys = [y for y in st.fiscal_years if st.value("revenue", y)]
    growth = [st.value("revenue", b) / st.value("revenue", a) - 1 for a, b in zip(ys, ys[1:])]
    out = {}
    if len(growth) >= 2:
        sg = round(_clamp(statistics.stdev(growth), [0.01, 0.10]), 4)
        out["scn_RevGrowthDelta"] = _dp([-sg, 0.0, sg], "ratio", st,
                                        "bear/bull = -/+ 1 st.dev. of annual revenue growth over %d yrs (bounded 1-10%%)"
                                        % len(growth))
    margins = [st.value("cogs", y) / st.value("revenue", y) for y in ys if st.value("cogs", y)]
    if len(margins) >= 3:
        sm = round(_clamp(statistics.stdev(margins) / statistics.mean(margins), [0.01, 0.10]), 4)
        out["scn_CogsMarginDelta"] = _dp([-sm, 0.0, sm], "ratio", st,
                                         "bear/bull = +/- 1 relative st.dev. of COGS margin over %d yrs (bounded 1-10%%)"
                                         % len(margins))
    return out


def current_items(st):
    """Current assets / liabilities not modelled as separate lines, so the
    analysis sheet's current and quick ratios match the reported balance sheet."""
    oca, ocl = [], []
    for y in st.fiscal_years:
        v = lambda k: st.value(k, y)
        ca, cl = v("current_assets"), v("current_liabilities")
        oca.append(None if ca is None else units.scale(ca - sum(v(k) or 0 for k in ("cash", "receivables", "inventory"))))
        ocl.append(None if cl is None else units.scale(cl - (v("payables") or 0)))
    out = {}
    if any(x is not None for x in oca):
        out["hist_OtherCurrentAssets"] = _dp(oca, units.label(), st, "AssetsCurrent - (cash + receivables + inventory)")
    if any(x is not None for x in ocl):
        out["hist_OtherCurrentLiab"] = _dp(ocl, units.label(), st, "LiabilitiesCurrent - accounts payable")
    return out


def build(st, terminal_growth, st_long=None):
    """{named_range: DataPoint}; list values fill multi-year ranges in order.
    st_long: longer history (5 fiscal years) for averages and volatility."""
    lng = st_long or st
    out = {}
    g = revenue_growth(lng, terminal_growth)
    if g:
        out["gm_RevenueGrowth"] = g
    out.update(working_capital_days(st))
    for name, fn in (("drv_CapexPct", capex_pct), ("drv_Payout", payout)):
        dp = fn(lng)
        if dp:
            out[name] = dp
    life = asset_life(st)
    if life:
        out["drv_LifeExisting"] = life
        out["drv_LifeNew"] = DataPoint(life.value, "years", SRC, life.as_of,
                                       "same as existing assets (%s)" % life.method, AUTO)
    if st.latest("ppe_net") is not None:
        out["hist_TaxBasisPPE"] = tax_basis(st)
    for name, key, what in (("val_NonOpAssets", "nonop_investments",
                              "non-operating investments (equity-method stakes, long-term securities)"),
                             ("val_MinorityInterest", "minority_interest", "non-controlling interest")):
        v = st.latest(key)
        method = st.items.get(key, {}).get(st.fiscal_years[-1])
        out[name] = _dp(0.0 if v is None else units.scale(v), units.label(), st,
                        "%s, FY%s balance; %s" % (what, st.fiscal_years[-1],
                                                 method.method if method and v is not None else "not reported -> 0"))
    out.update(below_ebit(st) or {})
    out.update(current_items(st))
    out.update(scenario_deltas(lng))
    flat = "debt held flat - no scheduled repayment/borrowing (standard simplification)"
    out["drv_DebtRepayment"] = DataPoint(0.0, units.label(), "policy", "", flat, AUTO)
    out["drv_NewBorrowing"] = DataPoint(0.0, units.label(), "policy", "", flat, AUTO)
    return out
