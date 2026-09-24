"""
Backward-compatible entry point. The implementation lives in
lunoviq/providers/sec_xbrl.py; existing commands keep working:

    python edgar_feed.py KO --facts ko.json --out KO.xlsx
"""
from lunoviq.providers.sec_xbrl import *          # noqa: F401,F403
from lunoviq.providers.sec_xbrl import main       # noqa: F401

if __name__ == "__main__":
    main()
