"""
Command line:

    python -m lunoviq run KO
    python -m lunoviq run KO --set val_TerminalGrowth=0.02 --set val_ExitMultiple=18
    python -m lunoviq run KO --facts ko.json --no-recalc      # offline fundamentals
"""
import argparse
import sys

from . import pipeline


def _fmt(v):
    if isinstance(v, float):
        return "%.4f" % v if abs(v) < 1 else "{:,.2f}".format(v)
    return str(v)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lunoviq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="build a populated, recalculated model for a ticker")
    r.add_argument("ticker")
    r.add_argument("--facts", help="offline SEC companyfacts JSON")
    r.add_argument("--offline", action="store_true", help="use cached HTTP responses only")
    r.add_argument("--no-recalc", action="store_true", help="skip Excel recalculation")
    r.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                   help="override a named-range input, e.g. val_Beta=0.6")
    a = ap.parse_args(argv)

    res = pipeline.run(a.ticker, facts_path=a.facts, offline=a.offline,
                       recalc=False if a.no_recalc else None, sets=a.set)
    print("Company : %s (%s)" % (res["company"], res["ticker"]))
    print("Model   : %s" % res["model"])
    print("Record  : %s" % res["run_json"])
    if res["missing_items"]:
        print("Missing : %s" % ", ".join(res["missing_items"]))
    out = res["outputs"]
    if out:
        print("\nWACC %.2f%% | terminal growth %.2f%%" % (out["wacc"] * 100, out["terminal_growth"] * 100))
        for k, v in out["valuation"].items():
            print("  %-24s %s" % (k, _fmt(v)))
        print("Health  : %s" % ", ".join("%s=%s" % kv for kv in out["health"].items()))
        if out["checks_flagged"]:
            print("CHECK flags:\n  " + "\n  ".join(out["checks_flagged"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
