import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def pytest_collection_modifyitems(config, items):
    """Tests marked `excel` drive Microsoft Excel; run them with RUN_EXCEL_TESTS=1."""
    if os.environ.get("RUN_EXCEL_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_EXCEL_TESTS=1 to run Excel recalculation tests")
    for item in items:
        if "excel" in item.keywords:
            item.add_marker(skip)
