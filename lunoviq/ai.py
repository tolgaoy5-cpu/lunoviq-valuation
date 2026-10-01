"""
AI-drafted valuation memo (optional).

The model gets the run's key figures (values per share by method, price, WACC
build, reverse DCF, growth sources and flags, peers, checks) and writes a short
memo for an investment committee or a manager. Every $ amount, percentage and
multiple in its answer is then checked against those figures; a draft with an
unknown figure, or with a buy/sell/hold recommendation, is rejected. The text is
always labelled as an AI draft and is not investment advice.

The API key is shared with Lunoviq FP&A and lives outside every repository:
    ~/.lunoviq/ai.toml   [ai] provider = "openai" | "anthropic", api_key = "...", model = "..."
    or the environment variables OPENAI_API_KEY / ANTHROPIC_API_KEY
Nothing is sent unless the user asks for a memo. Standard library only.
"""
import json
import os
import re
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

SETTINGS = Path.home() / ".lunoviq" / "ai.toml"
DEFAULT_MODEL = {"openai": "gpt-4o-mini", "anthropic": "claude-sonnet-5"}
ADVICE = re.compile(r"\b(buy|sell|hold|overweight|underweight|outperform|underperform|strong buy)\b"
                    r"(?!\s*(?:side|-side|back|backs))", re.I)


class AIError(RuntimeError):
    pass


def settings():
    cfg = {}
    if SETTINGS.exists():
        with open(SETTINGS, "rb") as f:
            cfg = tomllib.load(f).get("ai", {})
    provider = cfg.get("provider") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") and
                                       not os.environ.get("OPENAI_API_KEY") else "openai")
    key = cfg.get("api_key") or os.environ.get("%s_API_KEY" % provider.upper())
    if not key:
        return None
    return {"provider": provider, "api_key": key, "model": cfg.get("model") or DEFAULT_MODEL[provider]}


def save_settings(provider, api_key, model=""):
    if provider not in DEFAULT_MODEL:
        raise AIError("unknown provider %s" % provider)
    key = (api_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,300}", key):
        raise AIError("that does not look like an API key")
    q = lambda s: '"%s"' % s.replace("\\", "\\\\").replace('"', '\\"')
    text = "# Lunoviq AI settings: on this computer only, outside every repository.\n[ai]\nprovider = %s\napi_key = %s\n" % (
        q(provider), q(key))
    if model.strip():
        text += "model = %s\n" % q(model.strip())
    SETTINGS.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    SETTINGS.write_text(text)
    os.chmod(SETTINGS, 0o600)


def clear_settings():
    if SETTINGS.exists():
        SETTINGS.unlink()


def _r(v, d=1):
    return None if v is None else round(v, d)


