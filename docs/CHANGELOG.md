# Project change log

## 2026-09-24: Phase 1, inspect and document (read-only)

- **Changed:** nothing in the existing files. Added `docs/`, `tools/` and `backups/`.
- **New files:**
  - `tools/xlsx_inspect.py`: read-only summary and cell-level diff of any `.xlsx`.
  - `docs/PHASE1_WORKBOOK_INVENTORY.md`
  - `docs/CHANGELOG.md`
  - `backups/2026-09-24_phase1/`: read-only copies of every workbook, plus SHA-256 checksums.
- **Why:** to establish which workbook is the master, how the versions differ, and where the pipeline plugs in, before touching anything.
- **Tests run:**
  - Ran the inspector on all 7 workbooks.
  - Diffed base vs `_Fixed` vs Lunoviq, and `_Fixed` vs `_Fixed copy`: 0 differences between those two.
  - Verified the backup checksums.
- **Result:** passed. The base model computes cleanly (0 formula errors, all health checks OK).
- **Open risks:** see §4 of the inventory. The main ones are that the master is ambiguous and that the generated models have never been recalculated.

## 2026-09-24: Merged master template v2 and Excel recalculation

The user chose to merge both branches into a new master and to recalculate with Excel via xlwings.

- **New workbook:** `Lunoviq_Master_Financial_Model_v2.xlsx`, built by `tools/build_master.py`. Neither parent was modified. v2 is the Lunoviq template plus:
  1. The `_Fixed` dashboard edits: `00_Dashboard` I37:L40 (scenario-aware) and I56.
  2. The unused FTE block removed from `07_Analysis_Scenarios` rows 134–156, with its merge A134:H134 and its conditional format B155:B156.
  3. **A root-cause fix.** The generated template had `<definedNames>` after `<calcPr>` in `xl/workbook.xml`. That breaks the OOXML schema order, so Excel flagged the file for repair and automation could not open it (AppleScript error -50). I found this by bisecting the file: removing the names made it open, and moving the block made it open with all names intact. The fix is a reorder only; the content is unchanged.

  The patch was applied at XML level, so the charts, styles and 95 named ranges are byte-identical to v1.
- **New tools:**
  - `tools/recalc.py`: Excel recalculation via xlwings.
  - `tools/build_master.py`
- **New tests:**
  - `tests/`: 10 pytest tests. The 2 Excel tests only run when `RUN_EXCEL_TESTS=1` is set.
  - `pytest.ini`
- **Other new files:** `.gitignore`.
- **Changed:** `edgar_feed.py` line 8 (docstring) and line 419. The `--model` default now points to v2. `README.md` line 27 was updated to match. The pre-change copies are in `backups/2026-09-24_phase1/`.
- **Tests:** `RUN_EXCEL_TESTS=1 python3 -m pytest` gives **10 passed**. They cover:
  - The build is reproducible.
  - v2 differs from v1 only in the intended cells.
  - v2 is exactly the union of both parents' edits.
  - Named ranges, charts and styles are preserved.
  - The schema order is correct.
  - **Excel regression:** 2,445 recalculated formulas match the `_Fixed` values, apart from 4 explained cells. Three come from Lunoviq's C68 change (C68, C72 and Dashboard E40). The fourth, Dashboard J41, is a stale cached value inside `_Fixed`: WPS never recalculated it.
  - The KO offline pipeline: core values are correct, the balance-sheet plugs close, and the template is not modified.
  - KO recalculated in Excel: all health checks OK, and the balance sheet balances in 2023–2030.
- **Reproducibility:** re-running `edgar_feed.py` on `ko.json` today reproduces the existing `COCA_COLA_CO_Model.xlsx` cell for cell, apart from the peer step and the timestamp.
- **Open risks and limitations, for later phases:**
  1. The model's historical net income for KO (6.4bn in 2023) doesn't match the reported figure (10.7bn). The model builds net income from a subset of lines. This is Phase 6.
  2. KO's DCF of $17.49 per share reflects the template's demo assumptions (WACC inputs, capex %, working-capital days) and the demo share price of 18.50. None of these are fed from data yet. These are modelling assumptions, and they will need your decisions.
  3. `edgar_feed.py` still overwrites `02_Operating_Model!B78:D78` with hard-coded values, and copies history into the forecast drivers.
  4. Saving through openpyxl re-serialises the charts in the generated company models. They still open and calculate in Excel, but I haven't visually checked chart formatting.
  5. In v1 and v2, `07_Analysis_Scenarios` rows 130–157 are grouped rather than hidden, unlike the WPS files. This is cosmetic, and I left it unchanged.

