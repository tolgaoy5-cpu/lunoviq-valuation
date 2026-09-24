"""
Risk-free rate and equity risk premium providers (free, public sources).

    treasury   US Treasury daily par yield curve, 10-year (official)
    fred       FRED series DGS10 (fallback; no API key needed for CSV)
    damodaran  Damodaran's monthly implied ERP (NYU Stern)
"""
import csv
import datetime as dt
import io
import re

from .. import config, http
from ..schema import AUTO, DataPoint
from .base import EquityRiskPremiumProvider, RateProvider

TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rates.csv/%d/all?type=daily_treasury_yield_curve"
            "&field_tdr_date_value=%d&page&_format=csv")
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
DAMODARAN = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"


class TreasuryProvider(RateProvider):
    name = "treasury"

    def risk_free(self, offline=False):
        year = dt.date.today().year
        for y in (year, year - 1):                       # early January: use last year's file
            body, _ = http.fetch_bytes(TREASURY % (y, y), ttl=config.get("http.ttl_rates", 43200),
                                       offline=offline)
            rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))
            rows = [r for r in rows if r.get("10 Yr")]
            if rows:
                latest = max(rows, key=lambda r: dt.datetime.strptime(r["Date"], "%m/%d/%Y"))
                d = dt.datetime.strptime(latest["Date"], "%m/%d/%Y").date().isoformat()
                return DataPoint(round(float(latest["10 Yr"]) / 100, 6), "ratio", "US Treasury par yield curve",
                                 d, "10-year constant maturity", AUTO)
        raise LookupError("no 10-year yield in Treasury files")


def treasury_short_rate(offline=False, column="3 Mo"):
    """Latest 3-month Treasury bill yield (interest earned on corporate cash)."""
    year = dt.date.today().year
    for y in (year, year - 1):
        body, _ = http.fetch_bytes(TREASURY % (y, y), ttl=config.get("http.ttl_rates", 43200), offline=offline)
        rows = [r for r in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))) if r.get(column)]
        if rows:
            latest = max(rows, key=lambda r: dt.datetime.strptime(r["Date"], "%m/%d/%Y"))
            d = dt.datetime.strptime(latest["Date"], "%m/%d/%Y").date().isoformat()
            return DataPoint(round(float(latest[column]) / 100, 6), "ratio", "US Treasury par yield curve",
                             d, "3-month bill, used for interest on cash", AUTO)
    raise LookupError("no 3-month yield in Treasury files")


class FredProvider(RateProvider):
    name = "fred"

    def risk_free(self, offline=False):
        body, _ = http.fetch_bytes(FRED, ttl=config.get("http.ttl_rates", 43200), offline=offline)
        rows = [r for r in csv.reader(io.StringIO(body.decode())) if len(r) == 2]
        for d, v in reversed(rows[1:]):
            if v not in (".", ""):
                return DataPoint(round(float(v) / 100, 6), "ratio", "FRED DGS10", d, "10-year constant maturity", AUTO)
        raise LookupError("FRED DGS10 had no observations")


class DamodaranProvider(EquityRiskPremiumProvider):
    name = "damodaran"

    def erp(self, offline=False):
        import openpyxl
        import warnings

        body, _ = http.fetch_bytes(DAMODARAN, ttl=7 * 86400, offline=offline)
        col = config.get("damodaran.erp_column", "ERP (T12m with sustainable payout)")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        ws = wb["Historical ERP"]
        rows = ws.iter_rows(values_only=True)
        header = list(next(rows))
        squash = [re.sub(r"\s+", "", str(h or "")).lower() for h in header]
        want = re.sub(r"\s+", "", col).lower()             # file writes "T12 m" / "T12m" inconsistently
        if want not in squash:
            raise LookupError("column %r not in Damodaran file (have %s)" % (col, header))
        i = squash.index(want)
        latest = None
        for r in rows:
            if r[0] and isinstance(r[i], (int, float)):
                latest = r
        return DataPoint(float(latest[i]), "ratio", "Damodaran implied ERP (NYU Stern)",
                         latest[0].date().isoformat(), header[i], AUTO)


RATINGS = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"


class DamodaranRatingsProvider:
    """Interest-coverage -> synthetic rating -> default spread (large non-financial firms)."""
    name = "damodaran_ratings"

    def table(self, offline=False):
        import html as _html

        body, _ = http.fetch_bytes(RATINGS, ttl=30 * 86400, offline=offline)
        page = body.decode("latin-1")
        asof = re.search(r"Data used is as of ([A-Za-z]+ \d{4})", page)
        rows = []
        for tr in re.findall(r"<tr.*?</tr>", page, re.S | re.I):
            cells = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", c))).strip()
                     for c in re.findall(r"<td.*?</td>", tr, re.S | re.I)]
            if len(cells) >= 4 and cells[3].endswith("%"):
                try:
                    rows.append((float(cells[0]), float(cells[1]), cells[2], float(cells[3].rstrip("%")) / 100))
                except ValueError:
                    continue
        if len(rows) < 10:
            raise LookupError("could not parse Damodaran ratings table (%d rows)" % len(rows))
        return sorted(rows), (asof.group(1) if asof else "")
