"""
Everything the web UI shows for one run, read from the recalculated workbook
(the workbook stays the single source of truth) plus run.json metadata.
Written next to the model as summary.json.
"""
import json
from pathlib import Path

import openpyxl

YEARS = "BCDEFGHI"            # 03 columns: 3 historical + 5 forecast years
DCF_YEARS = "BCDEF"           # 04 columns: 5 forecast years


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def build(model_path, run_json=None):
    wb = openpyxl.load_workbook(model_path, data_only=True)
    rec = json.loads(Path(run_json).read_text()) if run_json else {}
    st, dcf, dash = wb["03_3_Statement_Model"], wb["04_DCF_Valuation"], wb["00_Dashboard"]
    sens, comp, an = wb["05_Sensitivity"], wb["06_Comparable_Valuation"], wb["07_Analysis_Scenarios"]
    row = lambda ws, r, cols: [_num(ws["%s%d" % (c, r)].value) for c in cols]

    years = [str(st["%s4" % c].value) for c in YEARS]
    fin = {name: row(st, r, YEARS) for name, r in (
        ("revenue", 8), ("ebitda", 15), ("ebit", 17), ("net_income", 26), ("cash", 57),
        ("debt", 65), ("equity", 72))}
    fin["ufcf"] = [None, None, None] + row(dcf, 11, DCF_YEARS)

    methods = []
    for r, key in ((37, "dcf_perpetuity"), (38, "dcf_exit"), (39, "trading_comps"), (40, "precedents")):
        methods.append({"key": key, "label": dash["A%d" % r].value,
                        "value": _num(dash["B%d" % r].value),
                        "low": _num(dash["D%d" % r].value), "high": _num(dash["F%d" % r].value)})

    grid = {"wacc": [_num(sens["A%d" % r].value) for r in range(7, 13)],
            "growth": [_num(sens["%s6" % c].value) for c in "BCDEFG"],
            "price": [[_num(sens["%s%d" % (c, r)].value) for c in "BCDEFG"] for r in range(7, 13)]}

    peers = []
    for r in range(7, 11):
        name = comp["A%d" % r].value
        if name:
            peers.append({"name": name, "ev_revenue": _num(comp["F%d" % r].value),
                          "ev_ebitda": _num(comp["G%d" % r].value), "pe": _num(comp["H%d" % r].value),
                          "rationale": comp["J%d" % r].value})

    bench = []
    for r in range(112, 125):
        bench.append({"metric": an["A%d" % r].value, "company": _num(an["B%d" % r].value),
                      "low": _num(an["C%d" % r].value), "median": _num(an["D%d" % r].value),
                      "high": _num(an["E%d" % r].value), "position": an["G%d" % r].value})

    outputs = rec.get("outputs") or {}
    inputs = rec.get("inputs", {})
    review = [{"name": k, "value": v.get("value"), "method": v.get("method"), "status": v.get("status")}
              for k, v in inputs.items() if v.get("status") in ("template_default", "override")
              or "review" in (v.get("method") or "")]
    return {
        "ticker": rec.get("ticker"), "company": rec.get("company"), "generated": rec.get("generated"),
        "model": str(model_path), "units": "USD thousands",
        "price": _num(dash["B41"].value), "shares": _num(dcf["H29"].value),
        "selected": _num(dash["B42"].value), "scenario": dash["B8"].value,
        "years": years, "financials": fin,
        "wacc": {"wacc": _num(dcf["B18"].value), "cost_of_equity": _num(dcf["B16"].value),
                 "equity_weight": _num(dcf["C16"].value), "after_tax_kd": _num(dcf["B17"].value),
                 "debt_weight": _num(dcf["C17"].value), "terminal_growth": _num(dcf["B19"].value),
                 "risk_free": (inputs.get("val_RiskFree") or {}).get("value"),
                 "erp": (inputs.get("val_ERP") or {}).get("value"),
                 "beta": (inputs.get("val_Beta") or {}).get("value"),
                 "pretax_kd": (inputs.get("val_CostOfDebt") or {}).get("value"),
                 "tax_rate": (inputs.get("drv_TaxRate") or {}).get("value")},
        "bridge": {"pv_discrete": _num(dcf["E28"].value), "pv_terminal": _num(dcf["E29"].value),
                   "ev": _num(dcf["E30"].value), "nonop": _num(dcf["E31"].value),
                   "minority": _num(dcf["E32"].value), "net_debt_adj": _num(dcf["E34"].value),
                   "equity": _num(dcf["H28"].value), "exit_multiple": _num(dcf["B30"].value)},
        "methods": methods, "grid": grid, "peers": peers, "peer_source": rec.get("peer_source"),
        "peers_skipped": rec.get("peers_skipped", []), "benchmarks": bench,
        "health": outputs.get("health", {}), "errors": outputs.get("errors", []),
        "dcf_checks": outputs.get("dcf_checks"), "audit": rec.get("audit"),
        "missing_items": rec.get("missing_items", []), "review": review,
        "inputs": {k: {"value": v.get("value"), "source": v.get("source"), "as_of": v.get("as_of"),
                       "method": v.get("method"), "status": v.get("status")} for k, v in inputs.items()},
    }


def write(model_path, run_json):
    data = build(model_path, run_json)
    out = Path(model_path).with_name("summary.json")
    out.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    return out, data
