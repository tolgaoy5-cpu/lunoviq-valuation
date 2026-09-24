"""
Read-only workbook inspector / differ.

Parses the raw OOXML (zip) directly, so it works on files that openpyxl
refuses to load (e.g. the WPS-saved workbooks, whose chart XML openpyxl
rejects) and on files written with an `x:` namespace prefix. Shared formulas
are expanded to their per-cell text so that diffs compare real formulas.

Never writes to any workbook.

Usage:
    python tools/xlsx_inspect.py summary <book.xlsx>
    python tools/xlsx_inspect.py diff <a.xlsx> <b.xlsx> [--sheet NAME] [--limit N]
"""
import argparse
import html
import re
import sys
import zipfile


def _strip_prefix(xml):
    return re.sub(r"<(/?)x:", r"<\1", xml)


def cells(path):
    """{sheet: {ref: (kind, text, cached_value)}}; kind is 'F' (formula) or 'V' (value)."""
    from openpyxl.formula.translate import Translator

    z = zipfile.ZipFile(path)
    wb = _strip_prefix(z.read("xl/workbook.xml").decode())
    rels = z.read("xl/_rels/workbook.xml.rels").decode()

    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        sst = _strip_prefix(z.read("xl/sharedStrings.xml").decode())
        for si in re.findall(r"<si>(.*?)</si>", sst, re.S):
            ss.append(html.unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))))

    rid = {}
    for rel in re.findall(r"<Relationship [^>]*>", rels):
        rid[re.search(r'Id="([^"]+)"', rel).group(1)] = re.search(r'Target="([^"]+)"', rel).group(1)

    out = {}
    for m in re.finditer(r"<sheet [^>]*>", wb):
        tag = m.group(0)
        name = html.unescape(re.search(r'name="([^"]+)"', tag).group(1))
        target = rid[re.search(r'r:id="([^"]+)"', tag).group(1)].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        x = _strip_prefix(z.read(target).decode())
        d, shared = {}, {}
        for c in re.finditer(r'<c r="([A-Z]+\d+)"([^>]*?)(?:/>|>(.*?)</c>)', x, re.S):
            ref, attr, body = c.groups()
            body = body or ""
            fm = re.search(r"<f([^>]*)>(.*?)</f>", body, re.S)
            fe = re.search(r'<f[^>]*si="(\d+)"[^>]*/>', body)
            v = re.search(r"<v>(.*?)</v>", body)
            cached = v.group(1) if v else None
            if fm and 'si="' in fm.group(1):
                si = re.search(r'si="(\d+)"', fm.group(1)).group(1)
                shared[si] = (ref, "=" + html.unescape(fm.group(2)))
            if fe and not fm:                       # shared-formula child cell
                origin, fx = shared[fe.group(1)]
                d[ref] = ("F", Translator(fx, origin=origin).translate_formula(ref), cached)
                continue
            isv = re.search(r"<is>.*?<t[^>]*>(.*?)</t>", body, re.S)
            if fm:
                d[ref] = ("F", "=" + html.unescape(fm.group(2)), cached)
            elif v:
                val = ss[int(cached)] if 't="s"' in attr else cached
                d[ref] = ("V", html.unescape(val), None)
            elif isv:
                d[ref] = ("V", html.unescape(isv.group(1)), None)
            if ref in d and d[ref][1] == "":
                del d[ref]
        out[name] = d
    return out


def defined_names(path):
    wb = _strip_prefix(zipfile.ZipFile(path).read("xl/workbook.xml").decode())
    return {html.unescape(n): html.unescape(v)
            for n, v in re.findall(r'<definedName [^>]*name="([^"]+)"[^>]*>(.*?)</definedName>', wb)}


def diff(a, b):
    """Sorted refs whose (kind, text) differ between two sheet dicts."""
    return sorted(k for k in set(a) | set(b)
                  if (a.get(k) or (None, None))[:2] != (b.get(k) or (None, None))[:2])


def summary(path):
    book = cells(path)
    print("%s  (defined names: %d)" % (path, len(defined_names(path))))
    for s, d in book.items():
        f = [v for v in d.values() if v[0] == "F"]
        cached = sum(1 for v in f if v[2] not in (None, ""))
        errs = sum(1 for v in f if v[2] and v[2].startswith("#"))
        print("  %-26s cells=%5d formulas=%5d cached=%5d errors=%d" % (s, len(d), len(f), cached, errs))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary"); s.add_argument("book")
    d = sub.add_parser("diff"); d.add_argument("a"); d.add_argument("b")
    d.add_argument("--sheet"); d.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "summary":
        summary(a.book)
        return
    A, B = cells(a.a), cells(a.b)
    for sh in sorted(set(A) | set(B)):
        if a.sheet and sh != a.sheet:
            continue
        refs = diff(A.get(sh, {}), B.get(sh, {}))
        print("%-26s %d differing cells" % (sh, len(refs)))
        for r in refs[:a.limit]:
            print("    %-6s A: %s\n           B: %s" % (r, (A.get(sh, {}).get(r) or ("", ""))[1][:90],
                                                     (B.get(sh, {}).get(r) or ("", ""))[1][:90]))


if __name__ == "__main__":
    sys.exit(main())
