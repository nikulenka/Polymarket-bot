#!/bin/bash

# Переходим в директорию бота
cd "/Users/vitalyn/00 Antigravity/Polymarket-bot" || exit

echo "[$(date)] Начинаем ежедневное обновление базы Polymarket-bot..."

# 1. Скачиваем свежие 50 000 сделок
./venv/bin/python3 src/fetch_trades.py

# 2. Пересчитываем рейтинг (сохраняется в data/top_wallets.csv)
./venv/bin/python3 src/rank_wallets.py

# 3. Анализируем PnL (просто для истории в логах)
./venv/bin/python3 src/analyze_pnl.py

echo "[$(date)] Обновление завершено!"
