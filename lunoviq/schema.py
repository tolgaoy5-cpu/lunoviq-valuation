"""
Standardized, provider-independent data model.

Every number that enters the workbook is a DataPoint carrying its unit, the
source it came from, when it was obtained and how it was derived. Providers
translate their native format into these objects; the Excel layer only ever
sees these objects. Swapping a free source for a paid one therefore changes
one provider module and nothing else.
"""
from dataclasses import asdict, dataclass, field
from typing import Optional

# Status of a value, shown in the workbook's assumptions log.
AUTO = "auto"                   # fetched or computed from data
OVERRIDE = "override"           # set by the user
TEMPLATE = "template_default"   # left at the template's value -> review
MISSING = "missing"


@dataclass
class DataPoint:
    value: Optional[float]
    unit: str                     # "USD", "USD/share", "shares", "ratio", "x"
    source: str                   # e.g. "SEC EDGAR companyfacts"
    as_of: str                    # ISO date/time the value refers to or was fetched
    method: str = ""              # tag used / formula / series name
    status: str = AUTO

    def to_dict(self):
        return asdict(self)


# Canonical line items: key -> (label, statement, unit).
# Signs: values are stored as reported (expenses positive); the Excel writer
# applies the template's sign convention.
LINE_ITEMS = {
    "revenue":                 ("Revenue", "IS", "USD"),
    "cogs":                    ("Cost of Goods Sold", "IS", "USD"),
    "sga":                     ("SG&A", "IS", "USD"),
    "other_opex":              ("Other Operating Expense", "IS", "USD"),
    "d_and_a":                 ("Depreciation & Amortisation", "IS", "USD"),
    "interest_expense":        ("Interest Expense", "IS", "USD"),
    "current_tax":             ("Current Income Tax", "IS", "USD"),
    "income_tax_total":        ("Income Tax Expense (total)", "IS", "USD"),
    "pretax_income":           ("Pre-tax Income (reported)", "IS", "USD"),
    "operating_income":        ("Operating Income (reported)", "IS", "USD"),
    "net_income":              ("Net Income", "IS", "USD"),
    "cash":                    ("Cash & Equivalents", "BS", "USD"),
    "receivables":             ("Accounts Receivable", "BS", "USD"),
    "inventory":               ("Inventory", "BS", "USD"),
    "ppe_net":                 ("PP&E (net)", "BS", "USD"),
    "other_assets":            ("Other Assets (plug)", "BS", "USD"),
    "payables":                ("Accounts Payable", "BS", "USD"),
    "total_debt":              ("Total Debt", "BS", "USD"),
    "deferred_tax_liability":  ("Deferred Tax Liability", "BS", "USD"),
    "other_liabilities":       ("Other Liabilities (plug)", "BS", "USD"),
    "common_equity":           ("Common Equity", "BS", "USD"),
    "retained_earnings":       ("Retained Earnings", "BS", "USD"),
    "other_equity":            ("Other Equity (plug)", "BS", "USD"),
    "tax_loss_carryforward":   ("Tax Loss Carryforward", "Note", "USD"),
    "total_assets":            ("Total Assets (reported)", "BS", "USD"),
    "total_liabilities":       ("Total Liabilities (reported)", "BS", "USD"),
    "total_equity":            ("Total Equity (reported)", "BS", "USD"),
    "shares_diluted":          ("Shares Outstanding", "Other", "shares"),
}
LABEL_TO_KEY = {v[0]: k for k, v in LINE_ITEMS.items()}


@dataclass
class FinancialStatements:
    ticker: str
    company: str
    cik: str
    currency: str
    fiscal_years: list
    items: dict = field(default_factory=dict)   # key -> {year: DataPoint}
    raw: object = None                           # provider-native payload, for legacy writers

    def value(self, key, year):
        dp = self.items.get(key, {}).get(year)
        return dp.value if dp else None

    def latest(self, key):
        return self.value(key, self.fiscal_years[-1])

    def missing(self):
        return [k for k in LINE_ITEMS
                if all(self.value(k, y) is None for y in self.fiscal_years)]

    def to_dict(self):
        return {"ticker": self.ticker, "company": self.company, "cik": self.cik,
                "currency": self.currency, "fiscal_years": self.fiscal_years,
                "items": {k: {str(y): dp.to_dict() for y, dp in v.items()}
                          for k, v in self.items.items()}}


@dataclass
class Quote:
    ticker: str
    price: DataPoint
    currency: str


@dataclass
class PriceHistory:
    ticker: str
    dates: list          # ISO dates, ascending
    closes: list         # adjusted closes
    source: str


# Market / valuation inputs, keyed by the template's named range.
MARKET_INPUTS = {
    "ctl_SharePrice":   "Current share price",
    "val_RiskFree":     "Risk-free rate",
    "val_ERP":          "Equity risk premium",
    "val_Beta":         "Levered beta",
    "val_CostOfDebt":   "Pre-tax cost of debt",
    "val_DebtWeight":   "Debt weight (D / (D+E))",
    "val_EquityWeight": "Equity weight (E / (D+E))",
    "drv_TaxRate":      "Tax rate (forecast)",
    "val_TerminalGrowth": "Terminal growth rate",
    "val_ExitMultiple": "Exit EV/EBITDA multiple",
}
