"""
SEC EDGAR fundamentals provider (free, official, US filers only).

Wraps the existing, tested XBRL mapping engine (sec_xbrl.build_feed) and
converts its output into the standardized schema. Network access goes
through lunoviq.http so raw payloads are cached on disk.
"""
import json
import re

from .. import config, http
from ..schema import LABEL_TO_KEY, LINE_ITEMS, AUTO, MISSING, DataPoint, FinancialStatements
from . import sec_xbrl
from .base import FundamentalsProvider

SOURCE = "SEC EDGAR companyfacts"
# Extra tags used for the effective tax rate (not written by the legacy writer).
EXTRA_TAGS = {
    "income_tax_total": ["IncomeTaxExpenseBenefit"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
    ],
    # Cash-flow items for forecast drivers (lunoviq/drivers.py)
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "PaymentsOfOrdinaryDividends",
                  "DividendsCommonStockCash"],
}


class SecEdgarProvider(FundamentalsProvider):
    name = "sec_edgar"

    def __init__(self, facts_path=None):
        self.facts_path = facts_path          # offline fixture (companyfacts JSON)

    def _headers(self):
        return {"User-Agent": config.sec_user_agent()}

    def resolve_cik(self, ticker, offline=False):
        t = ticker.strip().upper()
        if re.fullmatch(r"\d{1,10}", t):
            return t.zfill(10)
        data, _ = http.fetch_json(sec_xbrl.TICKERS, headers=self._headers(),
                                  ttl=config.get("http.ttl_fundamentals", 86400), offline=offline)
        for row in data.values():
            if row["ticker"].upper() == t:
                return str(row["cik_str"]).zfill(10)
        raise LookupError("Ticker not found at SEC: %s" % ticker)

    def company_facts(self, ticker, offline=False):
        if self.facts_path:
            with open(self.facts_path, encoding="utf-8") as fh:
                return json.load(fh)
        cik = self.resolve_cik(ticker, offline=offline)
        url = "%s/api/xbrl/companyfacts/CIK%s.json" % (sec_xbrl.BASE, cik)
        facts, _ = http.fetch_json(url, headers=self._headers(),
                                   ttl=config.get("http.ttl_fundamentals", 86400), offline=offline)
        return facts

    def financials(self, ticker, years=3, offline=False):
        facts = self.company_facts(ticker, offline=offline)
        series, used, fy_list = sec_xbrl.build_feed(facts, years)
        fye = sec_xbrl.fiscal_year_ends(facts)

        items = {}
        for label, by_year in series.items():
            key = LABEL_TO_KEY.get(label)
            if not key:
                continue
            unit = LINE_ITEMS[key][2]
            items[key] = {y: DataPoint(by_year.get(y), unit, SOURCE, fye.get(y, str(y)),
                                       used.get(label, ""),
                                       AUTO if by_year.get(y) is not None else MISSING)
                          for y in fy_list}
        for key, tags in EXTRA_TAGS.items():
            s, tag = sec_xbrl.pick(facts, tags, fye)
            items[key] = {y: DataPoint(s[y][0] if y in s else None, "USD", SOURCE,
                                       fye.get(y, str(y)), tag or "BULUNAMADI",
                                       AUTO if y in s else MISSING)
                          for y in fy_list}

        st = FinancialStatements(ticker=ticker.upper(), company=facts.get("entityName", ticker),
                                 cik=str(facts.get("cik", "")).zfill(10), currency="USD",
                                 fiscal_years=fy_list, items=items)
        st.raw = {"series": series, "used": used, "years": fy_list}
        return st
