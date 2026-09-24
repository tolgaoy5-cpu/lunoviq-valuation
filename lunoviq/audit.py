"""
Independent re-computation of the forecast (2026E-2030E) from the model's
inputs, in plain Python, compared line by line with Excel's results.

Nothing here reuses a workbook formula: each line is rebuilt from standard
accounting logic (growth-margin revenue engine, days-based working capital,
straight-line book / declining-balance tax depreciation, NOL usage, deferred
tax from the book-tax PP&E gap, beginning-balance interest, revolver/sweep,
three-statement articulation). A mismatch means either an Excel formula error
or a documented modelling difference.

Usage:
    python -m lunoviq.audit output/KO_.../KO_Model.xlsx
"""
import sys

import openpyxl

INP, OP, IS = "01_Inputs_Historicals", "02_Operating_Model", "03_3_Statement_Model"
FC_IN = "CDEFG"            # 01 forecast columns
FC = "EFGHI"               # 02/03 forecast columns
TOL = 1e-6


def _v(ws, ref):
    v = ws[ref].value
    return 0.0 if v is None or isinstance(v, str) else float(v)


def recompute(wb):
    i, op, st, dash = wb[INP], wb[OP], wb[IS], wb["00_Dashboard"]
    row = lambda r: [_v(i, "%s%d" % (c, r)) for c in FC_IN]
    scen = dash["B8"].value or "Base"
    k = {"Bear": "B", "Base": "C", "Bull": "D"}[scen]
    d_g, d_m = _v(i, "%s72" % k), _v(i, "%s74" % k)
    growth, cogs_m, sga_p, oth_p = row(100), row(101), row(43), row(44)
    dso, dio, dpo, capex_p = row(45), row(46), row(47), row(48)
    tax_r, payout, repay_in, borrow_in = row(52), row(53), row(54), row(55)
    nonop, mino = row(56), row(57)
    life_old, life_new, first = _v(i, "C49"), _v(i, "C50"), _v(i, "C51")
    kd, cash_r, rev_r = _v(i, "C79"), _v(i, "C82"), _v(i, "C83")    # C79: rate on existing debt (v7)
    min_cash, max_rev, eq_chg, sweep = _v(i, "C80"), _v(i, "C81"), _v(i, "C85"), _v(i, "C87")
    if _v(i, "C84") != 0:
        raise NotImplementedError("audit covers the default beginning-balance interest (circularity switch 0)")

    # 2025A opening balances
    rev0, cogs0 = _v(i, "D18"), _v(op, "D78")
    ar0, inv0, ap0 = _v(i, "D25"), _v(i, "D26"), _v(i, "D28")
    ppe0, basis0, dtl0, nol0 = _v(i, "D27"), _v(i, "D34"), _v(i, "D30"), _v(i, "D33")
    cash0, debt0, eq0, re0 = _v(i, "D24"), _v(i, "D29"), _v(i, "D31"), _v(i, "D32")
    oth_a, oth_l, oth_e = _v(st, "D56"), _v(st, "D63"), _v(st, "D69")

    out = {}
    rev, nwc_prev = rev0, ar0 + inv0 - ap0
    ppe, basis, dtl, nol = ppe0, basis0, dtl0, nol0
    cash, debt, revolver, eq, re = cash0, debt0, 0.0, eq0, re0
    capex_hist, old_dep = [], -ppe0 / life_old
    for t in range(5):
        c = FC[t]
        rev = rev * (1 + growth[t] + d_g)
        cogs = rev * cogs_m[t] * (1 - d_m)
        sga, oth = -rev * sga_p[t], -rev * oth_p[t]
        ebitda = rev - cogs + sga + oth
        ar, inv, ap = rev / 365 * dso[t], cogs / 365 * dio[t], cogs / 365 * dpo[t]
        wc_cash = -((ar + inv - ap) - nwc_prev)
        capex = rev * capex_p[t]
        capex_hist.append(capex)
        new_dep = -sum(cx / life_new * (first if v == t else 1.0) for v, cx in enumerate(capex_hist))
        dep = old_dep + new_dep
        ebit = ebitda + dep
        ppe_new = ppe + capex + dep
        tax_dep = -(basis * 0.15 + capex * 0.15 * 0.5)
        basis_new = basis + capex + tax_dep
        repay = -min(debt, repay_in[t])
        debt_new = debt + repay + borrow_in[t]
        int_lt = -(debt + debt_new) / 2 * kd
        int_rev = -revolver * rev_r
        int_cash = cash * cash_r
        net_int = int_lt + int_rev + int_cash
        ebt = ebit + nonop[t] + net_int
        adj = ebt - dep + tax_dep                  # add back book, deduct tax depreciation
        use = -min(max(0.0, adj), nol)
        taxable = max(0.0, adj + use)
        cur_tax = -taxable * tax_r[t]
        nol = max(0.0, nol + max(0.0, -adj) + use)
        dtl_new = max(0.0, dtl + ((ppe_new - basis_new) - (ppe - basis)) * tax_r[t])
        def_tax = -(dtl_new - dtl)
        tot_tax = cur_tax + def_tax
        minority = -mino[t] * (ebt + tot_tax)
        ni = ebt + tot_tax + minority
        div = -max(0.0, ni * payout[t])
        cfo = ni - dep - def_tax + wc_cash
        cfi = -capex
        pre = cash + cfo + cfi + (repay + borrow_in[t]) + eq_chg + div
        avail = pre - min_cash
        if avail < 0:
            draw = min(-avail, max_rev - revolver)
        else:
            draw = -min(avail, revolver) if sweep == 1 else 0.0
        cff = repay + borrow_in[t] + div + draw + eq_chg
        cash_new = cash + cfo + cfi + cff
        revolver += draw
        eq += eq_chg
        re += ni + div
        assets = oth_a + cash_new + ar + inv + ppe_new
        liab = oth_l + ap + debt_new + revolver + dtl_new
        equity = oth_e + eq + re
        out[c] = {
            (IS, 8): rev, (IS, 9): -cogs, (IS, 12): sga, (IS, 13): oth, (IS, 15): ebitda,
            (IS, 16): dep, (IS, 17): ebit, (IS, 18): nonop[t], (IS, 19): net_int, (IS, 20): ebt,
            (IS, 22): cur_tax, (IS, 23): def_tax, (IS, 24): tot_tax, (IS, 25): minority, (IS, 26): ni,
            (IS, 35): wc_cash, (IS, 36): cfo, (IS, 39): cfi, (IS, 45): div, (IS, 46): cff,
            (IS, 51): cash_new, (IS, 57): cash_new, (IS, 58): ar, (IS, 59): inv, (IS, 60): ppe_new,
            (IS, 61): assets, (IS, 64): ap, (IS, 65): debt_new + revolver, (IS, 66): dtl_new,
            (IS, 67): liab, (IS, 70): eq, (IS, 71): re, (IS, 72): equity, (IS, 74): liab + equity,
            (OP, 145): dep, (OP, 167): tax_dep, (OP, 168): basis_new, (OP, 211): nol,
        }
        nwc_prev, ppe, basis, dtl, cash, debt = ar + inv - ap, ppe_new, basis_new, dtl_new, cash_new, debt_new
    return out, scen


