"""
End-to-end run:  ticker -> providers -> schema -> Excel copy -> recalc -> validation.

Each run writes to output/<TICKER>_<YYYYMMDD-HHMM>/:
    <TICKER>_Model.xlsx   populated, recalculated model (with 09_Sources sheet)
    run.json              every input DataPoint, the statements, the outputs
"""
import datetime as dt
import json
import re
import tomllib
from pathlib import Path

import openpyxl

from . import config, drivers, market_inputs, peers as peer_sel, providers, units
from .excel import presentation, writer
from .providers import sec_xbrl
from .schema import AUTO, OVERRIDE, TEMPLATE, DataPoint

# Forecast drivers the legacy writer derives from the latest fiscal year
# (revenue growth is replaced by lunoviq.drivers).
HISTORY_DRIVERS = {
    "gm_CogsMargin": "last FY COGS / revenue, copied to all forecast years",
    "drv_SGApct": "last FY SG&A / revenue, copied to all forecast years",
    "drv_OtherOpExPct": "latest available other opex / revenue, copied to all forecast years",
    "val_Shares": "latest FY diluted weighted shares (000s)",
}


def _path(key, default):
    p = Path(config.get(key, default))
    return p if p.is_absolute() else config.ROOT / p


def load_overrides(ticker, cli_sets=()):
    """overrides/<TICKER>.toml [inputs] plus --set name=value (CLI wins)."""
    vals = {}
    f = _path("paths.overrides_dir", "overrides") / ("%s.toml" % ticker.upper())
    if f.exists():
        vals.update(tomllib.loads(f.read_text(encoding="utf-8")).get("inputs", {}))
    for s in cli_sets:
        k, v = s.split("=", 1)
        vals[k.strip()] = float(v)
    src = {k: ("CLI --set" if any(s.split("=")[0].strip() == k for s in cli_sets) else str(f.name))
           for k in vals}
    return {k: DataPoint(float(v), "", "user override (%s)" % src[k],
                         dt.date.today().isoformat(), "manual", OVERRIDE) for k, v in vals.items()}


STEPS = ["fundamentals", "market", "excel", "peers", "recalc", "audit"]


def run(ticker, facts_path=None, offline=False, recalc=None, sets=(), out_root=None, market=None,
        with_peers=True, progress=None, estimates=None):
    """Execute the pipeline. `market` / `estimates` let tests inject market inputs and analyst
    consensus (estimates are fetched only for live market data; False skips them);
    `progress(step)` is called as each stage starts (used by the web UI)."""
    step = progress or (lambda name: None)
    ticker = ticker.upper()
    years = config.get("pipeline.years", 3)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(out_root or _path("paths.output_dir", "output")) / ("%s_%s" % (ticker, stamp))
    out_dir.mkdir(parents=True, exist_ok=True)
    model = out_dir / ("%s_Model.xlsx" % re.sub(r"\W+", "_", ticker))
    template = _path("paths.template", "Lunoviq_Master_Financial_Model_v7.xlsx")

    # 1-2. data -> schema
    step("fundamentals")
    fundamentals = providers.get("fundamentals", facts_path=facts_path)
    st = fundamentals.financials(ticker, years, offline=offline)
    st_long = fundamentals.financials(ticker, config.get("pipeline.history_years", 5), offline=offline)
    units.set_div(units.choose(st.latest("revenue")))
    try:
        return _run(ticker, st, st_long, offline, recalc, sets, out_dir, model, template, market, with_peers, step,
                    estimates)
    finally:
        units.set_div(units.DEFAULT)


def _consensus(ticker, offline):
    try:
        return providers.get("estimates").revenue(ticker, offline=offline)
    except Exception as e:                        # optional input: fall back to history
        return {"error": "analyst estimates unavailable (%s)" % str(e)[:120]}


