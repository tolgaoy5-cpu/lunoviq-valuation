# Lunoviq: Financial Analysis & Valuation Automation

One command turns a stock ticker into a complete, recalculated, auditable
valuation model:

```
python -m lunoviq run KO
```

```
Ticker ─► Data providers ─► Standardized schema ─► Excel model (copy) ─► Recalculation ─► Validation
          SEC EDGAR          DataPoint:             named ranges,          Microsoft Excel    health checks,
          Yahoo prices       value + unit +         formula-protected,     via xlwings        CHECK flags,
          US Treasury/FRED   source + date +        09_Sources audit log                      run.json record
          Damodaran ERP      method + status
```

The Excel model contains a 3-statement model, DCF (perpetuity and exit
multiple), sensitivity tables, trading and transaction comparables, scenarios
(Bear/Base/Bull) and a management dashboard with built-in model-health checks.

## What each run produces

`output/<TICKER>_<timestamp>/`
- `<TICKER>_Model.xlsx`: the populated and recalculated model. It includes a
  `09_Sources` sheet listing every input with its source, as-of date, method,
  and status (`auto` / `override` / `template_default`).
- `run.json`: a machine-readable record of all inputs, the normalized financial
  statements, and the model outputs (valuation, WACC, health checks, flags).

## Data sources (free tier)

| Role | Provider | Notes |
|---|---|---|
| Financial statements | SEC EDGAR companyfacts (XBRL) | Official, US filers. Prioritized tag mapping with fiscal-year and duration checks. |
| Share price, beta | Yahoo Finance chart API | Free/unofficial, so for personal use only. Swap for a licensed vendor commercially. |
| Risk-free rate | US Treasury yield curve, with FRED DGS10 as fallback | 10-year constant maturity |
| Equity risk premium | Damodaran implied ERP (NYU Stern) | Monthly |

Providers implement small interfaces (`lunoviq/providers/base.py`) and are
selected in `config/lunoviq.toml`. Adding a paid vendor means adding one
module; the pipeline and Excel layer do not change.

## Design principles

- **The master template is never modified.** Each run writes to a copy.
- **Formulas are protected.** Inputs are written through named ranges, and
  writing to a cell that holds a formula raises an error.
- **Every number is traceable.** Each value carries its source, date, and
  derivation, both in the workbook and in `run.json`.
- **Judgement stays with the analyst.** Inputs with no objectively correct
  value (terminal growth, exit multiple) are never invented. They stay at the
  template value, are highlighted for review, and can be overridden:
  ```
  python -m lunoviq run KO --set val_TerminalGrowth=0.02 --set val_ExitMultiple=18
  ```
  Persistent overrides can go in `overrides/<TICKER>.toml` under `[inputs]`.
- **Policies are configuration, not code.** Beta window, credit spread, and tax
  bounds are set in `[wacc]` of the config.

## Setup

```bash
pip install -r requirements.txt
cp config/lunoviq.example.toml config/lunoviq.toml
# edit config/lunoviq.toml -> [sec] user_agent = "YourName your@email"  (SEC requirement)
python -m lunoviq run KO
```

Recalculation requires Microsoft Excel (macOS or Windows). Without it, use
`--no-recalc`: the populated workbook recalculates when opened in Excel.

## Tests

```bash
python -m pytest                     # fast, offline (network sources stubbed)
RUN_EXCEL_TESTS=1 python -m pytest   # adds end-to-end Excel recalculation tests
```

The tests cover:
- Provider parsers.
- WACC components (beta regression, cost-of-debt floor, tax rate, capital weights).
- Named-range writing and formula protection.
- The offline end-to-end pipeline.
- Template integrity. The master is a byte-level merge of two workbook branches
  and is regression-tested against Excel-computed values.

## Repository layout

```
lunoviq/                 package
  providers/             sec_edgar, sec_xbrl (XBRL mapping engine), yahoo, rates
  schema.py              standardized DataPoint / FinancialStatements model
  market_inputs.py       cost-of-capital inputs from market data
  excel/                 writer (named ranges, audit log), recalc (Excel), reader
  pipeline.py            end-to-end orchestration
config/                  lunoviq.example.toml (committed), lunoviq.toml (local)
tools/                   workbook inspector/differ, master-template builder
tests/                   pytest suite
data/fixtures/           cached SEC payloads for offline tests
docs/                    workbook inventory, architecture, change log
edgar_feed.py, peer_fetch.py   original CLIs (still supported)
```

## Status and roadmap

These phases are done:
- Workbook inventory.
- Merged master template.
- Excel recalculation.
- Provider architecture.
- WACC inputs from market data.
- Audit trail.

These are next:
1. Reconcile historical net income to reported figures.
2. Validate the 3-statement model.
3. Validate DCF, sensitivity, comparables, and scenarios.
4. Center the sensitivity axis on the computed WACC.
5. Base forecast drivers on data.
6. Dashboard outputs.
7. A simple UI.

See `docs/CHANGELOG.md`.

## Limitations

- US SEC filers only; annual data only.
- Yahoo data is unofficial and not for commercial redistribution.
- Forecast drivers are currently history-based placeholders. They are flagged
  in `09_Sources` and are analyst assumptions, not outputs.
