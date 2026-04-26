#!/bin/bash
cd "/Users/vitalyn/00 Antigravity/Polymarket-bot"
./venv/bin/python3 -u src/monitor.py >> logs/monitor.log 2>&1
