"""Provider registry: role -> implementation, chosen in config/lunoviq.toml [providers]."""
from .. import config
from .estimates import StockAnalysisEstimates
from .rates import DamodaranProvider, FredProvider, TreasuryProvider
from .sec_edgar import SecEdgarProvider
from .yahoo import YahooProvider

REGISTRY = {
    "fundamentals": {"sec_edgar": SecEdgarProvider},
    "prices": {"yahoo": YahooProvider},
    "risk_free": {"treasury": TreasuryProvider, "fred": FredProvider},
    "equity_risk_premium": {"damodaran": DamodaranProvider},
    "estimates": {"stockanalysis": StockAnalysisEstimates},
}
DEFAULTS = {"fundamentals": "sec_edgar", "prices": "yahoo",
            "risk_free": ["treasury", "fred"], "equity_risk_premium": "damodaran",
            "estimates": "stockanalysis"}


def get(role, **kwargs):
    """Return the configured provider (or ordered list of providers) for a role."""
    choice = config.get("providers.%s" % role, DEFAULTS[role])
    names = choice if isinstance(choice, list) else [choice]
    unknown = [n for n in names if n not in REGISTRY[role]]
    if unknown:
        raise SystemExit("Unknown %s provider(s) %s; available: %s"
                         % (role, unknown, sorted(REGISTRY[role])))
    objs = [REGISTRY[role][n](**kwargs) for n in names]
    return objs if isinstance(choice, list) else objs[0]
