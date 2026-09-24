"""
Company-specific content for sheets that are not driven by named ranges:
the trading-comparables peer table, the precedent-transaction block, and
hiding the unit-economics schedules that the Growth-Margin engine ignores.
Every write targets a constant cell; formula cells are refused.
"""
from .writer import FormulaProtectedError

COMP = "06_Comparable_Valuation"
PEER_ROWS = range(7, 11)
DEAL_ROWS = range(41, 46)

# Rows of the unit-economics (volume x price, plant capacity) engine. They hold
# the demo manufacturer's data and do not feed the Growth-Margin model.
UNIT_ECONOMICS_ROWS = {
    "01_Inputs_Historicals": [*range(6, 15), 39, 40, 41, 42, 73, *range(91, 96)],
    "02_Operating_Model": [*range(8, 30), 32, 33, *range(37, 78), 79, *range(231, 241), 245],
}


def _put(ws, ref, value):
    cell = ws[ref]
    if isinstance(cell.value, str) and cell.value.startswith("="):
        raise FormulaProtectedError("%s!%s holds a formula" % (ws.title, ref))
    cell.value = value


def write_peers(wb, peers):
    """Peer table in USD thousands: name, EV, revenue, EBITDA, net income, market cap."""
    ws = wb[COMP]
    k = lambda v: None if v is None else v / 1000
    for i, r in enumerate(PEER_ROWS):
        p = peers[i] if i < len(peers) else None
        vals = {"A": None, "B": None, "C": None, "D": None, "E": None, "I": None, "J": None, "K": None}
        if p:
            vals.update(A="%s (%s)" % (p["name"][:26], p["ticker"]), B=k(p["ev"]), C=k(p["revenue"]),
                        D=k(p["ebitda"]), E=k(p["net_income"]), I="US", J=p.get("rationale", ""),
                        K=k(p["market_cap"]))
        for col, v in vals.items():
            _put(ws, "%s%d" % (col, r), v)
    return len(peers)


def tidy(wb):
    """Readability fixes on the generated copy (never on the template)."""
    comp = wb[COMP]
    comp.column_dimensions["J"].width = 60           # peer rationale
    comp.column_dimensions["K"].width = 16           # market cap
    inp = wb["01_Inputs_Historicals"]
    if isinstance(inp["C102"].value, str) and "hacim" in inp["C102"].value:
        inp["C102"].value = ("Unit Economics = volume x price (needs internal data). Growth-Margin = revenue "
                             "growth and cost margins from filings; used for listed companies.")


def clear_precedents(wb):
    """No free source for M&A transaction data: leave the block empty (template
    v6 then shows the method as n/a) rather than showing the demo deals."""
    ws = wb[COMP]
    for r in DEAL_ROWS:
        for col in "ABCDEFGHIJ":
            _put(ws, "%s%d" % (col, r), None)


def hide_unit_economics(wb):
    for sheet, rows in UNIT_ECONOMICS_ROWS.items():
        for r in rows:
            wb[sheet].row_dimensions[r].hidden = True


def print_setup(wb, helper_col=24):
    """Landscape, one page wide, print area = used cells left of the chart-helper
    columns (X onward), so the workbook prints / exports to PDF as readable pages
    instead of hundreds of strips."""
    from openpyxl.utils import get_column_letter
    for ws in wb.worksheets:
        last_col, last_row = 1, 1
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None and c.column < helper_col and not ws.row_dimensions[c.row].hidden:
                    last_col, last_row = max(last_col, c.column), max(last_row, c.row)
        ws.print_area = "A1:%s%d" % (get_column_letter(last_col), last_row)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_options.horizontalCentered = True
        ws.page_margins.left = ws.page_margins.right = 0.4
        ws.oddFooter.center.text = "&A  |  page &P of &N"
