#!/bin/bash
# Ежедневное обновление: пересканировать китов (Whale Scouter) и перезапустить трекер.
source "/Users/vitalyn/MyDocuments/00 My Projects/.venv/bin/activate"
cd "$(dirname "$0")" || exit
export PYTHONPATH=.

echo "[$(date)] Обновление Polymarket-bot…"

# 1. Whale Scouter: лента сделок → WinRate/PnL/инсайдер → SQLite + экспорт CSV
python3 -u -m src.scout

# 2. Перезапуск трекера через единый путь запуска (pid-lock в run.sh)
./run.sh

echo "[$(date)] Готово."
