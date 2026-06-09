#!/bin/bash
# Запуск Live Tracker (модуль 2). Единственный путь запуска:
# убивает предыдущий экземпляр по pid-файлу, чтобы не было двух трекеров
# (двойные сигналы/позиции + старый код в памяти).
source "/Users/vitalyn/00 Antigravity/.venv/bin/activate"
cd "$(dirname "$0")" || exit
export PYTHONPATH=.

PID_FILE="data/monitor.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" && echo "Старый трекер (PID $OLD_PID) остановлен"
        sleep 2
    fi
fi
# Подстраховка: добиваем трекеры, запущенные мимо pid-файла
pkill -f "python3 .*-m src\.tracker" 2>/dev/null && sleep 2

mkdir -p logs data
nohup python3 -u -m src.tracker >> logs/monitor.log 2>&1 &
echo $! > "$PID_FILE"
echo "Трекер запущен, PID: $(cat $PID_FILE)"
