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

from . import config, market_inputs, providers
from .excel import writer
from .providers import sec_xbrl
from .schema import AUTO, OVERRIDE, DataPoint

# Forecast drivers the legacy writer derives from the latest fiscal year.
HISTORY_DRIVERS = {
    "gm_RevenueGrowth": "last FY revenue growth, copied to all forecast years",
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


def run(ticker, facts_path=None, offline=False, recalc=None, sets=(), out_root=None, market=None):
    """Execute the pipeline. `market` lets tests inject market inputs."""
    ticker = ticker.upper()
    years = config.get("pipeline.years", 3)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = Path(out_root or _path("paths.output_dir", "output")) / ("%s_%s" % (ticker, stamp))
    out_dir.mkdir(parents=True, exist_ok=True)
    model = out_dir / ("%s_Model.xlsx" % re.sub(r"\W+", "_", ticker))
    template = _path("paths.template", "Lunoviq_Master_Financial_Model_v2.xlsx")

    # 1-2. data -> schema
    st = providers.get("fundamentals", facts_path=facts_path).financials(ticker, years, offline=offline)
    if market is None:
        market = market_inputs.build(st, providers.get("prices"), providers.get("risk_free"),
                                     providers.get("equity_risk_premium"), offline=offline)

    # 3. historicals + history-based drivers (existing, tested writer) on a copy
    raw = st.raw
    sec_xbrl.write_model(str(template), str(model), st.company, st.cik,
                         raw["series"], raw["used"], raw["years"])

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
    inputs.update(overrides)
    log += writer.apply_inputs(wb, inputs)
    writer.write_log(wb, log, "%s (%s) | generated %s | template %s"
                     % (st.company, ticker, dt.datetime.now().strftime("%Y-%m-%d %H:%M"), template.name))
    wb.save(model)

    # 5-6. recalculation and validation
    result = {"ticker": ticker, "company": st.company, "model": str(model), "outputs": None,
              "missing_items": st.missing()}
    if recalc if recalc is not None else config.get("pipeline.recalculate", True):
        from .excel.recalc import recalc as excel_recalc
        from .excel.reader import outputs
        excel_recalc(model)
        result["outputs"] = outputs(model)

    record = dict(result, generated=dt.datetime.now().isoformat(timespec="seconds"),
                  template=str(template),
                  inputs={k: v.to_dict() for k, v in inputs.items()},
                  statements=st.to_dict())
    (out_dir / "run.json").write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    result["run_json"] = str(out_dir / "run.json")
    return result
