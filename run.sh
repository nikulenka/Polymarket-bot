#!/bin/bash
cd "$(dirname "$0")" || exit
export PYTHONPATH=.
./venv/bin/python3 -u src/monitor.py >> logs/monitor.log 2>&1