## 2026-09-24: Phases 2–3, standardized schema, free data providers, product-ready package

The user's direction: use free sources for now, publish on GitHub as a portfolio project, and build the architecture so it can later become a sellable product.

- **New package `lunoviq/`:**
  - `config.py` and `http.py`: HTTP with a disk cache, time-to-live settings, retries, and the SEC rate limit.
  - `schema.py`: a `DataPoint` carrying value, unit, source, as-of date, method and status.
  - `providers/`:
    - `base` (interfaces), `sec_edgar`, `yahoo`, and `rates` (Treasury, FRED, Damodaran).
    - `sec_xbrl`: the former `edgar_feed.py`, moved unchanged apart from the User-Agent line.
  - `market_inputs.py`: beta, cost of debt, tax rate and capital weights.
  - `excel/`:
    - `writer`: named ranges only, refuses to overwrite formulas, writes the `09_Sources` audit sheet.
    - `recalc`: moved from `tools/`.
    - `reader`: reads outputs and CHECK flags.
  - `pipeline.py` and `__main__.py`: the `python -m lunoviq run TICKER` command.
- **Compatibility:**
  - `edgar_feed.py` and `tools/recalc.py` are now thin shims. The legacy CLI output was verified numerically identical to before, apart from the timestamp.
  - In `peer_fetch.py`, the Stooq fetch was replaced by the price provider. Stooq now blocks scripts with a JavaScript challenge. The `--stooq` flag is kept as an alias for `--fetch-prices`.
- **Privacy:** the personal email was removed from the code. The SEC User-Agent now comes from the git-ignored `config/lunoviq.toml` or from the `LUNOVIQ_SEC_USER_AGENT` environment variable.
- **Organisation:**
  - `ko.json`, `kdp.json` and `stz.json` moved to `data/fixtures/`.
  - The English README replaces the Turkish one, which is kept as `docs/README_tr.md`.
  - Added `pyproject.toml` and `requirements.txt`.
  - `.gitignore` now excludes local config, the cache, `output/` and `backups/`.
- **Tests:** `RUN_EXCEL_TESTS=1 python3 -m pytest` gives **22 passed**. There are 12 new tests: provider parsers, beta regression, cost-of-debt floor, tax rate, capital weights, named-range fill, formula protection, the offline pipeline with overrides and audit log, and the Excel end-to-end run.
- **Live KO run (24 Sep 2026):**

  | Input or output | Value |
  |---|---|
  | Share price | $88.09 |
  | Risk-free rate | 5.11% |
  | Equity risk premium | 4.14% |
  | Beta (raw) | 0.31 |
  | Cost of debt | 6.11% (floor applied) |
  | Tax rate | 17.96% |
  | Debt weight | 10.7% |
  | WACC | 6.26% |
  | DCF (perpetuity) | $40.17 per share |

  Health checks: DCF = CHECK, because the WACC is outside the sensitivity grid's axis, which is hard-coded around 8.5%. All other areas are OK.
- **Open items:**
  1. Center the sensitivity-grid WACC axis on the computed WACC. This is a template change and needs approval.
  2. The forecast drivers (capex %, working-capital days) and the exit multiple are still demo or history placeholders. They cause the gap between the DCF value and the share price.
  3. Beta adjustment (raw vs Blume) is a policy choice. The default is raw; set `beta_adjustment = "blume"` to switch.
  4. Historical net income doesn't reconcile to the reported figure (carried over from the previous milestone).
  5. Yahoo is not licensed for commercial use.

## 2026-09-24: 12-company robustness run, data root-cause fixes, standard WACC, data-driven drivers, template v3

We ran a 12-company test together with the user: KO, PEP, AAPL, MSFT, NVDA, WMT, MNST, JNJ, XOM, GOOGL, AMZN and TSLA. The tool is `tools/batch_check.py`.

**Data-layer root causes** (`lunoviq/providers/sec_xbrl.py`):

