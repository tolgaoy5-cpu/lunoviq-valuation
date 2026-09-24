"""Backward-compatible CLI; implementation in lunoviq/excel/recalc.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lunoviq.excel.recalc import main, recalc  # noqa: E402,F401

if __name__ == "__main__":
    main()