def facts(s):
    """The figures the model may use, from summary.json, in the units it should write them."""
    price = s.get("price")
    bn = lambda v: _r(v * s.get("unit_div", 1e3) / 1e9, 1) if v is not None else None
    w, b, c = s.get("wacc") or {}, s.get("bridge") or {}, s.get("dcf_checks") or {}
    f = {"company": s.get("company"), "ticker": s.get("ticker"), "valuation_date": (s.get("generated") or "")[:10],
         "share_price_usd": _r(price, 2), "values_per_share_usd": {}, "audit": s.get("audit"),
         "model_checks": (s.get("health") or {}).get("Overall")}
    for m in s.get("methods", []):
        if m.get("value") is None:
            continue
        f["values_per_share_usd"][m["label"]] = {
            "value": _r(m["value"], 2), "sensitivity_low": _r(m.get("low"), 2), "sensitivity_high": _r(m.get("high"), 2),
            "vs_price_pct": _r((m["value"] / price - 1) * 100, 1) if price else None}
    f["wacc_pct"] = {k: _r((w.get(k) or 0) * 100, 1) for k in ("wacc", "cost_of_equity", "after_tax_kd", "terminal_growth",
                                                               "risk_free", "erp", "pretax_kd", "tax_rate")}
    f["beta"] = _r(w.get("beta"), 2)
    f["weights_pct"] = {"equity": _r((w.get("equity_weight") or 0) * 100, 0), "debt": _r((w.get("debt_weight") or 0) * 100, 0)}
    f["enterprise_value_usd_bn"] = bn(b.get("ev"))
    f["equity_value_usd_bn"] = bn(b.get("equity"))
    f["terminal_value_share_of_ev_pct"] = _r(b["pv_terminal"] / b["ev"] * 100, 1) if b.get("ev") else None
    f["exit_multiple_x"] = _r(b.get("exit_multiple"), 1)
    f["reverse_dcf"] = {"implied_terminal_growth_pct": _r((c.get("implied_terminal_growth") or 0) * 100, 1),
                        "implied_wacc_pct": _r((c.get("implied_wacc") or 0) * 100, 1),
                        "market_ev_usd_bn": bn(c.get("market_ev"))}
    g = s.get("growth") or {}
    f["revenue_growth"] = {"path_pct": [_r(x * 100, 1) for x in (g.get("path") or [])],
                           "source_used": g.get("used"), "history_cagr_pct": _r((g.get("history") or 0) * 100, 1),
                           "consensus_year1_pct": _r(g["consensus_y1"] * 100, 1) if g.get("consensus_y1") is not None else None,
                           "consensus_year2_pct": _r(g["consensus_y2"] * 100, 1) if g.get("consensus_y2") is not None else None,
                           "analysts": g.get("analysts"), "flags": g.get("flags", [])}
    peers = [p for p in s.get("peers", []) if p.get("ev_ebitda")]
    if peers:
        ev = sorted(p["ev_ebitda"] for p in peers)
        f["peers"] = {"names": [p["name"] for p in peers], "ev_ebitda_x": [_r(x, 1) for x in ev],
                      "median_ev_ebitda_x": _r(ev[len(ev) // 2] if len(ev) % 2 else (ev[len(ev) // 2 - 1] + ev[len(ev) // 2]) / 2, 1)}
    f["review_items"] = [r.get("name") for r in s.get("review", [])]
    f["readings"] = readings(f)
    return f


def _cmp(a, b):
    return "above" if a > b else "below" if a < b else "equal to"


def readings(f):
    """Comparisons worked out here, so the model never has to judge 'higher' or 'lower' itself."""
    out = []
    price = f.get("share_price_usd")
    vals = {k: v["value"] for k, v in f["values_per_share_usd"].items()}
    if vals and price:
        lo, hi = min(vals.values()), max(vals.values())
        pos = "above the whole range" if price > hi else "below the whole range" if price < lo else "inside the range"
        out.append("The share price of $%.2f is %s of values per share ($%.2f to $%.2f)." % (price, pos, lo, hi))
        for k, v in f["values_per_share_usd"].items():
            out.append("%s: $%.2f, %s the price by %.1f%%." % (k, v["value"], _cmp(v["value"], price),
                                                                 abs(v["vs_price_pct"] or 0)))
    rd, w = f["reverse_dcf"], f["wacc_pct"]
    if rd.get("implied_terminal_growth_pct") is not None:
        g_dir = _cmp(rd["implied_terminal_growth_pct"], w["terminal_growth"])
        r_dir = _cmp(rd["implied_wacc_pct"], w["wacc"])
        meaning = ("the market expects more growth or less risk than the model" if g_dir == "above" or r_dir == "below"
                   else "the market expects less growth or more risk than the model")
        out.append("Reverse DCF: the price implies terminal growth of %.1f%%, %s the model's %.1f%%, or a WACC of %.1f%%, "
                   "%s the model's %.1f%%: %s." % (rd["implied_terminal_growth_pct"], g_dir, w["terminal_growth"],
                                                    rd["implied_wacc_pct"], r_dir, w["wacc"], meaning))
    g = f["revenue_growth"]
    if g.get("consensus_year1_pct") is not None and g.get("history_cagr_pct") is not None:
        out.append("Year-1 revenue growth from analyst consensus is %.1f%%, %s the %.1f%% historical growth." % (
            g["consensus_year1_pct"], _cmp(g["consensus_year1_pct"], g["history_cagr_pct"]), g["history_cagr_pct"]))
    if f.get("terminal_value_share_of_ev_pct") is not None:
        out.append("%.1f%% of enterprise value comes from the terminal value." % f["terminal_value_share_of_ev_pct"])
    return out


COMPARE = re.compile(r"(\d+(?:\.\d+)?)%[^.;]{0,80}?\b(higher|lower|above|below|greater|less)\b[^.;]{0,40}?(\d+(?:\.\d+)?)%", re.I)


def check_directions(text):
    """'x% ... higher/lower ... y%' must agree with the numbers."""
    for m in COMPARE.finditer(text):
        a, word, b = float(m.group(1)), m.group(2).lower(), float(m.group(3))
        up = word in ("higher", "above", "greater")
        if a != b and (a > b) != up:
            raise AIError("the draft says %s%% is %s %s%%" % (m.group(1), word, m.group(3)))


PROMPT = """You are an equity analyst writing a short valuation memo on {company} ({ticker}) for a manager or an
investment committee. Use ONLY the figures in the JSON below; do not calculate new numbers. The "readings" list already states every
comparison (above/below, more/less): reuse those statements and do not make other comparisons of your own.
Write 5 or 6 sentences in British English covering: the range of values per share from the three methods against
the share price; what the reverse DCF says the market is pricing in; the two or three assumptions the result
depends on most (WACC, terminal growth, revenue growth and where it came from, the terminal value share); and
anything that needs a second look (flags, review items).
This is analysis, not investment advice: do not recommend buying, selling or holding the shares and do not use
those words. Write per-share values as $ with two decimals (e.g. $49.46), enterprise values as $ billions with one
decimal (e.g. $226.8bn), percentages with one decimal and multiples like 13.9x. No bullet points, no markdown.

JSON:
{facts}"""


def _known(obj):
    out = []
    if isinstance(obj, dict):
        for v in obj.values():
            out += _known(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _known(v)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(abs(float(obj)))
    elif isinstance(obj, str):
        out += [float(x) for x in re.findall(r"\d+(?:\.\d+)?", obj.replace(",", ""))]
    return out


def verify(text, f):
    """Every $ amount, percentage and multiple in `text` must be one of the run's figures (as rounded)."""
    check_directions(text)
    if ADVICE.search(text):
        raise AIError("the draft contains a recommendation (%s); the memo must not give investment advice"
                      % ADVICE.search(text).group(0))
    known = _known(f)
    bad = []
    for m in re.finditer(r"(\$\s?\d[\d,]*(?:\.\d+)?\s?(?:bn|billion|m|k)?|\d[\d,]*(?:\.\d+)?\s?%|\d+(?:\.\d+)?x\b)", text):
        tok = m.group(1)
        num = re.search(r"\d[\d,]*(?:\.\d+)?", tok).group(0).replace(",", "")
        dec = len(num.split(".")[1]) if "." in num else 0
        v, tol = float(num), 0.5 * 10 ** (-dec) + 1e-9
        if not any(abs(v - k) <= tol for k in known):
            bad.append(tok.strip())
    if bad:
        raise AIError("the draft used figures that are not in the valuation: %s" % ", ".join(sorted(set(bad))))
    return True


def _post(url, headers, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json",
                                                                               **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code == 401:
            raise AIError("the API key was rejected (401)") from None
        if e.code == 429:
            raise AIError("the API account has no credit or hit a rate limit (429)") from None
        raise AIError("the AI service returned %d: %s" % (e.code, detail)) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError("the AI service could not be reached: %s" % e) from None


def call(cfg, prompt):
    if cfg["provider"] == "openai":
        r = _post("https://api.openai.com/v1/chat/completions", {"Authorization": "Bearer " + cfg["api_key"]},
                  {"model": cfg["model"], "temperature": 0.2, "messages": [{"role": "user", "content": prompt}]})
        return r["choices"][0]["message"]["content"].strip()
    r = _post("https://api.anthropic.com/v1/messages", {"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01"},
              {"model": cfg["model"], "max_tokens": 700, "messages": [{"role": "user", "content": prompt}]})
    return "".join(b.get("text", "") for b in r["content"]).strip()


def draft(summary, cfg=None, attempts=2):
    cfg = cfg or settings()
    if not cfg:
        raise AIError("no API key is set")
    f = facts(summary)
    prompt = PROMPT.format(company=summary.get("company"), ticker=summary.get("ticker"), facts=json.dumps(f, indent=1))
    last = None
    for _ in range(attempts):
        text = call(cfg, prompt)
        try:
            verify(text, f)
            return {"text": text, "provider": cfg["provider"], "model": cfg["model"], "checked": True}
        except AIError as e:
            last = e
            prompt += "\n\nYour previous draft was rejected because %s. Use only the figures in the JSON." % e
    raise last
