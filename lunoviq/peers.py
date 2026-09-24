"""
Trading-comparables peer set.

Selection, in order of precedence:
  1. peers/<TICKER>.csv  (ticker,karar,gerekce): an analyst's documented choice;
     rows marked DAHIL are used, each with its written rationale
  2. automatic: US-listed companies in the same Damodaran industry, ranked by
     closeness of annual revenue to the target (SEC XBRL frames, one call for
     all filers); share classes of one company count once; peers with
     non-positive EBITDA or net income are skipped (their multiples are not
     meaningful)

Each peer's figures come from the same pipeline as the target: SEC
fundamentals (latest fiscal year, same EBITDA reconciliation) and the market
price, so target and peers are measured identically.
"""
import csv
import math

from . import config, http, providers
from .providers.industry import DamodaranIndustryProvider

FRAME = "https://data.sec.gov/api/xbrl/frames/us-gaap/%s/USD/CY%d.json"
REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "RevenueFromContractWithCustomerIncludingAssessedTax")
N_PEERS = 4


def _sec_headers():
    return {"User-Agent": config.sec_user_agent()}


def _ticker_ciks(offline=False):
    from .providers import sec_xbrl
    data, _ = http.fetch_json(sec_xbrl.TICKERS, headers=_sec_headers(), ttl=7 * 86400, offline=offline)
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


def _revenue_by_cik(year, offline=False):
    out = {}
    for tag in REVENUE_TAGS:
        try:
            d, _ = http.fetch_json(FRAME % (tag, year), headers=_sec_headers(), ttl=30 * 86400, offline=offline)
        except http.FetchError:
            continue
        for row in d.get("data", []):
            out.setdefault(row["cik"], row["val"])
    return out


def manual_peers(ticker):
    root = config.ROOT / config.get("paths.peers_dir", "peers")
    f = root / ("%s.csv" % ticker.upper())
    if not f.exists():
        return None
    rows = list(csv.DictReader(open(f, encoding="utf-8")))
    chosen = [(r["ticker"].strip().upper(), r["gerekce"].strip()) for r in rows
              if r["karar"].strip().upper().startswith("DAHIL")]
    excluded = [(r["ticker"].strip().upper(), r["gerekce"].strip()) for r in rows
                if not r["karar"].strip().upper().startswith("DAHIL")]
    return chosen, excluded, str(f.relative_to(config.ROOT))


def candidates(ticker, target_revenue, year, offline=False):
    ind = DamodaranIndustryProvider()
    industry = ind.industry(ticker, offline)
    if not industry:
        return [], None
    index = ind._index(offline)
    ciks = _ticker_ciks(offline)
    revs = _revenue_by_cik(year, offline)
    target_cik = ciks.get(ticker.upper())
    seen, out = {target_cik}, []
    for tk, grp in index.items():
        cik = ciks.get(tk)
        if grp != industry or cik is None or cik in seen or not revs.get(cik, 0) > 0:
            continue
        seen.add(cik)
        out.append((abs(math.log(revs[cik] / target_revenue)), tk, revs[cik]))
    return sorted(out), industry


def peer_figures(tk, offline=False):
    st = providers.get("fundamentals").financials(tk, 3, offline=offline)
    y = st.fiscal_years[-1]
    v = lambda k: st.value(k, y)
    rev = v("revenue")
    ebitda = rev - sum(v(k) or 0 for k in ("cogs", "sga", "other_opex")) if rev else None
    shares = v("shares_diluted")
    price = providers.get("prices").quote(tk, offline=offline).price.value
    mcap = price * shares if (price and shares) else None
    ev = mcap + (v("total_debt") or 0) - (v("cash") or 0) if mcap else None
    return {"ticker": tk, "name": st.company, "fy": y, "ev": ev, "market_cap": mcap, "revenue": rev,
            "ebitda": ebitda, "net_income": v("net_income"), "price": price, "ratios": ratios(st)}


def ratios(st):
    """Latest-fiscal-year ratios on the same definitions as 07_Analysis_Scenarios."""
    ys = st.fiscal_years
    y, p = ys[-1], (ys[-2] if len(ys) > 1 else None)
    v = lambda k, yr=y: st.value(k, yr)
    div = lambda a, b: (a / b) if (a is not None and b) else None
    rev, cogs = v("revenue"), v("cogs") or 0
    ebitda = lambda yr: (v("revenue", yr) - sum(v(k, yr) or 0 for k in ("cogs", "sga", "other_opex"))
                         if v("revenue", yr) else None)
    ta, te = v("total_assets"), v("total_equity")
    days = lambda bal, base: div((v(bal) or 0) * 365, base)
    ccc = None
    if rev:
        ccc = (days("receivables", rev) or 0) + (days("inventory", cogs) or 0) - (days("payables", cogs) or 0)
    cl = v("current_liabilities")
    return {
        "gross_margin": div(rev - cogs, rev) if rev else None,
        "ebitda_margin": div(ebitda(y), rev),
        "net_margin": div(v("net_income"), rev),
        "roa": div(v("net_income"), ta),
        "roe": div(v("net_income"), te) if te and te > 0 else None,
        "asset_turnover": div(rev, ta),
        "ccc": ccc,
        "debt_equity": div(v("total_debt") or 0, te) if te and te > 0 else None,
        "debt_ebitda": div(v("total_debt") or 0, ebitda(y)) if ebitda(y) and ebitda(y) > 0 else None,
        "current_ratio": div(v("current_assets"), cl),
        "quick_ratio": div((v("cash") or 0) + (v("receivables") or 0), cl),
        "revenue_growth": div(rev, v("revenue", p)) - 1 if p and v("revenue", p) else None,
        "ebitda_growth": (div(ebitda(y), ebitda(p)) - 1) if p and ebitda(p) and ebitda(p) > 0 and ebitda(y) else None,
    }


def select(ticker, target_revenue, target_fy, offline=False, log=None):
    """-> (peers[list of dict incl. 'rationale'], skipped[list of str], source str)."""
    skipped = []
    log = skipped.append if log is None else log
    manual = manual_peers(ticker)
    if manual:
        chosen, excluded, src = manual
        peers = []
        for tk, why in chosen[:N_PEERS]:
            try:
                p = peer_figures(tk, offline)
            except Exception as e:                      # noqa: BLE001 - report and continue
                log("%s skipped: %s" % (tk, e))
                continue
            if not (p["ev"] and p["ebitda"] and p["net_income"]):
                log("%s skipped: SEC reports no %s" % (tk, "share count (e.g. dual-class)"
                                                        if not p["market_cap"] else "positive earnings"))
                continue
            p["rationale"] = why
            peers.append(p)
        return peers, skipped, "analyst list %s" % src

    ranked, industry = candidates(ticker, target_revenue, target_fy - 1 if target_fy else 2024, offline)
    peers = []
    for dist, tk, rev in ranked:
        if len(peers) == N_PEERS:
            break
        try:
            p = peer_figures(tk, offline)
        except Exception as e:                          # noqa: BLE001
            log("%s skipped: %s" % (tk, e))
            continue
        if not (p["ev"] and p["ebitda"] and p["ebitda"] > 0 and p["net_income"] and p["net_income"] > 0):
            continue
        p["rationale"] = "Same Damodaran industry (%s); revenue %.1fbn vs target %.1fbn" % (
            industry, p["revenue"] / 1e9, target_revenue / 1e9)
        peers.append(p)
    return peers, skipped, "automatic: Damodaran industry '%s', closest revenue" % industry
