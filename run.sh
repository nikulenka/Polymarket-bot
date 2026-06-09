#!/bin/bash
# Запуск Live Tracker (модуль 2) в фоне.
source "/Users/vitalyn/00 Antigravity/.venv/bin/activate"
cd "$(dirname "$0")" || exit
export PYTHONPATH=.
python3 -u -m src.tracker >> logs/monitor.log 2>&1
