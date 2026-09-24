"""
Independent checks on a recalculated model, computed in Python from the
workbook's own cash flows (no Excel formulas reused):

  * recomputed enterprise value vs Excel's -> proves the DCF arithmetic
  * reverse DCF: the terminal growth rate, and separately the WACC, at which
    the DCF would equal today's share price ("what is the market pricing in?")
"""


def _ev(ufcf, terminal_fcf, wacc, g):
    pv = sum(cf / (1 + wacc) ** t for t, cf in enumerate(ufcf, 1))
    tv = terminal_fcf * (1 + g) / (wacc - g)
    return pv + tv / (1 + wacc) ** len(ufcf)


def dcf_checks(wb):
    d = wb["04_DCF_Valuation"]
    ufcf = [d["%s11" % c].value for c in "BCDEF"]
    terminal = d["G11"].value
    wacc, g = d["B29"].value, d["B28"].value
    net_debt, shares = d["E34"].value, d["H29"].value
    price = wb["00_Dashboard"]["B10"].value
    if None in ufcf + [terminal, wacc, g, net_debt, shares, price] or not shares:
        return None
    ev_py = _ev(ufcf, terminal, wacc, g)
    ev_xl = d["E30"].value
    target = price * shares + net_debt                   # market EV implied by the share price

    pv_discrete = sum(cf / (1 + wacc) ** t for t, cf in enumerate(ufcf, 1))
    tv_needed = (target - pv_discrete) * (1 + wacc) ** len(ufcf)
    implied_g = (tv_needed * wacc - terminal) / (tv_needed + terminal) if tv_needed + terminal else None

    implied_wacc = None
    lo, hi = g + 1e-4, 0.60
    if terminal > 0 and _ev(ufcf, terminal, lo, g) > target > _ev(ufcf, terminal, hi, g):
        for _ in range(100):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _ev(ufcf, terminal, mid, g) > target else (lo, mid)
        implied_wacc = (lo + hi) / 2
    return {
        "ev_excel": ev_xl, "ev_python": ev_py,
        "ev_match": ev_xl is not None and abs(ev_py - ev_xl) <= 1e-6 * max(1.0, abs(ev_xl)),
        "market_ev": target,
        "implied_terminal_growth": implied_g,
        "implied_wacc": implied_wacc,
    }
