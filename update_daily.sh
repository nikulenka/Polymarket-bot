#!/bin/bash
# Ежедневное обновление: пересканировать китов (Whale Scouter) и перезапустить трекер.
source "/Users/vitalyn/00 Antigravity/.venv/bin/activate"
cd "$(dirname "$0")" || exit
export PYTHONPATH=.

echo "[$(date)] Обновление Polymarket-bot…"

# 1. Whale Scouter: топ-холдеры → WinRate/PnL/инсайдер → SQLite + экспорт CSV
python3 -u -m src.scout

# 2. Перезапуск трекера, чтобы подхватить обновлённую БД китов
PID_FILE="data/monitor.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill "$OLD_PID" 2>/dev/null && echo "Старый трекер (PID $OLD_PID) остановлен"
fi
nohup python3 -u -m src.tracker >> logs/monitor.log 2>&1 &
echo $! > "$PID_FILE"
echo "Трекер перезапущен, PID: $(cat $PID_FILE)"

echo "[$(date)] Готово."
