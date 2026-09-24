"""
Industry classification and industry EV/EBITDA multiples (Damodaran, free).

    indname.xlsx  every listed company -> Damodaran industry group (by exchange:ticker)
    vebitda.xls   US industry averages: EV/EBITDA (positive-EBITDA firms)

Used for the exit-multiple leg of the DCF: at the end of a 5-year forecast the
business is valued at what its industry trades at today, the standard
investment-banking cross-check to the perpetuity-growth method.
"""
import io
import json
import warnings

from .. import http
from ..schema import AUTO, DataPoint

INDNAME = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/indname.xlsx"
VEBITDA = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/vebitda.xls"
US_EXCHANGES = ("NYSE", "NasdaqGS", "NasdaqGM", "NasdaqCM", "NYSEAM", "NYSEArca", "BATS")
TTL = 30 * 86400


class DamodaranIndustryProvider:
    name = "damodaran_industry"

    def _index(self, offline=False):
        """{ticker: industry} for US listings; parsed once, then cached as JSON."""
        cache = http._cache_path(INDNAME + "#us-index").with_suffix(".index.json")
        if cache.exists():
            return json.loads(cache.read_text())
        import openpyxl

        body, _ = http.fetch_bytes(INDNAME, ttl=TTL, offline=offline)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = openpyxl.load_workbook(io.BytesIO(body), read_only=True)
        ws = wb["By company name"]
        rows = ws.iter_rows(values_only=True)
        head = list(next(rows))
        i_tk, i_ind, i_cty = head.index("Exchange:Ticker"), head.index("Industry Group"), head.index("Country")
        out = {}
        for r in rows:
            tk = r[i_tk] or ""
            if ":" not in tk or r[i_cty] != "United States":
                continue
            exch, sym = tk.split(":", 1)
            if exch in US_EXCHANGES:
                out.setdefault(sym.upper(), r[i_ind])
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(out))
        return out

    def industry(self, ticker, offline=False):
        return self._index(offline).get(ticker.upper().replace("-", "."))

    def multiples(self, offline=False):
        import xlrd

        body, _ = http.fetch_bytes(VEBITDA, ttl=TTL, offline=offline)
        sh = xlrd.open_workbook(file_contents=body).sheet_by_name("Industry Averages")
        asof = ""
        head_row = None
        for r in range(sh.nrows):
            row = sh.row_values(r)
            if row[0] == "Date updated:" and isinstance(row[1], float):
                y, m, d, *_ = xlrd.xldate_as_tuple(row[1], 0)
                asof = "%04d-%02d-%02d" % (y, m, d)
            if row[0] == "Industry Name":
                head_row = r
                break
        if head_row is None:
            raise LookupError("vebitda.xls layout changed (no 'Industry Name' header)")
        # first EV/EBITDA column = "Only positive EBITDA firms" block
        col = sh.row_values(head_row).index("EV/EBITDA")
        table = {}
        for r in range(head_row + 1, sh.nrows):
            name, v = sh.cell_value(r, 0), sh.cell_value(r, col)
            if name and isinstance(v, float):
                table[name] = v
        return table, asof

    def ev_ebitda(self, ticker, offline=False):
        ind = self.industry(ticker, offline)
        if not ind:
            return None
        table, asof = self.multiples(offline)
        if ind not in table:
            return None
        return DataPoint(round(table[ind], 2), "x", "Damodaran industry averages (US)", asof,
                         "EV/EBITDA, positive-EBITDA firms, industry: %s" % ind, AUTO)