| Problem | Company | Fix |
|---|---|---|
| The fiscal-year-end list used only the first revenue tag, so recent years and the whole balance sheet were lost | NVDA, GOOGL | Merge all revenue tags, from 10-K filings only |
| Tags were chosen first-series-wins, so later years went missing | WMT and TSLA D&A, AMZN tax | Merge per year by tag priority |
| An unfinished year leaked in from a 10-Q | AMZN "2026" | Model years = fiscal years with 10-K revenue |
| Operating-expense categories missing (≈204bn) | AMZN | Reconcile: EBITDA = reported operating income + D&A, with "Other OpEx" as the balancing line (also removes the D&A double count); fallback pre-tax + interest (JNJ) |
| Current tax mixed definitions across years | AMZN | Federal + state + foreign when the total is absent |
| Plugs were blank if any component was missing, so the forecast balance sheet failed | AAPL, GOOGL, WMT, AMZN, TSLA, MNST | Missing component = 0 inside "Other" |
| Non-controlling interest was dropped | PEP | Equity = assets − liabilities |
| Net debt ignored short-term investments | AAPL, GOOGL, MSFT | Cash = cash + short-term investments, using exactly one securities tag per year (TSLA had double counting) |
| Tag coverage gaps | PEP, MNST, AMZN/MSFT/JNJ/GOOGL/TSLA, JNJ | PEP receivables; MNST SG&A and equity; the 2024+ interest-expense tag; JNJ dividends |
| No usable 10-K revenue | XOM | Clear `DataError` (SEC companyfacts holds no 10-K facts for XOM) |

**WACC** (industry-standard methods; the user agreed they need no decision):
- Beta: Blume-adjusted.
- Cost of debt: risk-free + the Damodaran synthetic-rating spread from interest coverage, using the live ratings table.

**Forecast drivers** (new `lunoviq/drivers.py`) replace the template's demo values:

| Driver | Method |
|---|---|
| Revenue growth | Recent CAGR, fading linearly to terminal growth |
| Capex % | 3-year average |
| DSO / DIO / DPO | Latest fiscal year |
| Asset lives | Net PP&E ÷ D&A |
| Tax basis of PP&E | Equal to book PP&E. The demo value of $40m distorted every company's taxes and free cash flow |
| Dividend payout | Dividends ÷ net income |
| Debt | Held flat |

**Template v3** (`tools/build_master_v3.py`; v2 is unchanged), 42 cells in `05_Sensitivity`:
- The WACC and terminal-growth axes are centered on the model's own values. They were hard-coded around the demo company's 8.5%.
- Scenario EBITDA uses the model's own 2030 margin instead of the demo 32%.
- Scenario revenue uses the growth driver of the active revenue engine.
- On the demo data, the core valuation is identical to v2 (Excel regression test).

**Tests:** 37 passed (`RUN_EXCEL_TESTS=1`). New files: `tests/test_xbrl_mapping.py` (one synthetic test per real bug) and `tests/test_master_v3.py`.

**Result:** 10 of 11 companies have every health check OK, and every historical balance sheet balances exactly. TSLA flags "scenario ordering" because its value is close to zero.

**Open item, not a bug:** DCF values fall far below market prices for the mega-cap growth companies (AAPL, MSFT, NVDA, GOOGL, AMZN), for KO and WMT. That comes from methodology choices:
- a 5-year explicit horizon fading to 2.5% growth,
- the recent AI capex level carried forward,
- a high risk-free rate of 5.1%,
- non-operating investments (equity-method stakes, long-term securities) not being valued.

These are the next decisions.

## 2026-09-24: Valuation-method fixes with the 5-year horizon kept (template v4)

The user decided to keep the 5-year horizon and asked for fully correct calculations.

- **Template v4** (`tools/build_master_v4.py`, built from v3 without modifying it):
  - **Normalized terminal year.** The terminal cash flow is in `04_DCF_Valuation!G6:G11`, with maintenance capex = the smaller of 2030 capex and 2030 D&A. This removes MSFT/GOOGL-style over-investment carried into perpetuity. It also avoids overstating AMZN, whose 2030 D&A is temporarily high because older assets are still being written off. All 112 terminal references in the sensitivity grids now use it.
  - **Equity bridge:** net debt − non-operating investments + minority interest. The inputs are `01_Inputs!C68:C69`, named `val_NonOpAssets` and `val_MinorityInterest`.
- **Data additions:**
  - Non-operating investments, with one recipe per year to avoid double counting.
  - Minority interest.
  - Exit multiple = Damodaran US industry EV/EBITDA (positive-EBITDA firms, January 2026), with each ticker mapped to its industry via `indname.xlsx`. New module: `lunoviq/providers/industry.py`. New dependency: `xlrd`.
