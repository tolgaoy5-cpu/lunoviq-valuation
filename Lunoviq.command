#!/bin/bash
# Lunoviq: double-click to open the valuation app in your browser.
# Keeps running in this Terminal window; close the window (or Ctrl+C) to stop it.
# Your other open Excel files are never touched.

cd "$(dirname "$0")" || exit 1
PORT=8765
URL="http://127.0.0.1:$PORT"

PY="$(command -v python3)"
[ -x /opt/anaconda3/bin/python3 ] && PY=/opt/anaconda3/bin/python3

if curl -s -o /dev/null -m 2 "$URL/api/history"; then
  echo "Lunoviq zaten çalışıyor — tarayıcı açılıyor: $URL"
  open "$URL"
  exit 0
fi

echo "Lunoviq başlatılıyor… (kapatmak için bu pencereyi kapatın)"
exec "$PY" -W ignore -m lunoviq serve --port "$PORT"
