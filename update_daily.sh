#!/bin/bash

# Переходим в директорию бота
cd "$(dirname "$0")" || exit

echo "[$(date)] Начинаем ежедневное обновление базы Polymarket-bot..."

# 1. Скачиваем свежие 50 000 сделок
./venv/bin/python3 src/fetch_trades.py

# 2. Пересчитываем рейтинг (сохраняется в data/top_wallets.csv)
./venv/bin/python3 src/rank_wallets.py

# 3. Анализируем PnL (просто для истории в логах)
./venv/bin/python3 src/analyze_pnl.py

# 4. Перезапускаем бота чтобы он подхватил новый top_wallets.csv
PID_FILE="data/monitor.pid"
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    kill "$OLD_PID" 2>/dev/null && echo "Старый бот (PID $OLD_PID) остановлен"
fi
nohup ./venv/bin/python3 -u src/monitor.py >> logs/monitor.log 2>&1 &
echo $! > "$PID_FILE"
echo "Бот перезапущен с новым списком китов, PID: $(cat $PID_FILE)"

echo "[$(date)] Обновление завершено!"
