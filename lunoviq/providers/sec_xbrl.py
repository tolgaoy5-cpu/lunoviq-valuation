"""
Lunoviq — SEC EDGAR data feed
-----------------------------
Given a ticker or CIK, fetches the last fiscal years from the SEC companyfacts
API, maps XBRL tags to the model's line items, writes 08_Data_Feed and fills
the historical block of 01_Inputs.

(tools/edgar_feed.py re-exports this module as a standalone CLI.)

Usage:
    python tools/edgar_feed.py AAPL  --model templates/Lunoviq_Master_Financial_Model_v2.xlsx
    python tools/edgar_feed.py 0000320193 --out Apple_Model.xlsx

Note: SEC's fair-access policy requires a User-Agent and allows at most 10 requests per second.
"""
import json, re, sys, time, argparse, datetime as dt
from pathlib import Path

BASE = "https://data.sec.gov"
TICKERS = "https://www.sec.gov/files/company_tickers.json"

# ---------------------------------------------------------------- tag mapping
# Priority-ordered XBRL tags per line item; per year, the first tag found is used.
TAGS = {
    "Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "Cost of Goods Sold": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "SG&A": ["SellingGeneralAndAdministrativeExpense",
             "GeneralAndAdministrativeExpense",
             # Last resort: companies that do not report SG&A separately (e.g. MNST)
             # report total operating expenses. Companies with R&D also report an
             # SG&A tag, so they never fall through to this one.
             "OperatingExpenses"],
    "Other Operating Expense": ["OtherCostAndExpenseOperating",
                                "OtherOperatingIncomeExpenseNet",
                                "ResearchAndDevelopmentExpense"],
    "Depreciation & Amortisation": ["DepreciationDepletionAndAmortization",
                                    "DepreciationAmortizationAndAccretionNet",
                                    "DepreciationAndAmortization", "Depreciation"],
    "Interest Expense": ["InterestExpense", "InterestExpenseDebt",
                         "InterestExpenseNonoperating",          # 2024+ taxonomy (e.g. AMZN)
                         "InterestIncomeExpenseNet"],
    "Net Income": ["NetIncomeLoss", "ProfitLoss"],
    "Accounts Receivable": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                            "AccountsNotesAndLoansReceivableNetCurrent"],   # e.g. PEP
    "Inventory": ["InventoryNet", "InventoryFinishedGoods"],
    "PP&E (net)": ["PropertyPlantAndEquipmentNet"],
    # Working capital (DPO) needs pure trade payables. "AP and accrued liabilities"
    # is a mixed line that inflates DPO -> last resort.
    "Accounts Payable": ["AccountsPayableCurrent",
                         "AccountsPayableTradeCurrent",
                         "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "Deferred Tax Liability": ["DeferredIncomeTaxLiabilitiesNet",
                               "DeferredTaxLiabilitiesNoncurrent",
                               "DeferredIncomeTaxesAndOtherTaxLiabilitiesNoncurrent"],
    "Retained Earnings": ["RetainedEarningsAccumulatedDeficit"],
    "Tax Loss Carryforward": ["OperatingLossCarryforwards"],
    # Reported totals -> used to compute the plugs that make the balance sheet balance
    "Total Assets (reported)": ["Assets"],
    "Total Equity (reported)": ["StockholdersEquity",
                                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "Total Liabilities (reported)": ["Liabilities"],
    # Ties the income statement to reported operating income (reconciliation below)
    "Operating Income (reported)": ["OperatingIncomeLoss"],
    # DCF equity bridge: minority interest is deducted from equity value
    "Minority Interest": ["MinorityInterest"],
    # Current ratio (07 analysis sheet): reported current assets / current liabilities
    "Current Assets (reported)": ["AssetsCurrent"],
    "Current Liabilities (reported)": ["LiabilitiesCurrent"],
    "Pre-tax Income (reported)": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"],
}
# Items built by summing tags: use a single total tag when the company reports one,
# otherwise add up the components.
SUMS = {
    # Recipe = (REQUIRED tags, OPTIONAL tags)
    # A recipe is used for a year only if ALL its required tags exist for that year,
    # so a tag change does not break the series and a missing component does not
    # silently produce a low total.
    "Total Debt": {
        "single": ["DebtLongtermAndShorttermCombinedAmount"],
        "recipes": [
            (["LongTermDebtAndCapitalLeaseObligations"],
             ["LongTermDebtAndCapitalLeaseObligationsCurrent",
              "CommercialPaper", "OtherShortTermBorrowings"]),
            (["LongTermDebtNoncurrent"],
             ["LongTermDebtCurrent", "CommercialPaper",
              "OtherShortTermBorrowings", "ShortTermBorrowings"]),
            (["LongTermDebt"], ["DebtCurrent"]),
        ],
    },
    # Current tax: without a total tag, federal + state + foreign are added up.
    # (AMZN 2025 has only the federal tag; using it alone mixed definitions
    # across years.)
    "Current Income Tax": {
        "single": [],
        "recipes": [
            (["CurrentIncomeTaxExpenseBenefit"], []),
            (["CurrentFederalTaxExpenseBenefit"],
             ["CurrentStateAndLocalTaxExpenseBenefit", "CurrentForeignTaxExpenseBenefit"]),
        ],
    },
    # Cash = cash and equivalents + short-term investments / marketable securities.
    # Net debt (DCF equity bridge) uses this definition; cash alone overstated net
    # debt for companies with large portfolios (AAPL, GOOGL, MSFT).
    "Cash & Equivalents": {
        "single": [],
        "recipes": [
            # ONE securities tag per year: companies can report the same amount
            # under several tags (e.g. TSLA MarketableSecuritiesCurrent =
            # ShortTermInvestments), so adding them would double count.
            (["CashCashEquivalentsAndShortTermInvestments"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "MarketableSecuritiesCurrent"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "AvailableForSaleSecuritiesDebtSecurities"], []),  # NVDA
            (["CashAndCashEquivalentsAtCarryingValue"], []),
            (["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], []),
        ],
    },
    # Non-operating investments (equity-method stakes, long-term securities).
    # Their returns are not in free cash flow, so the DCF adds them separately.
    # One recipe per year: balance sheet lines can include each other (MSFT's
    # LongTermInvestments also covers equity-method stakes).
    "Non-operating Investments": {
        "single": [],
        "recipes": [
            (["LongTermInvestments"], []),
            (["EquityMethodInvestments", "MarketableSecuritiesNoncurrent"], []),
            (["EquityMethodInvestments"], []),
            (["MarketableSecuritiesNoncurrent"], []),
            (["OtherLongTermInvestments"], []),
            (["EquitySecuritiesFVNINoncurrent"], []),
        ],
    },
    "Common Equity": {
        "single": [],
        "recipes": [
            (["CommonStockValue", "AdditionalPaidInCapital"], []),
            (["CommonStockValue", "AdditionalPaidInCapitalCommonStock"], []),
            (["CommonStocksIncludingAdditionalPaidInCapital"], []),
            (["CommonStockValueOutstanding", "AdditionalPaidInCapitalCommonStock"], []),   # e.g. MNST
            (["CommonStockValue"], []),
        ],
    },
}
# 'Issued' shares include treasury stock and overstate the real count.
# Prefer diluted weighted average, then shares outstanding.
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding",
          "WeightedAverageNumberOfSharesOutstandingBasic",
          "CommonStockSharesOutstanding",
          "EntityCommonStockSharesOutstanding",
          "CommonStockSharesIssued"]

ORDER = ["Revenue", "Cost of Goods Sold", "SG&A", "Other Operating Expense",
         "Depreciation & Amortisation", "Interest Expense", "Current Income Tax",
         "Net Income", "Cash & Equivalents", "Accounts Receivable", "Inventory",
         "PP&E (net)", "Accounts Payable", "Total Debt", "Deferred Tax Liability",
         "Common Equity", "Retained Earnings", "Tax Loss Carryforward",
         "Shares Outstanding",
         "Total Assets (reported)", "Total Liabilities (reported)",
         "Total Equity (reported)", "Operating Income (reported)",
         "Pre-tax Income (reported)", "Non-operating Investments", "Minority Interest",
         "Current Assets (reported)", "Current Liabilities (reported)"]


NOT_FOUND = "NOT FOUND"   # tag marker shown in 08_Data_Feed column F


class DataError(ValueError):
    """The company's SEC data is not sufficient to build the model."""


# ---------------------------------------------------------------- network
def _get(url):
    import urllib.request
    from lunoviq.config import sec_user_agent
    req = urllib.request.Request(url, headers={"User-Agent": sec_user_agent(),
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    return json.loads(raw)


def resolve_cik(token):
    """Ticker -> 10-digit CIK; a CIK is returned as is."""
    t = token.strip().upper()
    if re.fullmatch(r"\d{1,10}", t):
        return t.zfill(10)
    data = _get(TICKERS)
    for row in data.values():
        if row["ticker"].upper() == t:
            return str(row["cik_str"]).zfill(10)
    raise SystemExit("Ticker not found: %s" % token)


def fetch_facts(cik):
    time.sleep(0.15)                       # SEC rate limit
    return _get("%s/api/xbrl/companyfacts/CIK%s.json" % (BASE, cik))


# ---------------------------------------------------------------- transformation
def fiscal_year_ends(facts):
    """The company's fiscal year-end dates: {year: 'YYYY-MM-DD'}.

    Derived from annual (330-400 day) revenue facts. With a 52/53-week calendar
    the date moves by a few days each year, so actual dates are collected instead
    of assuming a fixed month/day.

    Fix (2026-09): only the first tag found used to be read. When a company
    switched tags (e.g. NVDA in 2022) later years disappeared and balance sheet
    items could not be matched. All revenue tags are now merged and only 10-K
    facts are used (so 12-month figures in a 10-Q cannot create an unclosed year).
    """
    ends = {}
    for taxo in ("us-gaap", "ifrs-full"):
        for tag in TAGS["Revenue"] + ["Revenues", "NetIncomeLoss"]:
            node = facts.get("facts", {}).get(taxo, {}).get(tag)
            if not node:
                continue
            for unit, rows in node.get("units", {}).items():
                if unit != "USD":
                    continue
                for r in rows:
                    if "start" not in r or not r.get("form", "").startswith("10-K"):
                        continue
                    d0 = dt.date.fromisoformat(r["start"])
                    d1 = dt.date.fromisoformat(r["end"])
                    if not 330 <= (d1 - d0).days <= 400:
                        continue
                    y = d1.year if d1.month > 5 else d1.year - 1
                    # keep the latest date per year (matches the year-end balance sheet)
                    if y not in ends or r["end"] > ends[y]:
                        ends[y] = r["end"]
        if ends:
            break                                  # us-gaap found: skip ifrs
    return ends


def annual_series(facts, tag, fye):
    """{fiscal_year: (value, filing_date)}.

    Avoids two traps:
      1) SEC's 'fy' field is the year of the FILING, not of the data.
         The period is always derived from the 'end' date.
      2) Balance sheet items are also reported as quarterly snapshots.
         Only facts at the fiscal year end (+/- 7 days) are used.
    """
    for taxo in ("us-gaap", "ifrs-full", "dei"):
        node = facts.get("facts", {}).get(taxo, {}).get(tag)
        if not node:
            continue
        for unit, rows in node.get("units", {}).items():
            if unit not in ("USD", "shares", "USD/shares"):
                continue
            out = {}
            for r in rows:
                end = r.get("end")
                if not end:
                    continue
                d1 = dt.date.fromisoformat(end)
                if "start" in r:                          # flow item
                    d0 = dt.date.fromisoformat(r["start"])
                    if not 330 <= (d1 - d0).days <= 400:
                        continue
                    fy = d1.year if d1.month > 5 else d1.year - 1
                else:                                     # point-in-time (balance sheet) item
                    fy = None
                    for y, anchor in fye.items():
                        if abs((d1 - dt.date.fromisoformat(anchor)).days) <= 7:
                            fy = y
                            break
                    if fy is None:
                        continue                          # quarterly snapshot -> skip
                filed = r.get("filed", "")
                form = r.get("form", "")
                rank = 0 if form.startswith("10-K") else 1
                # Preference: 10-K first (lower rank), then the latest filing.
                score = (-rank, filed)
                prev = out.get(fy)
                if prev is None or score > prev[2]:
                    out[fy] = (r["val"], filed, score)
            if out:
                return {k: (v[0], v[1]) for k, v in out.items()}, tag
    return {}, None


def pick(facts, tags, fye):
    """Per year, the value of the highest-priority tag that has that year.

    The first non-empty series used to be taken as a whole; when a company switched
    tags (e.g. WMT and TSLA depreciation, AMZN tax) the latest years were empty.
    The returned note lists the tags used, in year order, separated by ' | '.
    """
    merged, notes = {}, {}
    for t in tags:
        s, used = annual_series(facts, t, fye)
        for y, v in s.items():
            if y not in merged:
                merged[y], notes[y] = v, used
    if not merged:
        return {}, None
    uniq = []
    for y in sorted(notes):
        if notes[y] not in uniq:
            uniq.append(notes[y])
    return merged, " | ".join(uniq)


def build_feed(facts, n_years=3):
    """{item: {year: value}} plus the tags used."""
    fye = fiscal_year_ends(facts)
    if not fye:
        raise DataError("SEC companyfacts has no 10-K revenue facts (no fiscal year end found). "
                        "The company may not publish annual data in XBRL.")
    series, used = {}, {}
    for item in ORDER:
        if item in TAGS:
            s, u = pick(facts, TAGS[item], fye)
            series[item] = {k: v[0] for k, v in s.items()}
            used[item] = u or NOT_FOUND

        elif item in SUMS:
            # "single" (a total in one tag) is added as the LOWEST-priority recipe.
            # Such a tag may exist only for very old years (e.g. KDP's
            # DebtLongtermAndShorttermCombinedAmount only for 2017). Stopping at the
            # first hit lost the series; every year is now evaluated separately.
            recipes = list(SUMS[item]["recipes"]) + [([t], []) for t in SUMS[item]["single"]]
            if True:
                # Tag drift: companies change tags over time
                # (e.g. Coca-Cola 2024: LongTermDebtNoncurrent -> ...AndCapitalLeaseObligations).
                # Per year, the first recipe whose required components exist is used.
                cache = {}
                for req, opt in recipes:
                    for t in req + opt:
                        if t not in cache:
                            ss, _ = annual_series(facts, t, fye)
                            cache[t] = {fy: v[0] for fy, v in ss.items()}
                allyears = sorted({y for d in cache.values() for y in d})
                tot, notes = {}, {}
                for y in allyears:
                    for req, opt in recipes:
                        if not all(y in cache[t] for t in req):
                            continue                      # required tag missing -> next recipe
                        have = req + [t for t in opt if y in cache[t]]
                        tot[y] = sum(cache[t][y] for t in have)
                        notes[y] = " + ".join(have)
                        break
                series[item] = tot
                if notes:
                    uniq = []
                    for y in sorted(notes):
                        if notes[y] not in uniq:
                            uniq.append(notes[y])
                    used[item] = " | ".join(uniq)
                else:
                    used[item] = NOT_FOUND

        elif item == "Shares Outstanding":
            s, u = pick(facts, SHARES, fye)
            series[item] = {k: v[0] for k, v in s.items()}
            used[item] = u or NOT_FOUND

    # ---- Balance sheet plugs ---------------------------------------------------
    # The model has four asset and three liability lines. Real companies also have
    # goodwill, intangibles, investments, treasury stock, AOCI and so on. These are
    # computed as residuals from the reported totals, so the balance sheet balances
    # exactly for every company.
    def g(item, y):
        return series.get(item, {}).get(y)

    TA, TL, TE = "Total Assets (reported)", "Total Liabilities (reported)", "Total Equity (reported)"
    oa, ol, oe = {}, {}, {}
    for y in sorted({fy for s in series.values() for fy in s}):
        ta, te = g(TA, y), g(TE, y)
        tl = g(TL, y)
        if tl is None and ta is not None and te is not None:
            tl = ta - te                                   # derive Liabilities if not reported
        # An unreported component (e.g. AAPL deferred tax liability, GOOGL inventory)
        # counts as 0: the amount is already inside the "other" line. One missing
        # component used to leave the plug empty and the forecast balance sheet off.
        parts_a = [g(k, y) or 0 for k in ("Cash & Equivalents", "Accounts Receivable",
                                          "Inventory", "PP&E (net)")]
        parts_l = [g(k, y) or 0 for k in ("Accounts Payable", "Total Debt",
                                          "Deferred Tax Liability")]
        parts_e = [g(k, y) or 0 for k in ("Common Equity", "Retained Earnings")]
        if ta is not None:
            oa[y] = ta - sum(parts_a)
        if tl is not None:
            ol[y] = tl - sum(parts_l)
        # Total equity = assets - liabilities. Parent equity (TE) excludes minority
        # interest (e.g. PEP); the difference goes to "other equity".
        if ta is not None and tl is not None:
            oe[y] = (ta - tl) - sum(parts_e)
        elif te is not None:
            oe[y] = te - sum(parts_e)
    # ---- Operating expense reconciliation --------------------------------------
    # The template's expense lines (COGS, SG&A, Other) do not cover every reported
    # category (e.g. AMZN: fulfillment, technology, marketing), and most companies
    # report D&A inside COGS/SG&A. Standard normalisation:
    #     EBITDA = reported operating income + D&A
    # "Other operating expense" is the reconciling line that reaches this EBITDA,
    # so historical EBIT = reported operating income. When D&A is embedded the
    # line is NEGATIVE (an add-back); that is correct and avoids double counting.
    # For companies that do not report operating income (e.g. JNJ):
    #     operating income ~ pre-tax income + interest expense
    recon, how = {}, set()
    for y in sorted(set(series.get("Operating Income (reported)", {})) |
                    set(series.get("Pre-tax Income (reported)", {}))):
        oi = g("Operating Income (reported)", y)
        if oi is None and g("Pre-tax Income (reported)", y) is not None:
            oi = g("Pre-tax Income (reported)", y) + abs(g("Interest Expense", y) or 0)
            how.add("pre-tax income + interest expense")
        elif oi is not None:
            how.add("OperatingIncomeLoss")
        rev, da = g("Revenue", y), g("Depreciation & Amortisation", y)
        if None in (oi, rev, da):
            continue
        recon[y] = rev - (g("Cost of Goods Sold", y) or 0) - (g("SG&A", y) or 0) - (oi + da)
    if recon:
        tagged = series.get("Other Operating Expense", {})
        series["Other Operating Expense"] = {**tagged, **recon}
        used["Other Operating Expense"] = ("Revenue - COGS - SG&A - (operating income + D&A) "
                                           "[reconciling line; operating income: %s]" % ", ".join(sorted(how)))

    series["Other Assets (plug)"] = oa
    series["Other Liabilities (plug)"] = ol
    series["Other Equity (plug)"] = oe
    used["Other Assets (plug)"] = "Assets - (Cash+AR+Inv+PPE)"
    used["Other Liabilities (plug)"] = "Liabilities - (AP+Debt+DTL)"
    used["Other Equity (plug)"] = "(Assets - Liabilities) - (Common+Retained)  [incl. minority interest]"

    # Years: fiscal years with a 10-K and revenue. The union of all series used to
    # be taken, so an unclosed year seen in one item (e.g. AMZN "2026") entered
    # the model.
    years = sorted(y for y in fye if y in series.get("Revenue", {}))[-n_years:]
    if not years:
        raise DataError("No 10-K revenue data found.")
    return series, used, years


def to_thousands(item, val, div=1000.0):
    if val is None:
        return None
    return val if item == "Shares Outstanding" else val / div


# ---------------------------------------------------------------- Excel writer
def write_model(path_in, path_out, company, cik, series, used, years, div=1000.0):
    import openpyxl
    wb = openpyxl.load_workbook(path_in)
    fd = wb["08_Data_Feed"]
    fd["B3"] = company
    fd["E3"] = cik
    fd["H3"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fd["B4"] = " / ".join(str(y) for y in years)

    row_of = {fd.cell(row=r, column=1).value: r for r in range(7, 40)
              if fd.cell(row=r, column=1).value}
    for item in list(ORDER) + ["Other Assets (plug)", "Other Liabilities (plug)",
                               "Other Equity (plug)"]:
        r = row_of.get(item)
        if not r:
            continue
        for i, y in enumerate(years):
            fd.cell(row=r, column=3 + i).value = to_thousands(item, series[item].get(y), div)
        fd.cell(row=r, column=6).value = used.get(item, "")

    # --- fill the historical block of 01_Inputs (template sign conventions)
    inp = wb["01_Inputs_Historicals"]
    def put(row, item, sign=1, keep_sign=False):
        for i, y in enumerate(years):
            v = to_thousands(item, series[item].get(y), div)
            if v is not None and sign < 0:
                v = sign * v if keep_sign else sign * abs(v)
            inp.cell(row=row, column=2 + i).value = v
    put(18, "Revenue")
    put(19, "SG&A", -1)
    put(20, "Other Operating Expense", -1, keep_sign=True)   # the reconciling line can be negative
    put(21, "Depreciation & Amortisation", -1)
    put(22, "Interest Expense", -1)
    put(23, "Current Income Tax", -1)
    put(24, "Cash & Equivalents")
    put(25, "Accounts Receivable")
    put(26, "Inventory")
    put(27, "PP&E (net)")
    put(28, "Accounts Payable")
    put(29, "Total Debt")
    put(30, "Deferred Tax Liability")
    put(31, "Common Equity")
    put(32, "Retained Earnings")
    put(33, "Tax Loss Carryforward")

    # share count (same unit divisor) and Growth-Margin drivers
    sh = series["Shares Outstanding"].get(years[-1])
    if sh:
        inp["C67"] = sh / div
    # ---- Forecast drivers from history -----------------------------------------
    # Take operating expense ratios from history as well as growth and COGS margin;
    # otherwise the forecast runs on the template's demo assumptions (SG&A 11.5%)
    # and the margin does not match the company.
    rev = [series["Revenue"].get(y) for y in years]
    cogs = [series["Cost of Goods Sold"].get(y) for y in years]
    sga = [series["SG&A"].get(y) for y in years]
    oox = [series["Other Operating Expense"].get(y) for y in years]

    def fill(row, val, nd=4):
        for c in range(3, 8):                      # C..G = 2026E..2030E
            inp.cell(row=row, column=c).value = round(val, nd)

    if all(rev) and len(rev) >= 2:
        fill(100, rev[-1] / rev[-2] - 1)           # revenue growth
    if rev[-1] and cogs[-1]:
        fill(101, cogs[-1] / rev[-1])              # COGS margin
    if rev[-1] and sga[-1]:
        fill(43, abs(sga[-1]) / rev[-1])           # SG&A % of revenue
    ox = next((v for v in reversed(oox) if v), None)
    if rev[-1] and ox:
        fill(44, ox / rev[-1])                     # other operating expense % of revenue (signed)
    elif rev[-1]:
        fill(44, 0.0)                              # no data -> zero (no demo value left behind)

    # In Growth-Margin mode historical COGS must also come from the filings;
    # otherwise the historical columns are still fed by the unit-cost blocks and
    # the EBITDA margin is meaningless.
    op = wb["02_Operating_Model"]
    for i, y in enumerate(years):
        cogs = to_thousands("Cost of Goods Sold", series["Cost of Goods Sold"].get(y), div)
        if cogs is not None:
            col = 2 + i                                    # B, C, D
            op.cell(row=78, column=col).value = cogs       # Total COGS (historical)

    wb["00_Dashboard"]["B5"] = company
    wb["00_Dashboard"]["B12"] = "Growth-Margin"    # the right mode for external data
    wb.save(path_out)
    return path_out


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="Ticker (AAPL) or CIK (0000320193)")
    ap.add_argument("--model", default="templates/Lunoviq_Master_Financial_Model_v2.xlsx")
    ap.add_argument("--out", default=None)
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--facts", default=None, help="companyfacts JSON file for offline runs")
    ap.add_argument("--save-json", default=None, help="save the raw companyfacts JSON to this file")
    a = ap.parse_args()

    if a.facts:
        facts = json.load(open(a.facts))
        cik = str(facts.get("cik", "")).zfill(10)
    else:
        cik = resolve_cik(a.ticker)
        facts = fetch_facts(cik)
        if a.save_json:
            json.dump(facts, open(a.save_json, "w"))
            print("Raw JSON saved: %s" % a.save_json)
    company = facts.get("entityName", a.ticker)

    try:
        series, used, years = build_feed(facts, a.years)
    except DataError as e:
        raise SystemExit("%s: %s" % (a.ticker, e))
    missing = [k for k, v in used.items() if v == NOT_FOUND]

    out = a.out or "%s_Model.xlsx" % re.sub(r"\W+", "_", company)[:40]
    write_model(a.model, out, company, cik, series, used, years)

    print("Company: %s (CIK %s)" % (company, cik))
    print("Years  : %s" % ", ".join(str(y) for y in years))
    print("Written: %s" % out)
    for item in ORDER:
        vals = [series[item].get(y) for y in years]
        flag = "  <-- NOT FOUND" if used.get(item) == NOT_FOUND else ""
        print("  %-30s %s%s" % (item, ["%.0f" % (v/1000) if v else "-" for v in vals], flag))
    if missing:
        print("\nNOT FOUND (%d) - fill in manually: %s" % (len(missing), ", ".join(missing)))

    print("""
NEXT STEPS (this standalone feed only fills the historicals; the full pipeline,
`python -m lunoviq run <TICKER>`, does all of this automatically)
  1. 00_Dashboard B10 -> current share price (not in the filings).
  2. 06_Comparable_Valuation -> the peers are still placeholders; replace them,
     otherwise the football-field check turns red (by design).
  3. 01_Inputs rows 100-101 -> revenue growth and COGS margin are copied from the
     last year; they should be your forecast, not a repeat of history.
  4. 08_Data_Feed column F -> check which XBRL tag was used.
  5. 00_Dashboard I57 -> model health; if red, do not rely on the results.""")

if __name__ == "__main__":
    main()
