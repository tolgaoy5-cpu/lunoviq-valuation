"""
Provider interfaces. A provider serves one role; config/lunoviq.toml picks
which implementation serves each role, so a paid data vendor can be added as
a new module without touching the pipeline or the Excel layer.
"""
from abc import ABC, abstractmethod


class FundamentalsProvider(ABC):
    name = "abstract"

    @abstractmethod
    def financials(self, ticker, years=3, offline=False):
        """-> schema.FinancialStatements"""


class PriceProvider(ABC):
    name = "abstract"

    @abstractmethod
    def quote(self, ticker, offline=False):
        """-> schema.Quote"""

    @abstractmethod
    def monthly_history(self, symbol, months=60, offline=False):
        """-> schema.PriceHistory (month-end adjusted closes)"""


class RateProvider(ABC):
    name = "abstract"

    @abstractmethod
    def risk_free(self, offline=False):
        """-> schema.DataPoint (decimal, e.g. 0.0475)"""


class EquityRiskPremiumProvider(ABC):
    name = "abstract"

    @abstractmethod
    def erp(self, offline=False):
        """-> schema.DataPoint (decimal)"""


class EstimatesProvider(ABC):
    name = "abstract"

    @abstractmethod
    def revenue(self, ticker, offline=False):
        """-> {last_fy_end, last_revenue, y1_revenue, y1_growth, y2_revenue, y2_growth, analysts, source}"""
