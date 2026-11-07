#!/usr/bin/env bash
cd "$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
  exec python3 app.py "$@"
fi
echo "找不到 Python 3，請先安裝（https://www.python.org/downloads/）。"
read -r -p "按 Enter 結束…" _
