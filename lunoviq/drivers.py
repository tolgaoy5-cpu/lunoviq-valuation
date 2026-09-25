"""
Forecast drivers derived from the company's own history, replacing the
template's demo-company values. Each driver is a DataPoint (value may be a
5-year list) with the method recorded; policy bounds live in config [drivers].

Standard mechanical defaults, not forecasts of the analyst's view:
    revenue growth   year 1 (and 2): the user's company-guidance input, else analyst consensus,
                     else the 5-yr revenue CAGR; then a linear fade to terminal growth by year 5
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
from .schema import AUTO, OVERRIDE, DataPoint

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


def _hist_cagr(st):
    ys = [y for y in st.fiscal_years if st.value("revenue", y)]
    if len(ys) < 2:
        return None, 0
    n = ys[-1] - ys[0]
    return (st.value("revenue", ys[-1]) / st.value("revenue", ys[0])) ** (1 / n) - 1, n


def consensus_check(st, consensus):
    """Consensus is used only if its last reported year is our latest fiscal year: its
    last actual revenue must match SEC revenue (same year, same revenue definition)."""
    if not consensus or consensus.get("y1_growth") is None:
        return (consensus or {}).get("error") or "no analyst estimate found"
    sec, theirs = st.latest("revenue"), consensus.get("last_revenue")
    if not sec or not theirs or abs(theirs / sec - 1) > config.get("drivers.consensus_match", 0.02):
        return ("estimates are based on a different year or revenue definition (their last FY %s revenue "
                "%s vs SEC FY%s %s)" % (consensus.get("last_fy_end"), "n/a" if not theirs else "%.0f" % theirs,
                                        st.fiscal_years[-1], "n/a" if not sec else "%.0f" % sec))
    return None


def growth_path(st, terminal, consensus=None, guidance=None):
    """Revenue growth for the 5 forecast years and the sources behind it.

    Year 1: company guidance entered by the user > analyst consensus > history.
    Year 2: analyst consensus if available. Later years fade linearly to terminal growth,
    reached in year 5. Without guidance or consensus this is the history-only path."""
    hist = revenue_growth(st, terminal)
    g0, n = _hist_cagr(st)
    problem = consensus_check(st, consensus)
    c1 = None if problem else consensus["y1_growth"]
    c2 = None if problem else consensus.get("y2_growth")
    sources = {"history": g0, "history_years": n, "consensus_y1": c1, "consensus_y2": c2,
               "analysts": None if problem else consensus.get("analysts"),
               "consensus_source": None if problem else consensus.get("source"),
               "consensus_note": problem, "guidance": guidance}
    if guidance is None and c1 is None:
        if hist is None:
            return None, sources
        sources["used"] = "history"
        return _dp(hist.value, "ratio", st, hist.method + "; no analyst estimate (%s) - review" % problem), sources

    y1, used = (guidance, "guidance") if guidance is not None else (c1, "consensus")
    gap = config.get("drivers.growth_gap_review", 0.10)
    if used == "guidance" and c1 is not None and abs(guidance - c1) > gap / 2:
        c2 = None             # consensus year 2 builds on consensus year 1, which the user rejected
    if c2 is not None:
        path = [y1, c2] + [c2 + (terminal - c2) * t / 3 for t in (1, 2, 3)]
        tail = "year 2 analyst consensus %.1f%%, then linear fade to terminal %.1f%% by year 5" % (c2 * 100, terminal * 100)
    else:
        path = [y1 + (terminal - y1) * t / 4 for t in range(5)]
        tail = "linear fade to terminal %.1f%% by year 5" % (terminal * 100)
    head = ("year 1 company guidance %.1f%% (user input)" % (y1 * 100) if used == "guidance" else
            "year 1 analyst consensus %.1f%% (%s analysts, %s)" % (y1 * 100, sources["analysts"] or "n/a",
                                                                   sources["consensus_source"]))
    note = head + ", " + tail
    flags = []
    if g0 is not None and abs(y1 - g0) > gap:
        flags.append("year 1 %.1f%% vs %d-yr history %.1f%%: large gap, check for acquisitions, "
                     "divestitures or one-offs" % (y1 * 100, n, g0 * 100))
    if used == "guidance" and c1 is not None and abs(guidance - c1) > gap / 2:
        flags.append("your guidance %.1f%% vs analyst consensus %.1f%%" % (guidance * 100, c1 * 100))
    if flags:
        note += " - review: " + "; ".join(flags)
    sources["used"], sources["flags"] = used, flags
    dp = DataPoint([round(v, 4) for v in path], "ratio", sources["consensus_source"] if used == "consensus"
                   else "user input (company guidance)", "FY%s" % st.fiscal_years[-1], note, AUTO)
    return dp, sources


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


def build(st, terminal_growth, st_long=None, consensus=None, guidance=None):
    """{named_range: DataPoint}; list values fill multi-year ranges in order.
    st_long: longer history (5 fiscal years) for averages and volatility.
    consensus: EstimatesProvider.revenue() result; guidance: user's year-1 growth.
    Entries starting with "_" are logged (09_Sources, run.json) but not written."""
    lng = st_long or st
    out = {}
    g, src = growth_path(lng, terminal_growth, consensus, guidance)
    if g:
        out["gm_RevenueGrowth"] = g
    fy = "FY%s" % st.fiscal_years[-1]
    for key, label in (("history", "%d-yr revenue CAGR (history)" % src["history_years"]),
                       ("consensus_y1", "analyst consensus, year 1"), ("consensus_y2", "analyst consensus, year 2"),
                       ("guidance", "company guidance, year 1 (user input)")):
        note = label + ("; used" if src.get("used") == key.split("_")[0] and key != "consensus_y2" else "")
        if key.startswith("consensus") and src["consensus_note"]:
            note = label + ": not available - " + src["consensus_note"]
        elif key.startswith("consensus") and src["analysts"]:
            note += "; %d analysts" % src["analysts"]
        if key == "guidance" and src[key] is None:
            continue
        if key.startswith("consensus"):
            source = src["consensus_source"] or "analyst consensus"
        else:
            source = "user input" if key == "guidance" else SRC
        out["_growth_" + key] = DataPoint(src[key], "ratio", source, fy, note,
                                          OVERRIDE if key == "guidance" else AUTO)
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
