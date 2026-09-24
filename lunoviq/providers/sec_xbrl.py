"""
Lunoviq — SEC EDGAR veri besleyici
----------------------------------
Ticker veya CIK verilir; SEC companyfacts API'sinden son 3 mali yil cekilir,
08_Data_Feed sayfasina yazilir ve 01_Inputs tarihsel blogu doldurulur.

(Paket ici konum: lunoviq/providers/sec_xbrl.py — kok dizindeki edgar_feed.py
geriye uyumluluk icin bunu yeniden disa aktarir.)

Kullanim:
    python edgar_feed.py AAPL  --model Lunoviq_Master_Financial_Model_v2.xlsx
    python edgar_feed.py 0000320193 --out Apple_Model.xlsx

Not: SEC kullanim politikasi User-Agent zorunlu kilar ve saniyede 10 istek siniri koyar.
"""
import json, re, sys, time, argparse, datetime as dt
from pathlib import Path

BASE = "https://data.sec.gov"
TICKERS = "https://www.sec.gov/files/company_tickers.json"

# ---------------------------------------------------------------- eşleme sözlüğü
# Her kalem icin oncelik sirali XBRL etiketleri. Ilk bulunan kullanilir.
TAGS = {
    "Revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "Cost of Goods Sold": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "SG&A": ["SellingGeneralAndAdministrativeExpense",
             "GeneralAndAdministrativeExpense",
             # Son care: SG&A'yi ayri raporlamayan sirketler (orn. MNST) toplam
             # faaliyet giderini verir. Ar-Ge'si olan sirketlerde SG&A etiketi zaten
             # bulundugu icin buraya dusulmez.
             "OperatingExpenses"],
    "Other Operating Expense": ["OtherCostAndExpenseOperating",
                                "OtherOperatingIncomeExpenseNet",
                                "ResearchAndDevelopmentExpense"],
    "Depreciation & Amortisation": ["DepreciationDepletionAndAmortization",
                                    "DepreciationAmortizationAndAccretionNet",
                                    "DepreciationAndAmortization", "Depreciation"],
    "Interest Expense": ["InterestExpense", "InterestExpenseDebt",
                         "InterestExpenseNonoperating",          # 2024+ taksonomi (orn. AMZN)
                         "InterestIncomeExpenseNet"],
    "Net Income": ["NetIncomeLoss", "ProfitLoss"],
    "Accounts Receivable": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                            "AccountsNotesAndLoansReceivableNetCurrent"],   # orn. PEP
    "Inventory": ["InventoryNet", "InventoryFinishedGoods"],
    "PP&E (net)": ["PropertyPlantAndEquipmentNet"],
    # Isletme sermayesi (DPO) icin SAF ticari borc gerekir. AP+tahakkuk eden
    # giderler karisik bir kalemdir ve DPO'yu sisirir -> en son care.
    "Accounts Payable": ["AccountsPayableCurrent",
                         "AccountsPayableTradeCurrent",
                         "AccountsPayableAndAccruedLiabilitiesCurrent"],
    "Deferred Tax Liability": ["DeferredIncomeTaxLiabilitiesNet",
                               "DeferredTaxLiabilitiesNoncurrent",
                               "DeferredIncomeTaxesAndOtherTaxLiabilitiesNoncurrent"],
    "Retained Earnings": ["RetainedEarningsAccumulatedDeficit"],
    "Tax Loss Carryforward": ["OperatingLossCarryforwards"],
    # Raporlanan toplamlar -> bilancoyu denklestiren tamamlayicilari hesaplamak icin
    "Total Assets (reported)": ["Assets"],
    "Total Equity (reported)": ["StockholdersEquity",
                                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "Total Liabilities (reported)": ["Liabilities"],
    # Gelir tablosunu raporlanan faaliyet karina baglamak icin (asagidaki mutabakat)
    "Operating Income (reported)": ["OperatingIncomeLoss"],
    "Pre-tax Income (reported)": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"],
}
# toplanarak elde edilenler
# Once tek etiketle dene (sirket toplami zaten veriyorsa onu kullan),
# bulunamazsa bilesenleri topla.
SUMS = {
    # Tarif = (ZORUNLU etiketler, OPSIYONEL etiketler)
    # Bir tarif, ZORUNLU etiketlerinin HEPSI o yil icin bulunursa kullanilir.
    # Boylece sirket etiket degistirdiginde seri kopmaz ve eksik bilesen
    # sessizce dusuk toplam uretmez.
    "Total Debt": {
        "single": ["DebtLongtermAndShorttermCombinedAmount"],
        "recipes": [
            (["LongTermDebtAndCapitalLeaseObligations"],
             ["LongTermDebtAndCapitalLeaseObligationsCurrent",
              "CommercialPaper", "OtherShortTermBorrowings"]),
            (["LongTermDebtNoncurrent"],
             ["LongTermDebtCurrent", "CommercialPaper",
              "OtherShortTermBorrowings", "ShortTermBorrowings"]),
            (["LongTermDebt"], ["DebtCurrent"]),
        ],
    },
    # Cari vergi: toplam etiket yoksa federal + eyalet + yabanci toplanir.
    # (AMZN 2025'te yalniz federal etiketi var; tek basina almak tanimi yillar
    # arasinda karistiriyordu.)
    "Current Income Tax": {
        "single": [],
        "recipes": [
            (["CurrentIncomeTaxExpenseBenefit"], []),
            (["CurrentFederalTaxExpenseBenefit"],
             ["CurrentStateAndLocalTaxExpenseBenefit", "CurrentForeignTaxExpenseBenefit"]),
        ],
    },
    # Nakit = nakit ve benzerleri + kisa vadeli yatirimlar/menkul kiymetler.
    # Net borc (DCF ozkaynak koprusu) bu tanimla hesaplanir; yalniz nakit
    # alininca yatirim portfoyu buyuk sirketlerde (AAPL, GOOGL, MSFT) net borc
    # olmasi gerekenden yuksek cikiyordu.
    "Cash & Equivalents": {
        "single": [],
        "recipes": [
            # Her yil TEK bir menkul kiymet etiketi kullanilir: sirketler ayni tutari
            # birden cok etiketle raporlayabiliyor (orn. TSLA'da MarketableSecurities-
            # Current = ShortTermInvestments), toplamak cift sayim yapar.
            (["CashCashEquivalentsAndShortTermInvestments"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "MarketableSecuritiesCurrent"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "ShortTermInvestments"], []),
            (["CashAndCashEquivalentsAtCarryingValue", "AvailableForSaleSecuritiesDebtSecurities"], []),  # NVDA
            (["CashAndCashEquivalentsAtCarryingValue"], []),
            (["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], []),
        ],
    },
    "Common Equity": {
        "single": [],
        "recipes": [
            (["CommonStockValue", "AdditionalPaidInCapital"], []),
            (["CommonStockValue", "AdditionalPaidInCapitalCommonStock"], []),
            (["CommonStocksIncludingAdditionalPaidInCapital"], []),
            (["CommonStockValueOutstanding", "AdditionalPaidInCapitalCommonStock"], []),   # orn. MNST
            (["CommonStockValue"], []),
        ],
    },
}
# 'Issued' hisse, hazine hisselerini de icerir -> gercek dolasimdaki sayiyi
# asiri gosterir. Once seyreltilmis agirlikli ortalama, sonra outstanding.
SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding",
          "WeightedAverageNumberOfSharesOutstandingBasic",
          "CommonStockSharesOutstanding",
          "EntityCommonStockSharesOutstanding",
          "CommonStockSharesIssued"]

ORDER = ["Revenue", "Cost of Goods Sold", "SG&A", "Other Operating Expense",
         "Depreciation & Amortisation", "Interest Expense", "Current Income Tax",
         "Net Income", "Cash & Equivalents", "Accounts Receivable", "Inventory",
         "PP&E (net)", "Accounts Payable", "Total Debt", "Deferred Tax Liability",
         "Common Equity", "Retained Earnings", "Tax Loss Carryforward",
         "Shares Outstanding",
         "Total Assets (reported)", "Total Liabilities (reported)",
         "Total Equity (reported)", "Operating Income (reported)",
         "Pre-tax Income (reported)"]


class DataError(ValueError):
    """Sirketin SEC verisi modeli kurmaya yetmiyor."""


# ---------------------------------------------------------------- ag katmani
def _get(url):
    import urllib.request
    from lunoviq.config import sec_user_agent
    req = urllib.request.Request(url, headers={"User-Agent": sec_user_agent(),
                                               "Accept-Encoding": "gzip, deflate"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    return json.loads(raw)


def resolve_cik(token):
    """Ticker -> 10 haneli CIK. Zaten CIK ise dogrudan dondurur."""
    t = token.strip().upper()
    if re.fullmatch(r"\d{1,10}", t):
        return t.zfill(10)
    data = _get(TICKERS)
    for row in data.values():
        if row["ticker"].upper() == t:
            return str(row["cik_str"]).zfill(10)
    raise SystemExit("Ticker bulunamadi: %s" % token)


def fetch_facts(cik):
    time.sleep(0.15)                       # SEC hiz siniri
    return _get("%s/api/xbrl/companyfacts/CIK%s.json" % (BASE, cik))


# ---------------------------------------------------------------- donusturme
def fiscal_year_ends(facts):
    """Sirketin mali yil sonu tarihlerini bulur: {yil: 'YYYY-MM-DD'}.

    Gelir kaleminin yillik (330-400 gun) kayitlarindan turetilir. 52/53 haftalik
    takvim kullanan sirketlerde tarih her yil birkac gun kayar; bu yuzden sabit
    bir ay/gun varsaymak yerine gercek tarihler toplanir.

    Duzeltme (2026-09): eskiden ILK bulunan etiketin yillariyla yetiniliyordu.
    Sirket etiket degistirdiginde (orn. NVDA 2022'de) sonraki yillar kayboluyor,
    bilanco kalemleri de bu yuzden eslesemiyordu. Artik tum gelir etiketleri
    birlestirilir ve yalnizca 10-K kayitlari kullanilir (10-Q'daki 12 aylik
    kayitlar kapanmamis bir mali yil uretmesin).
    """
    ends = {}
    for taxo in ("us-gaap", "ifrs-full"):
        for tag in TAGS["Revenue"] + ["Revenues", "NetIncomeLoss"]:
            node = facts.get("facts", {}).get(taxo, {}).get(tag)
            if not node:
                continue
            for unit, rows in node.get("units", {}).items():
                if unit != "USD":
                    continue
                for r in rows:
                    if "start" not in r or not r.get("form", "").startswith("10-K"):
                        continue
                    d0 = dt.date.fromisoformat(r["start"])
                    d1 = dt.date.fromisoformat(r["end"])
                    if not 330 <= (d1 - d0).days <= 400:
                        continue
                    y = d1.year if d1.month > 5 else d1.year - 1
                    # ayni yil icin en gec tarihi tut (yil sonu bilancosuyla eslesir)
                    if y not in ends or r["end"] > ends[y]:
                        ends[y] = r["end"]
        if ends:
            break                                  # us-gaap bulunduysa ifrs'e bakma
    return ends


def annual_series(facts, tag, fye):
    """{mali_yil: (deger, dosyalama_tarihi)}.

    Iki tuzagi birden asar:
      1) SEC'in 'fy' alani DOSYALAMANIN yilidir, verinin ait oldugu yil degildir.
         Donem her zaman 'end' tarihinden turetilir.
      2) Bilanco kalemleri ceyreklik anlik goruntuler olarak da raporlanir.
         Yalnizca mali yil sonuna denk gelen (+/- 7 gun) kayitlar alinir.
    """
    for taxo in ("us-gaap", "ifrs-full", "dei"):
        node = facts.get("facts", {}).get(taxo, {}).get(tag)
        if not node:
            continue
        for unit, rows in node.get("units", {}).items():
            if unit not in ("USD", "shares", "USD/shares"):
                continue
            out = {}
            for r in rows:
                end = r.get("end")
                if not end:
                    continue
                d1 = dt.date.fromisoformat(end)
                if "start" in r:                          # AKIS kalemi
                    d0 = dt.date.fromisoformat(r["start"])
                    if not 330 <= (d1 - d0).days <= 400:
                        continue
                    fy = d1.year if d1.month > 5 else d1.year - 1
                else:                                     # ANLIK (bilanco) kalemi
                    fy = None
                    for y, anchor in fye.items():
                        if abs((d1 - dt.date.fromisoformat(anchor)).days) <= 7:
                            fy = y
                            break
                    if fy is None:
                        continue                          # ceyreklik goruntu -> atla
                filed = r.get("filed", "")
                form = r.get("form", "")
                rank = 0 if form.startswith("10-K") else 1
                # Secim olcutu: once 10-K (rank kucuk), esitlikte en son dosyalanan.
                score = (-rank, filed)
                prev = out.get(fy)
                if prev is None or score > prev[2]:
                    out[fy] = (r["val"], filed, score)
            if out:
                return {k: (v[0], v[1]) for k, v in out.items()}, tag
    return {}, None


def pick(facts, tags, fye):
    """Her yil icin, o yili iceren en oncelikli etiketin degeri.

    Eskiden ilk dolu seri butunuyle alinirdi; sirket etiket degistirince
    (orn. WMT amortisman, TSLA amortisman, AMZN vergi) son yillar bos kaliyordu.
    Donen etiket metni yil sirasiyla kullanilan etiketleri ' | ' ile listeler.
    """
    merged, notes = {}, {}
    for t in tags:
        s, used = annual_series(facts, t, fye)
        for y, v in s.items():
            if y not in merged:
                merged[y], notes[y] = v, used
    if not merged:
        return {}, None
    uniq = []
    for y in sorted(notes):
        if notes[y] not in uniq:
            uniq.append(notes[y])
    return merged, " | ".join(uniq)


def build_feed(facts, n_years=3):
    """{kalem: {yil: deger}} + hangi etiketin kullanildigi."""
    fye = fiscal_year_ends(facts)
    if not fye:
        raise DataError("SEC companyfacts icinde 10-K gelir kaydi yok (mali yil sonu bulunamadi). "
                        "Sirket yillik veriyi XBRL'de yayinlamamis olabilir.")
    series, used = {}, {}
    for item in ORDER:
        if item in TAGS:
            s, u = pick(facts, TAGS[item], fye)
            series[item] = {k: v[0] for k, v in s.items()}
            used[item] = u or "BULUNAMADI"

        elif item in SUMS:
            # "single" (tek etikette toplam) EN DUSUK oncelikli tarif olarak eklenir.
            # Boyle bir etiket bulunabilir ama yalnizca cok eski yillari kapsiyor
            # olabilir (orn. KDP'de DebtLongtermAndShorttermCombinedAmount sadece
            # 2017). Once "bulundu" deyip durmak seriyi kaybettiriyordu; artik
            # her yil ayri degerlendiriliyor.
            recipes = list(SUMS[item]["recipes"]) + [([t], []) for t in SUMS[item]["single"]]
            if True:
                # Etiket kaymasi: sirket yillar icinde etiket degistirebilir
                # (orn. Coca-Cola 2024'te LongTermDebtNoncurrent -> ...AndCapitalLeaseObligations).
                # Her yil icin, ZORUNLU bilesenleri bulunan ilk tarif kullanilir.
                cache = {}
                for req, opt in recipes:
                    for t in req + opt:
                        if t not in cache:
                            ss, _ = annual_series(facts, t, fye)
                            cache[t] = {fy: v[0] for fy, v in ss.items()}
                allyears = sorted({y for d in cache.values() for y in d})
                tot, notes = {}, {}
                for y in allyears:
                    for req, opt in recipes:
                        if not all(y in cache[t] for t in req):
                            continue                      # zorunlu eksik -> sonraki tarif
                        have = req + [t for t in opt if y in cache[t]]
                        tot[y] = sum(cache[t][y] for t in have)
                        notes[y] = " + ".join(have)
                        break
                series[item] = tot
                if notes:
                    uniq = []
                    for y in sorted(notes):
                        if notes[y] not in uniq:
                            uniq.append(notes[y])
                    used[item] = " | ".join(uniq)
                else:
                    used[item] = "BULUNAMADI"

        elif item == "Shares Outstanding":
            s, u = pick(facts, SHARES, fye)
            series[item] = {k: v[0] for k, v in s.items()}
            used[item] = u or "BULUNAMADI"

    # ---- Bilancoyu denklestiren tamamlayicilar --------------------------------
    # Model dort aktif ve uc pasif kalemi taniyor. Gercek bir sirkette serefiye,
    # maddi olmayan duran varlik, istirakler, hazine hissesi, AOCI gibi kalemler
    # de var. Raporlanan toplamlardan artik olarak hesaplanip modele verilir;
    # boylece bilanco her sirkette birebir denk kalir.
    def g(item, y):
        return series.get(item, {}).get(y)

    TA, TL, TE = "Total Assets (reported)", "Total Liabilities (reported)", "Total Equity (reported)"
    oa, ol, oe = {}, {}, {}
    for y in sorted({fy for s in series.values() for fy in s}):
        ta, te = g(TA, y), g(TE, y)
        tl = g(TL, y)
        if tl is None and ta is not None and te is not None:
            tl = ta - te                                   # Liabilities raporlanmadiysa turet
        # Raporlanmayan bilesen (orn. AAPL'de ertelenmis vergi yukumlulugu, GOOGL'de
        # stok) 0 sayilir: o tutar zaten "diger" kalemin icindedir. Eskiden tek
        # bir eksik bilesen tamamlayiciyi bos birakiyor ve tahmin bilancosu tutmuyordu.
        parts_a = [g(k, y) or 0 for k in ("Cash & Equivalents", "Accounts Receivable",
                                          "Inventory", "PP&E (net)")]
        parts_l = [g(k, y) or 0 for k in ("Accounts Payable", "Total Debt",
                                          "Deferred Tax Liability")]
        parts_e = [g(k, y) or 0 for k in ("Common Equity", "Retained Earnings")]
        if ta is not None:
            oa[y] = ta - sum(parts_a)
        if tl is not None:
            ol[y] = tl - sum(parts_l)
        # Toplam ozkaynak = varliklar - yukumlulukler. Ana ortaklik ozkaynagi (TE)
        # azinlik paylarini icermez (orn. PEP); fark da "diger ozkaynak"a gider.
        if ta is not None and tl is not None:
            oe[y] = (ta - tl) - sum(parts_e)
        elif te is not None:
            oe[y] = te - sum(parts_e)
    # ---- Faaliyet gideri mutabakati ------------------------------------------
    # Sablonun gider kalemleri (COGS, SG&A, Diger) sirketlerin raporladigi
    # kategorileri kapsamiyor (orn. AMZN: fulfillment, teknoloji, pazarlama) ve
    # cogu sirket amortismani COGS/SG&A icinde raporluyor. Standart normalizasyon:
    #     EBITDA = raporlanan faaliyet kari + amortisman
    # "Diger faaliyet gideri" bu EBITDA'ya ulastiran denklestirici kalem olur;
    # boylece tarihsel EBIT = raporlanan faaliyet kari. Amortisman gomuluyse
    # kalem NEGATIF cikar (geri ekleme) - bu dogrudur, cift sayimi onler.
    # Faaliyet kari raporlamayan sirketler (orn. JNJ) icin yaklasik:
    #     faaliyet kari ~ vergi oncesi kar + faiz gideri
    recon, how = {}, set()
    for y in sorted(set(series.get("Operating Income (reported)", {})) |
                    set(series.get("Pre-tax Income (reported)", {}))):
        oi = g("Operating Income (reported)", y)
        if oi is None and g("Pre-tax Income (reported)", y) is not None:
            oi = g("Pre-tax Income (reported)", y) + abs(g("Interest Expense", y) or 0)
            how.add("vergi oncesi kar + faiz gideri")
        elif oi is not None:
            how.add("OperatingIncomeLoss")
        rev, da = g("Revenue", y), g("Depreciation & Amortisation", y)
        if None in (oi, rev, da):
            continue
        recon[y] = rev - (g("Cost of Goods Sold", y) or 0) - (g("SG&A", y) or 0) - (oi + da)
    if recon:
        tagged = series.get("Other Operating Expense", {})
        series["Other Operating Expense"] = {**tagged, **recon}
        used["Other Operating Expense"] = ("Revenue - COGS - SG&A - (faaliyet kari + D&A) "
                                           "[mutabakat kalemi; faaliyet kari: %s]" % ", ".join(sorted(how)))

    series["Other Assets (plug)"] = oa
    series["Other Liabilities (plug)"] = ol
    series["Other Equity (plug)"] = oe
    used["Other Assets (plug)"] = "Assets - (Cash+AR+Inv+PPE)"
    used["Other Liabilities (plug)"] = "Liabilities - (AP+Debt+DTL)"
    used["Other Equity (plug)"] = "(Assets - Liabilities) - (Common+Retained)  [azinlik paylari dahil]"

    # Yillar: 10-K'si olan ve geliri bulunan mali yillar. Eskiden tum serilerin
    # birlesimi aliniyordu; tek bir kalemde gorulen kapanmamis yil (orn. AMZN
    # "2026") modele giriyordu.
    years = sorted(y for y in fye if y in series.get("Revenue", {}))[-n_years:]
    if not years:
        raise DataError("10-K gelir verisi bulunamadi.")
    return series, used, years


def to_thousands(item, val):
    if val is None:
        return None
    return val if item == "Shares Outstanding" else val / 1000.0


# ---------------------------------------------------------------- Excel yazimi
def write_model(path_in, path_out, company, cik, series, used, years):
    import openpyxl
    wb = openpyxl.load_workbook(path_in)
    fd = wb["08_Data_Feed"]
    fd["B3"] = company
    fd["E3"] = cik
    fd["H3"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fd["B4"] = " / ".join(str(y) for y in years)

    row_of = {fd.cell(row=r, column=1).value: r for r in range(7, 40)
              if fd.cell(row=r, column=1).value}
    for item in list(ORDER) + ["Other Assets (plug)", "Other Liabilities (plug)",
                               "Other Equity (plug)"]:
        r = row_of.get(item)
        if not r:
            continue
        for i, y in enumerate(years):
            fd.cell(row=r, column=3 + i).value = to_thousands(item, series[item].get(y))
        fd.cell(row=r, column=6).value = used.get(item, "")

    # --- 01_Inputs tarihsel blogunu besle (isaret kurallariyla)
    inp = wb["01_Inputs_Historicals"]
    def put(row, item, sign=1, keep_sign=False):
        for i, y in enumerate(years):
            v = to_thousands(item, series[item].get(y))
            if v is not None and sign < 0:
                v = sign * v if keep_sign else sign * abs(v)
            inp.cell(row=row, column=2 + i).value = v
    put(18, "Revenue")
    put(19, "SG&A", -1)
    put(20, "Other Operating Expense", -1, keep_sign=True)   # mutabakat kalemi negatif olabilir
    put(21, "Depreciation & Amortisation", -1)
    put(22, "Interest Expense", -1)
    put(23, "Current Income Tax", -1)
    put(24, "Cash & Equivalents")
    put(25, "Accounts Receivable")
    put(26, "Inventory")
    put(27, "PP&E (net)")
    put(28, "Accounts Payable")
    put(29, "Total Debt")
    put(30, "Deferred Tax Liability")
    put(31, "Common Equity")
    put(32, "Retained Earnings")
    put(33, "Tax Loss Carryforward")

    # hisse sayisi (bin adet) ve Growth-Margin surucileri
    sh = series["Shares Outstanding"].get(years[-1])
    if sh:
        inp["C67"] = sh / 1000.0
    # ---- Tahmin surucilerini gecmisten turet ---------------------------------
    # Yalnizca buyume ve COGS marjini degil, FAALIYET GIDERI oranlarini da
    # gecmisten al. Aksi halde tahmin yillari sablonun demo varsayimlariyla
    # (SG&A %11,5) calisir ve marj gercege uymaz.
    rev = [series["Revenue"].get(y) for y in years]
    cogs = [series["Cost of Goods Sold"].get(y) for y in years]
    sga = [series["SG&A"].get(y) for y in years]
    oox = [series["Other Operating Expense"].get(y) for y in years]

    def fill(row, val, nd=4):
        for c in range(3, 8):                      # C..G = 2026E..2030E
            inp.cell(row=row, column=c).value = round(val, nd)

    if all(rev) and len(rev) >= 2:
        fill(100, rev[-1] / rev[-2] - 1)           # gelir buyumesi
    if rev[-1] and cogs[-1]:
        fill(101, cogs[-1] / rev[-1])              # COGS marji
    if rev[-1] and sga[-1]:
        fill(43, abs(sga[-1]) / rev[-1])           # SG&A % gelir
    ox = next((v for v in reversed(oox) if v), None)
    if rev[-1] and ox:
        fill(44, ox / rev[-1])                     # diger faaliyet gideri % gelir (isaretli)
    elif rev[-1]:
        fill(44, 0.0)                              # veri yoksa sifirla (demo degeri kalmasin)

    # Growth-Margin modunda tarihsel COGS de gercek veriden gelmeli.
    # Aksi halde 2023-25 sutunlari hala birim ekonomisi maliyet bloglarindan
    # beslenir ve EBITDA marji sacmalar.
    op = wb["02_Operating_Model"]
    for i, y in enumerate(years):
        cogs = to_thousands("Cost of Goods Sold", series["Cost of Goods Sold"].get(y))
        if cogs is not None:
            col = 2 + i                                    # B, C, D
            op.cell(row=78, column=col).value = cogs       # Total COGS (tarihsel)

    wb["00_Dashboard"]["B5"] = company
    wb["00_Dashboard"]["B12"] = "Growth-Margin"    # dis veriyle calisirken dogru mod
    wb.save(path_out)
    return path_out


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ticker", help="Ticker (AAPL) veya CIK (0000320193)")
    ap.add_argument("--model", default="Lunoviq_Master_Financial_Model_v2.xlsx")
    ap.add_argument("--out", default=None)
    ap.add_argument("--years", type=int, default=3)
    ap.add_argument("--facts", default=None, help="Cevrimdisi test icin companyfacts JSON dosyasi")
    ap.add_argument("--save-json", default=None, help="Cekilen ham companyfacts JSON'u bu dosyaya yaz")
    a = ap.parse_args()

    if a.facts:
        facts = json.load(open(a.facts))
        cik = str(facts.get("cik", "")).zfill(10)
    else:
        cik = resolve_cik(a.ticker)
        facts = fetch_facts(cik)
        if a.save_json:
            json.dump(facts, open(a.save_json, "w"))
            print("Ham JSON kaydedildi: %s" % a.save_json)
    company = facts.get("entityName", a.ticker)

    try:
        series, used, years = build_feed(facts, a.years)
    except DataError as e:
        raise SystemExit("%s: %s" % (a.ticker, e))
    missing = [k for k, v in used.items() if v == "BULUNAMADI"]

    out = a.out or "%s_Model.xlsx" % re.sub(r"\W+", "_", company)[:40]
    write_model(a.model, out, company, cik, series, used, years)

    print("Sirket : %s (CIK %s)" % (company, cik))
    print("Yillar : %s" % ", ".join(str(y) for y in years))
    print("Yazildi: %s" % out)
    for item in ORDER:
        vals = [series[item].get(y) for y in years]
        flag = "  <-- BULUNAMADI" if used.get(item) == "BULUNAMADI" else ""
        print("  %-30s %s%s" % (item, ["%.0f" % (v/1000) if v else "-" for v in vals], flag))
    if missing:
        print("\nBULUNAMADI (%d) - elle doldur: %s" % (len(missing), ", ".join(missing)))

    print("""
SIRADAKI ADIMLAR (model bunlar yapilmadan tamam degildir)
  1. 00_Dashboard B10 -> CARI HISSE FIYATI. Finansal tablolarda yoktur,
     elle girilir. Simdi sablonun demo degeri (18.50) duruyor.
  2. 06_Comparable_Valuation -> emsal sirketler hala kurgusal (Peer Alpha...).
     Gercek emsallerle degistir; degistirmezsen 'Football Field' kontrolu
     kirmizi yanar (dogru davranis).
  3. 01_Inputs satir 100-101 -> gelir buyumesi ve COGS marji son yildan
     kopyalandi. Bunlar SENIN TAHMININ olmali, gecmisin tekrari degil.
  4. 08_Data_Feed sutun F -> hangi XBRL etiketinin kullanildigini kontrol et.
  5. 00_Dashboard I57 -> model sagligi. Kirmizi ise sebebi bulunana kadar
     sonuclara guvenme.""")

if __name__ == "__main__":
    main()
