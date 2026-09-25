"""
Display unit of a model: USD thousands or USD millions, chosen per company.

Large companies are shown in millions, as in professional models: KO revenue
149,300 instead of 149,299,623. Every amount written to the workbook and the
share count use the same divisor, so per-share values are unchanged.

The pipeline sets the unit for one run and restores the default afterwards.
"""
DEFAULT = 1e3
MILLIONS_FROM_REVENUE = 1e9          # revenue >= $1bn -> USD millions

DIV = DEFAULT


def choose(revenue):
    return 1e6 if revenue and abs(revenue) >= MILLIONS_FROM_REVENUE else 1e3


def set_div(div):
    global DIV
    DIV = float(div)


def scale(value):
    return None if value is None else value / DIV


def label():
    return "USD millions" if DIV == 1e6 else "USD thousands"


def short():
    return "$m" if DIV == 1e6 else "$000"


def shares_label():
    return "m" if DIV == 1e6 else "000s"