def historical_ratios(wb, statements):
    """07 ratios in historical years vs the same ratios computed straight from
    the reported (SEC) figures in run.json. Catches definition errors such as
    a current ratio that leaves out current liabilities."""
    items, years = statements["items"], statements["fiscal_years"]
    rep = lambda k, y: (items.get(k, {}).get(str(y)) or {}).get("value")
    an = wb["07_Analysis_Scenarios"]
    checks = {82: ("current ratio", lambda y: rep("current_assets", y) / rep("current_liabilities", y)),
              10: ("net margin", lambda y: rep("net_income", y) / rep("revenue", y)),
              18: ("ROE", lambda y: rep("net_income", y) / (rep("total_assets", y) - (rep("total_liabilities", y)
                                                             or rep("total_assets", y) - rep("total_equity", y))))}
    out = []
    for r, (name, fn) in checks.items():
        for col, y in zip("BCD", years):
            try:
                want = fn(y)
            except (TypeError, ZeroDivisionError):
                continue
            got = an["%s%d" % (col, r)].value
            if not isinstance(got, (int, float)) or abs(got - want) > 1e-6 * max(1, abs(want)):
                out.append((name, y, got, want))
    return out


def audit(path, run_json=None):
    wb = openpyxl.load_workbook(path, data_only=True)
    if wb[IS]["E8"].value is None:
        raise ValueError("%s has no calculated values (built with --no-recalc?)" % path)
    expected, scen = recompute(wb)
    rows, worst = [], 0.0
    for col, lines in expected.items():
        for (sheet, r), py in lines.items():
            xl = wb[sheet]["%s%d" % (col, r)].value
            xl = float(xl) if isinstance(xl, (int, float)) else None
            rel = abs(py - xl) / max(1.0, abs(xl)) if xl is not None else float("inf")
            worst = max(worst, rel)
            if rel > TOL:
                rows.append((sheet, "%s%d" % (col, r), wb[sheet]["A%d" % r].value, xl, py))
    balanced = all(abs(v[(IS, 61)] - v[(IS, 74)]) < 1e-3 for v in expected.values())
    ratio_issues = None
    if run_json:
        import json
        ratio_issues = historical_ratios(wb, json.load(open(run_json))["statements"])
    return {"scenario": scen, "lines_checked": sum(len(v) for v in expected.values()),
            "mismatches": rows, "worst_relative_diff": worst, "python_balance_sheet_balances": balanced,
            "historical_ratio_issues": ratio_issues}


if __name__ == "__main__":
    from pathlib import Path
    rj = Path(sys.argv[1]).with_name("run.json")
    res = audit(sys.argv[1], rj if rj.exists() else None)
    print("Scenario %s | %d line-years checked | worst rel. diff %.2e | Python BS balances: %s"
          % (res["scenario"], res["lines_checked"], res["worst_relative_diff"], res["python_balance_sheet_balances"]))
    print("Historical ratios vs SEC: %s" % ("not checked" if res["historical_ratio_issues"] is None
                                              else ("OK" if not res["historical_ratio_issues"]
                                                    else res["historical_ratio_issues"])))
    for m in res["mismatches"][:40]:
        print("  MISMATCH %-22s %-6s %-32s excel=%s python=%.2f" % (m[0], m[1], m[2], m[3], m[4]))
