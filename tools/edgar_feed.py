"""
Backward-compatible entry point. The implementation lives in
lunoviq/providers/sec_xbrl.py; existing commands keep working:

    python tools/edgar_feed.py KO --facts ko.json --out KO.xlsx
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lunoviq.providers.sec_xbrl import *          # noqa: E402,F401,F403
from lunoviq.providers.sec_xbrl import main       # noqa: E402,F401

if __name__ == "__main__":
    main()
