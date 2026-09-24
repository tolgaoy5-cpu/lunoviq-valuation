"""
Settings loader.

Precedence (highest first):
    1. environment variables  (LUNOVIQ_SEC_USER_AGENT, LUNOVIQ_CACHE_DIR)
    2. config/lunoviq.toml    (local, git-ignored: personal contact details live here)
    3. config/lunoviq.example.toml (committed defaults)
"""
import os
import tomllib
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@lru_cache(maxsize=1)
def settings():
    cfg = {}
    for name in ("lunoviq.example.toml", "lunoviq.toml"):
        p = CONFIG_DIR / name
        if p.exists():
            cfg = _merge(cfg, tomllib.loads(p.read_text(encoding="utf-8")))
    env = {
        ("sec", "user_agent"): os.environ.get("LUNOVIQ_SEC_USER_AGENT"),
        ("http", "cache_dir"): os.environ.get("LUNOVIQ_CACHE_DIR"),
    }
    for (section, key), val in env.items():
        if val:
            cfg.setdefault(section, {})[key] = val
    return cfg


def get(path, default=None):
    """get("wacc.credit_spread") -> value, or default if absent."""
    node = settings()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def sec_user_agent():
    ua = get("sec.user_agent")
    if not ua or "example.com" in ua:
        raise SystemExit(
            "SEC requires a contact User-Agent. Set it in config/lunoviq.toml:\n"
            '    [sec]\n    user_agent = "YourApp your.email@domain.com"\n'
            "or export LUNOVIQ_SEC_USER_AGENT.")
    return ua
