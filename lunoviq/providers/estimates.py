"""
Analyst consensus revenue estimates (next two fiscal years).

Source: stockanalysis.com forecast page, free and public (robots.txt allows it),
cached for a day. Like Yahoo prices, it is unofficial: fine for personal use; a
commercial product needs a licensed consensus feed (FactSet, S&P, LSEG), added
as another EstimatesProvider without touching the pipeline.

The page embeds the data as a JavaScript object; the fields read here are
    table.annual.dates / revenue / analysts / lastDate   (last reported FY and the estimate)
    stats.annual.revenueThis / revenueNext               (year-1 and year-2 consensus)
"""
import re

from .. import config, http
from .base import EstimatesProvider

URL = "https://stockanalysis.com/stocks/%s/forecast/"
NUM = r"(-?[\d.]+(?:e[+-]?\d+)?|null)"


class EstimatesError(RuntimeError):
    pass


def _num(s):
    return None if s in (None, "null", '"[PRO]"') else float(s)


def parse(html):
    """-> {last_fy_end, last_revenue, y1_revenue, y1_growth, y2_revenue, y2_growth, analysts}
    (growth as decimals). Raises EstimatesError when the page has no revenue estimates."""
    this = re.search(r"revenueThis:\{last:%s,this:%s,growth:%s\}" % (NUM, NUM, NUM), html)
    nxt = re.search(r"revenueNext:\{last:%s,this:%s,growth:%s\}" % (NUM, NUM, NUM), html)
    table = re.search(r"table:\{annual:\{.*?dates:\[([^\]]*)\].*?analysts:\[([^\]]*)\].*?lastDate:(\d+)",
                      html, re.S)
    if not (this and table) or _num(this.group(2)) is None:
        raise EstimatesError("no revenue estimates on the page")
    dates = [d.strip('"') for d in table.group(1).split(",")]
    analysts = table.group(2).split(",")
    i = int(table.group(3))
    out = {"last_fy_end": dates[i] if i < len(dates) else None,
           "last_revenue": _num(this.group(1)),
           "y1_revenue": _num(this.group(2)),
           "y1_growth": _num(this.group(3)) / 100 if _num(this.group(3)) is not None else None,
           "y2_revenue": None, "y2_growth": None,
           "analysts": int(_num(analysts[i + 1])) if i + 1 < len(analysts) and _num(analysts[i + 1]) else None}
    if nxt and _num(nxt.group(2)) is not None and _num(nxt.group(3)) is not None:
        out["y2_revenue"], out["y2_growth"] = _num(nxt.group(2)), _num(nxt.group(3)) / 100
    return out


class StockAnalysisEstimates(EstimatesProvider):
    name = "stockanalysis"
    source = "stockanalysis.com analyst consensus"

    def revenue(self, ticker, offline=False):
        url = URL % ticker.lower().replace(".", "-")
        body, ts = http.fetch_bytes(url, ttl=config.get("http.ttl_estimates", 86400), offline=offline,
                                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                             "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
        out = parse(body.decode("utf-8", "replace"))
        out["source"], out["fetched_at"] = self.source, ts
        return out