- **Independent checks** (`lunoviq/valuation_checks.py`):
  - Python recomputes the enterprise value from the workbook's own cash flows. Result: it matches Excel for all 11 companies.
  - Reverse DCF: the terminal growth and the WACC implied by the market price.
- **Tests:** 40 passed (`RUN_EXCEL_TESTS=1`).
- **12-company run:** 11 of 11 are healthy (XOM excluded: SEC data gap). Full numbers are in `output/batch_check_*.json` and the results report.

## 2026-09-24: Manager-grade pass, templates v5 and v6

The user asked for accuracy suitable for presenting to a manager, and approved the income-statement change.

- **v5 (`tools/build_master_v5.py`):** net income reconciliation.
  - New lines: non-operating income, historical deferred tax, and "minority interest & other".
  - Model net income now equals reported net income in every historical year. KO: 10,714 / 10,631 / 13,107 million $.
  - Forecast: non-operating income is held at its average, and the minority share at its historical ratio (flagged).
- **v6 (`tools/build_master_v6.py`):**
  - P/E fixed. It was EV ÷ net income; it is now market cap ÷ net income, with a new market-cap column.
  - Empty peer rows are ignored instead of producing `#DIV/0!`.
  - Precedent transactions show "n/a" when no deals are entered. There is no free M&A data source, and the demo deals are no longer shown.
- **Pipeline:**
  - Trading comparables are selected automatically: `peers/<TICKER>.csv` (analyst list) first, otherwise the same Damodaran industry by closest revenue (SEC frames). Peers are measured exactly like the target, and skipped peers are logged with the reason.
  - Unit-economics demo rows are hidden.
  - The Turkish note in the English workbook was translated.
  - Print and PDF setup: landscape, one page wide. KO went from 188 PDF pages to 30.
  - Drivers use 5 years of history. Bear/bull ranges = ± one standard deviation of the company's own growth and margin history.
  - Yahoo falls back to its mirror host.
- **Quality gate:** the reader now scans every cell for Excel errors. The template's checks only looked for "CHECK" and missed a `#DIV/0!`.
- **Tests:** 45 passed (`RUN_EXCEL_TESTS=1`).
- **12-company run:** 11 of 11 healthy with 0 Excel errors (XOM: SEC data gap).
- **Visual QA:** KO exported to PDF through Excel. Dashboard, income statement, DCF, comparables and sources pages reviewed.
- **External benchmarks:**
  - JNJ: model 229–289 $ against an analyst fair value of 305 $.
  - MSFT: sector-multiple method 624 $ against Morningstar's 600 $.
  - KO: model 45–58 $ against Morningstar's 74 $ (19x EBITDA, where the model uses the industry's 16.9x).

## 2026-09-24: Independent audit and template v7 (session paused here)

- **Independent forecast audit** (`lunoviq/audit.py`): Python rebuilds 190 forecast line-years from the model's inputs and compares them with Excel. Result: 0 mismatches for all 11 companies, for NVDA's bear and bull scenarios, and for a forced revolver stress case. Historical current ratio, net margin and ROE are also checked against SEC figures. The audit runs inside `tools/batch_check.py` and the test suite.
- **Template v7** (`tools/build_master_v7.py`):
  1. **Current/quick ratio.** It counted only payables as current liabilities, which put KO at 5.5x against about 1.0x reported. It now includes other current assets and liabilities from SEC data. KO's historical figures are 1.13 / 1.03 / 1.46.
  2. **Bear/bull free cash flow.** It was EBITDA × 70%, the demo company's rule; it now uses the model's own UFCF/EBITDA ratio.
  3. **Interest on existing debt.** It now uses the company's effective rate (new input `fin_DebtRate`, `01!C79`). WACC keeps the market cost of debt.
- **Benchmark tables** (07): the demo "Peer Low/Median/High" data is replaced by the real peers' ratios, the "DemoCo" label by the ticker, and the demo industry column is cleared.
- **Financing inputs** now come from data instead of the demo values:
  - cash interest = the 3-month Treasury bill,
  - revolver rate = the pre-tax cost of debt,
  - minimum cash = 2% of revenue,
  - revolver limit = 10% of revenue.
- **Tests:** 48 passed (`RUN_EXCEL_TESTS=1`).
- **12-company run:** 11 of 11 healthy with 0 Excel errors, 0 audit mismatches and correct historical ratios.

### Open: pick up here next session

**Interest income is double counted in the forecast.** The historical "non-operating income" line (03 row 18) includes interest income, and that line's average is carried into the forecast. Since v7 the forecast net interest (row 19) also earns interest on cash at the Treasury bill rate, so interest income is counted twice. KO: about $0.6bn a year of net income.

