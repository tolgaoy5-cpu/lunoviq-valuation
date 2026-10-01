"""AI valuation memo: facts, the figure check, the no-advice rule, settings. The AI service is mocked."""
import os
import stat

import pytest

from lunoviq import ai

SUMMARY = {
    "ticker": "KO", "company": "COCA COLA CO", "generated": "2026-09-25T17:36:27", "price": 87.98, "unit_div": 1e6,
    "methods": [{"key": "dcf_perpetuity", "label": "DCF Perpetuity", "value": 49.4616, "low": 34.92, "high": 106.15},
                {"key": "dcf_exit", "label": "DCF Exit Multiple", "value": 52.44, "low": 45.1, "high": 61.2},
                {"key": "trading_comps", "label": "Trading Comparables", "value": 44.77, "low": 40.0, "high": 50.0},
                {"key": "precedents", "label": "Precedent Transactions", "value": None}],
    "wacc": {"wacc": 0.07127, "cost_of_equity": 0.07418, "after_tax_kd": 0.0470, "terminal_growth": 0.025,
             "risk_free": 0.0518, "erp": 0.0414, "pretax_kd": 0.0573, "tax_rate": 0.18, "beta": 0.54,
             "equity_weight": 0.8929, "debt_weight": 0.1071},
    "bridge": {"pv_discrete": 45338.0, "pv_terminal": 181481.0, "ev": 226819.0, "equity": 213300.0, "exit_multiple": 16.9},
    "dcf_checks": {"implied_terminal_growth": 0.0466, "implied_wacc": 0.052, "market_ev": 392948.7},
    "growth": {"path": [0.0371, 0.0025, 0.01, 0.0175, 0.025], "used": "consensus", "history": 0.0553,
               "consensus_y1": 0.0371, "consensus_y2": 0.0025, "analysts": 18, "flags": []},
    "peers": [{"name": "PepsiCo, Inc. (PEP)", "ev_ebitda": 13.95}, {"name": "Keurig Dr Pepper (KDP)", "ev_ebitda": 13.7},
              {"name": "Monster Beverage (MNST)", "ev_ebitda": 15.4}],
    "review": [{"name": "val_TerminalGrowth"}], "health": {"Overall": "OK"},
    "audit": {"lines_checked": 190, "mismatches": 0},
}

GOOD = ("Coca-Cola is valued at $44.77 to $52.44 per share across the three methods, against a share price of $87.98, "
        "with the perpetuity DCF at $49.46. The reverse DCF shows the market is pricing in 4.7% terminal growth, or a "
        "WACC of 5.2% instead of 7.1%. About 80.0% of the enterprise value of $226.8bn comes from the terminal value, "
        "so the 2.5% terminal growth assumption matters most. Revenue growth starts from analyst consensus of 3.7%, "
        "below the 5.5% history, and peers trade at a median 13.9x EV/EBITDA. Share buybacks are not modelled.")


def test_facts_are_in_display_units():
    f = ai.facts(SUMMARY)
    assert f["values_per_share_usd"]["DCF Perpetuity"]["value"] == 49.46
    assert f["enterprise_value_usd_bn"] == 226.8 and f["reverse_dcf"]["implied_terminal_growth_pct"] == 4.7
    assert f["wacc_pct"]["wacc"] == 7.1 and f["peers"]["median_ev_ebitda_x"] == 13.9
    assert "Precedent Transactions" not in f["values_per_share_usd"]


def test_verify_accepts_true_figures_and_rejects_invented_ones():
    f = ai.facts(SUMMARY)
    assert ai.verify(GOOD, f)
    with pytest.raises(ai.AIError, match=r"\$63.10"):
        ai.verify(GOOD.replace("$52.44", "$63.10"), f)
    with pytest.raises(ai.AIError, match="9.9%"):
        ai.verify(GOOD.replace("4.7% terminal", "9.9% terminal"), f)
    with pytest.raises(ai.AIError, match="18.5x"):
        ai.verify(GOOD.replace("13.9x", "18.5x"), f)


@pytest.mark.parametrize("phrase", ["We would buy the shares.", "Our rating is Hold.", "The stock is a strong buy."])
def test_investment_advice_is_rejected(phrase):
    with pytest.raises(ai.AIError, match="recommendation"):
        ai.verify(GOOD + " " + phrase, ai.facts(SUMMARY))


def test_draft_retries_then_succeeds(monkeypatch):
    answers = iter([GOOD + " Buy the shares.", GOOD])
    prompts = []
    monkeypatch.setattr(ai, "call", lambda cfg, p: (prompts.append(p), next(answers))[1])
    res = ai.draft(SUMMARY, {"provider": "openai", "api_key": "x", "model": "m"})
    assert res["text"] == GOOD and len(prompts) == 2 and "recommendation" in prompts[1]


def test_settings_are_shared_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "SETTINGS", tmp_path / ".lunoviq" / "ai.toml")
    for v in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    assert ai.settings() is None
    with pytest.raises(ai.AIError):
        ai.save_settings("openai", "short")
    ai.save_settings("openai", "sk-test_" + "c" * 30)
    assert ai.settings()["model"] == "gpt-4o-mini"
    assert stat.S_IMODE(os.stat(tmp_path / ".lunoviq" / "ai.toml").st_mode) == 0o600
    ai.clear_settings()
    assert ai.settings() is None


def test_readings_state_directions_and_wrong_directions_are_rejected():
    f = ai.facts(SUMMARY)
    rd = " ".join(f["readings"])
    assert "WACC of 5.2%, below the model's 7.1%" in rd and "terminal growth of 4.7%, above the model's 2.5%" in rd
    assert "above the whole range" in rd
    with pytest.raises(ai.AIError, match="5.2% is higher"):
        ai.verify(GOOD + " The implied WACC of 5.2% is higher than the model's 7.1%.", f)
    assert ai.verify(GOOD + " The implied WACC of 5.2% is lower than the model's 7.1%.", f)
