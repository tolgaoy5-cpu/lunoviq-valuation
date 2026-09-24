#!/bin/bash
# Lunoviq: double-click to build a valuation model for any US-listed company.
# Asks for a ticker, fetches the data, fills and recalculates the Excel model,
# then opens it. Your other open Excel files are not touched.

cd "$(dirname "$0")" || exit 1

PY="$(command -v python3)"
[ -x /opt/anaconda3/bin/python3 ] && PY=/opt/anaconda3/bin/python3

TICKER=$(osascript -e 'text returned of (display dialog "Hisse kodu (ör. KO, MSFT, JNJ):" default answer "" with title "Lunoviq" buttons {"İptal", "Modeli oluştur"} default button 2)' 2>/dev/null)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')
[ -z "$TICKER" ] && exit 0

echo "Lunoviq — $TICKER modeli hazırlanıyor (1-2 dakika sürebilir)..."
echo
"$PY" -W ignore -m lunoviq run "$TICKER" --open
STATUS=$?
echo
if [ $STATUS -eq 0 ]; then
  osascript -e "display notification \"$TICKER modeli hazır ve Excel'de açıldı.\" with title \"Lunoviq\""
  echo "Bitti. Bu pencereyi kapatabilirsiniz."
else
  osascript -e "display alert \"Lunoviq\" message \"$TICKER için model oluşturulamadı. Ayrıntılar Terminal penceresinde.\""
  echo "Hata oluştu (yukarıdaki mesaja bakın)."
fi