Planned fix (drafted, not applied):
- Add an `interest_income` tag (`InvestmentIncomeInterest`, `InvestmentIncomeInterestAndDividend`, `InterestIncomeOther`).
- Write historical net interest into `hist_InterestExpense` (expense − income), and compute non-operating income net of it.
- If a company doesn't report interest income separately (AAPL, MSFT, PEP, MNST), set `fin_CashRate` to 0 and keep the interest income inside non-operating income.
- Then rerun `tools/batch_check.py`, `tools/build_report.py`, and republish the report.

**Other open items:**
- The report page still shows the v6 run.
- Known simplifications: share buybacks are not modelled, so the forecast cash and current ratio drift upward; tax loss carryforwards use SEC's total, including state and foreign.
- Web UI (Phase 10) and GitHub publishing come last.

## 2026-09-25: Interest double count fixed (resumed session)

- **Correction to the entry above.** The interrupted edit on 24 Sep had in fact been written to disk before the interruption, and it was included in commit 66a3560. It is not "not applied". It was untested at that point; it has now been tested.
- **Fix: interest income** (for example KO).
  - Historical net interest = interest expense − interest income (`hist_InterestExpense`, `01!B22:D22`).
  - Non-operating income is calculated net of it.
  - The forecast earns interest on cash at the 3-month Treasury bill rate.
  - KO 2026E net income: 14.41 → 13.69 billion $. The roughly 0.7 billion $ double count is removed, and historical net income still equals the reported figures.
- **Symmetric rule for interest expense** (for example AAPL since FY2024, where it is not reported separately):
  - Interest expense stays inside non-operating income, and `fin_DebtRate` is set to 0.
  - Without this, 5.5 billion $ of debt interest would have been counted twice.
  - The same applies to interest income that isn't reported separately: `fin_CashRate` is set to 0.
  - WACC is unaffected.
- **Tests:** 50 passed (`RUN_EXCEL_TESTS=1`), including 2 new unit tests for these rules.
- **12-company run:** 11 of 11 healthy, 0 Excel errors, 0 audit mismatches, historical ratios correct.

## 2026-09-25: Phase 10, web app

- **Server** (`lunoviq/web/server.py`, standard library only, `python -m lunoviq serve`):
  - Binds to localhost only.
  - Runs one model build at a time on a worker thread, since Excel recalculates one workbook at a time. The browser polls the job's progress step by step.
  - Endpoints: ticker/company search (SEC list), start a run with validated overrides (revenue growth, terminal growth, exit multiple), run history, run summary, Excel download, and opening the model in Excel.
  - Error messages are in Turkish, and "retry" is offered only where retrying can help.
  - Path traversal is blocked (run IDs are regex-checked; static files are resolved inside `static/`).
  - `index.html` references its CSS/JS with a version suffix, so browsers never show an outdated interface after a change.
- **Pipeline:** new `progress` callback. The audit now runs inside the pipeline, and `lunoviq/summary.py` writes `summary.json` (the UI's data, read from the recalculated workbook).
- **UI** (`lunoviq/web/static`, vanilla JS/CSS, no build step):
  - Results show the valuation range (football field), a plain-language readout with reverse DCF, the WACC build-up, the EV→equity bridge, a financials chart and table, a sensitivity heatmap, peers and benchmarks, the assumption editor, checks and audit, "review before presenting" items, and every input's source.
  - Turkish; light and dark themes; responsive (charts are redrawn at their displayed width); keyboard-accessible autocomplete.
- **QA:**
  - Tested in the browser: desktop light and dark, and phone width (375px, no horizontal overflow).
  - Tested end to end: search by name ("monster" → MNST), progress, results, assumption override (6% growth, audit 190/190), XOM data error, unknown ticker, and client- and server-side validation.
  - Defects found in QA and fixed:
    - the logger crashed on 404s,
    - the topbar's blur caused rendering glitches,
    - the chart label overlapped an axis value, and chart text was oversized,
    - some UI text was in English,
    - stale cached JS was served,
    - the growth override was displayed incorrectly,
    - the page title didn't update,
    - "retry" was shown for errors that can't be retried.
- **Launcher:** `Lunoviq.command` now starts the web app, or just opens the browser if the app is already running.
- **Tests:** 63 passed (`RUN_EXCEL_TESTS=1`), including 13 new web API tests with a stubbed pipeline.
