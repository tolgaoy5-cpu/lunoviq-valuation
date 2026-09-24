"""
Recalculate a workbook with the installed Microsoft Excel (via xlwings), then
save it so every formula has a cached value that Python can read and validate.

Excel runs hidden in its own instance. Only pass copies or pipeline output,
never the master template.

Usage:
    python -m lunoviq.excel.recalc <in.xlsx> [--out <out.xlsx>]   (default: in place)
"""
import argparse
import shutil
from pathlib import Path


def recalc(path_in, path_out=None):
    import xlwings as xw

    src = Path(path_in).resolve()
    dst = Path(path_out).resolve() if path_out else src
    if dst != src:
        shutil.copy2(src, dst)
    app = xw.App(visible=False, add_book=False)
    try:
        app.display_alerts = False
        app.screen_updating = False
        wb = app.books.open(str(dst), update_links=False)
        app.calculation = "automatic"
        app.calculate()                      # template has fullCalcOnLoad=1 as well
        wb.save()
        wb.close()
    finally:
        app.quit()
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("--out")
    a = ap.parse_args()
    print("Recalculated:", recalc(a.book, a.out))


if __name__ == "__main__":
    main()
