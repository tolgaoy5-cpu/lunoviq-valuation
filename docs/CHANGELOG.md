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
