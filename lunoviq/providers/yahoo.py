"""
Yahoo Finance price provider (free, unofficial chart endpoint).

Suitable for personal / portfolio use. Yahoo's terms do not permit
commercial redistribution, so a commercial deployment should swap this for a
licensed vendor by adding another PriceProvider — nothing else changes.
"""
import datetime as dt
from urllib.parse import quote as urlquote

from .. import config, http
from ..schema import AUTO, DataPoint, PriceHistory, Quote
from .base import PriceProvider

SOURCE = "Yahoo Finance (chart API)"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"


def _chart(symbol, rng, interval, ttl, offline):
    data, _ = http.fetch_json(URL % (urlquote(symbol), rng, interval), ttl=ttl, offline=offline)
    res = (data.get("chart") or {}).get("result")
    if not res:
        raise LookupError("Yahoo returned no data for %s: %s" % (symbol, data.get("chart", {}).get("error")))
    return res[0]


class YahooProvider(PriceProvider):
    name = "yahoo"

    def quote(self, ticker, offline=False):
        r = _chart(ticker, "5d", "1d", config.get("http.ttl_prices", 900), offline)
        m = r["meta"]
        when = dt.datetime.fromtimestamp(m["regularMarketTime"], dt.timezone.utc)
        return Quote(ticker=ticker.upper(), currency=m.get("currency", "USD"),
                     price=DataPoint(float(m["regularMarketPrice"]), "USD/share", SOURCE,
                                     when.strftime("%Y-%m-%d %H:%M UTC"), "regularMarketPrice", AUTO))

    def monthly_history(self, symbol, months=60, offline=False):
        years = max(1, -(-months // 12)) + 1
        r = _chart(symbol, "%dy" % years, "1mo", config.get("http.ttl_rates", 43200), offline)
        adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose") or r["indicators"]["quote"][0]["close"]
        rows = [(dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), c)
                for t, c in zip(r["timestamp"], adj) if c is not None]
        return PriceHistory(ticker=symbol, dates=[d for d, _ in rows], closes=[c for _, c in rows],
                            source=SOURCE)
