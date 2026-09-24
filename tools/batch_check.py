"""
Multi-company robustness check: runs the full pipeline for a list of tickers
and reports data quality, model health and valuation vs market price.

Usage:
    python tools/batch_check.py KO PEP AAPL MSFT ... [--no-recalc]
Writes output/batch_check_<timestamp>.json alongside the console table.
"""
import argparse
import datetime as dt
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lunoviq import pipeline  # noqa: E402
from lunoviq.config import ROOT  # noqa: E402

DEFAULT = ["KO", "PEP", "AAPL", "MSFT", "NVDA", "WMT", "MNST", "JNJ", "XOM", "GOOGL", "AMZN", "TSLA"]


def check(ticker, recalc):
    res = pipeline.run(ticker, recalc=recalc)
    rec = json.loads(Path(res["run_json"]).read_text())
    items = rec["statements"]["items"]
    years = rec["statements"]["fiscal_years"]
    y = str(years[-1])

    def v(key, yr=y):
        return (items.get(key, {}).get(yr) or {}).get("value")

    row = {"ticker": ticker, "company": rec["company"][:28], "years": years,
           "missing": rec["missing_items"]}
    # data checks against reported totals
    parts_a = [v(k) for k in ("cash", "receivables", "inventory", "ppe_net", "other_assets")]
    row["assets_tie"] = (None if None in parts_a or v("total_assets") is None
                         else abs(sum(parts_a) - v("total_assets")) < 1)
    inp = rec["inputs"]
    for k in ("ctl_SharePrice", "val_RiskFree", "val_ERP", "val_Beta", "val_CostOfDebt",
              "val_DebtWeight", "drv_TaxRate"):
        row[k] = (inp.get(k) or {}).get("value")
    row["beta_note"] = (inp.get("val_Beta") or {}).get("method", "")
    row["reported_ni"] = v("net_income")
    out = rec.get("outputs")
    if out:
        row["wacc"] = out["wacc"]
        row["dcf"] = out["valuation"].get("DCF Perpetuity")
        row["exit"] = out["valuation"].get("DCF Exit Multiple")
        row["health"] = out["health"].get("Overall")
        row["flags"] = out["checks_flagged"]
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", default=DEFAULT)
    ap.add_argument("--no-recalc", action="store_true")
    a = ap.parse_args()
    rows = []
    for t in a.tickers or DEFAULT:
        try:
            rows.append(check(t, not a.no_recalc))
        except (Exception, SystemExit) as e:          # legacy engine exits on fatal data gaps
            rows.append({"ticker": t, "error": "%s: %s" % (type(e).__name__, e),
                         "trace": traceback.format_exc(limit=3)})
        r = rows[-1]
        if "error" in r:
            print("%-6s ERROR %s" % (t, r["error"][:150]))
        else:
            print("%-6s px %8.2f  beta %5.2f  kd %5.2f%%  wd %5.1f%%  tax %5.1f%%  WACC %s  DCF %s  health %s  miss %s"
                  % (t, r["ctl_SharePrice"] or 0, r["val_Beta"] or 0, (r["val_CostOfDebt"] or 0) * 100,
                     (r["val_DebtWeight"] or 0) * 100, (r["drv_TaxRate"] or 0) * 100,
                     "%.2f%%" % (r["wacc"] * 100) if r.get("wacc") else "-",
                     "%.2f" % r["dcf"] if isinstance(r.get("dcf"), (int, float)) else r.get("dcf"),
                     r.get("health"), ",".join(r["missing"]) or "-"), flush=True)
    out = ROOT / "output" / ("batch_check_%s.json" % dt.datetime.now().strftime("%Y%m%d-%H%M"))
    out.write_text(json.dumps(rows, indent=2, default=str))
    print("\nSaved", out)


if __name__ == "__main__":
    main()
