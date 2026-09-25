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
        row["exit_multiple"] = out.get("exit_multiple")
        row["comps"] = out["valuation"].get("Trading Comparables")
        row["errors"] = out.get("errors")
        row["dcf_checks"] = out.get("dcf_checks")
    if recalc:
        from lunoviq.audit import audit
        a = audit(res["model"], res["run_json"])
        row["audit"] = {"lines": a["lines_checked"], "mismatches": len(a["mismatches"]),
                        "ratio_issues": a["historical_ratio_issues"]}
    row["peers"] = [p["ticker"] for p in rec.get("peers", [])]
    row["peers_skipped"] = rec.get("peers_skipped")
    g = inp.get("gm_RevenueGrowth") or {}
    row["growth"] = g.get("value")
    row["growth_method"] = g.get("method", "")
    row["growth_history"] = (inp.get("_growth_history") or {}).get("value")
    row["growth_consensus"] = (inp.get("_growth_consensus_y1") or {}).get("value")
    row["nonop"] = (inp.get("val_NonOpAssets") or {}).get("value")
    row["industry"] = (inp.get("val_ExitMultiple") or {}).get("method", "")
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
            c = r.get("dcf_checks") or {}
            f = lambda v, pct=False: "-" if v is None else ("%.2f%%" % (v * 100) if pct else "%.2f" % v)
            au = r.get("audit") or {}
            print("%-6s audit %s/%s mism, ratios %s | " % (t, au.get("mismatches"), au.get("lines"),
                                                          "OK" if au.get("ratio_issues") == [] else au.get("ratio_issues")), end="")
            print("%-6s px %7.2f | WACC %s | DCF %7s | exit %7s | comps %7s [%s] | impl.g %s | EV ok %s | errors %d | health %s"
                  % (t, r["ctl_SharePrice"] or 0, f(r.get("wacc"), True), f(r.get("dcf")), f(r.get("exit")),
                     f(r.get("comps")) if isinstance(r.get("comps"), (int, float)) else r.get("comps"),
                     ",".join(r["peers"]), f(c.get("implied_terminal_growth"), True), c.get("ev_match"),
                     len(r.get("errors") or []), r.get("health")), flush=True)
            gr = r.get("growth") or []
            print("       growth %s | hist %s cons %s | %s" % (" ".join("%.1f%%" % (x * 100) for x in gr),
                  f(r.get("growth_history"), True), f(r.get("growth_consensus"), True),
                  "REVIEW" if "review" in r.get("growth_method", "") else "ok"), flush=True)
    out = ROOT / "output" / ("batch_check_%s.json" % dt.datetime.now().strftime("%Y%m%d-%H%M"))
    out.write_text(json.dumps(rows, indent=2, default=str))
    print("\nSaved", out)


if __name__ == "__main__":
    main()