def _run(ticker, st, st_long, offline, recalc, sets, out_dir, model, template, market, with_peers, step,
         estimates=None):
    step("market")
    if market is None:
        market = market_inputs.build(st, providers.get("prices"), providers.get("risk_free"),
                                     providers.get("equity_risk_premium"), offline=offline)
        if estimates is None:
            estimates = _consensus(ticker, offline)

    step("excel")
    # 3. historicals + history-based drivers (existing, tested writer) on a copy
    raw = st.raw
    sec_xbrl.write_model(str(template), str(model), st.company, st.cik,
                         raw["series"], raw["used"], raw["years"], div=units.DIV)

    # 4. market inputs, review flags and overrides through named ranges
    wb = openpyxl.load_workbook(model)
    log = []
    for name, note in HISTORY_DRIVERS.items():
        v = writer.read_named(wb, name)[0]
        log.append((name, DataPoint(v, "", "SEC EDGAR companyfacts", str(st.fiscal_years[-1]),
                                    note + " - placeholder, review", AUTO), "(set by sec_xbrl.write_model)"))
    inputs = dict(market)
    inputs.update(writer.template_defaults(wb, config.get("pipeline.review_inputs", [])))
    overrides = load_overrides(ticker, sets)
    terminal = (overrides.get("val_TerminalGrowth") or inputs["val_TerminalGrowth"]).value
    guidance = overrides.pop("drv_Year1Growth", None)
    inputs.update(drivers.build(st, terminal, st_long, consensus=estimates or None,
                                guidance=guidance.value if guidance else None))
    inputs.update(overrides)
    log += writer.apply_inputs(wb, inputs)

    # comparables, precedent transactions, unit-economics rows
    peer_list, skipped, peer_src = ([], [], "not run")
    if with_peers:
        step("peers")
        peer_list, skipped, peer_src = peer_sel.select(ticker, st.latest("revenue"), st.fiscal_years[-1],
                                                       offline=offline)
        presentation.write_peers(wb, peer_list)
        presentation.write_benchmarks(wb, ticker, peer_list)
    presentation.clear_precedents(wb)
    presentation.hide_unit_economics(wb)
    presentation.tidy(wb)
    presentation.relabel_units(wb)
    presentation.compact_style(wb)
    log.append(("comps_peers", DataPoint(", ".join(p["ticker"] for p in peer_list) or None, "", peer_src,
                                         dt.date.today().isoformat(),
                                         "EV/EBITDA: " + ", ".join("%s %.1fx" % (p["ticker"], p["ev"] / p["ebitda"])
                                                                   for p in peer_list if p.get("ebitda") and p.get("ev"))
                                         + ("; skipped: " + "; ".join(skipped) if skipped else ""),
                                         AUTO), "06_Comparable_Valuation!A7:K10"))
    log.append(("precedents", DataPoint(None, "", "none", "", "no free M&A transaction source; method shown as "
                                        "n/a - enter deals in 06!A41:J45 to activate", TEMPLATE), "06!A41:J45"))
    writer.write_log(wb, log, "%s (%s) | generated %s | template %s"
                     % (st.company, ticker, dt.datetime.now().strftime("%Y-%m-%d %H:%M"), template.name))
    presentation.print_setup(wb)                  # after 09_Sources exists
    wb.save(model)

    # 5-6. recalculation and validation
    result = {"ticker": ticker, "company": st.company, "model": str(model), "outputs": None,
              "units": units.label(), "unit_div": units.DIV,
              "missing_items": st.missing()}
    if recalc if recalc is not None else config.get("pipeline.recalculate", True):
        from .excel.recalc import recalc as excel_recalc
        from .excel.reader import outputs
        step("recalc")
        excel_recalc(model)
        result["outputs"] = outputs(model)

    result["peers"] = [{k: p.get(k) for k in ("ticker", "name", "ev", "market_cap", "revenue", "ebitda",
                                              "net_income", "rationale")} for p in peer_list]
    result["peer_source"] = peer_src
    result["peers_skipped"] = skipped
    record = dict(result, generated=dt.datetime.now().isoformat(timespec="seconds"),
                  template=str(template),
                  inputs={k: v.to_dict() for k, v in inputs.items()},
                  statements=st.to_dict())
    run_json = out_dir / "run.json"
    run_json.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    result["run_json"] = str(run_json)
    if result["outputs"]:
        step("audit")
        from . import audit as audit_mod, summary
        a = audit_mod.audit(model, run_json)
        record["audit"] = result["audit"] = {
            "lines_checked": a["lines_checked"], "mismatches": len(a["mismatches"]),
            "balance_sheet_balances": a["python_balance_sheet_balances"],
            "historical_ratio_issues": a["historical_ratio_issues"]}
        run_json.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        result["summary"] = str(summary.write(model, run_json)[0])
    return result
