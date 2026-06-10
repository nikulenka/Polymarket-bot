# Polymarket Whale Tracker

Бот отслеживает «умные деньги» на Polymarket: находит кошельки с высоким WinRate/PnL и «инсайдерские» молодые кошельки с крупными ставками, ловит их сделки в реальном времени, оценивает консенсус и шлёт сигнал в Telegram. Опционально повторяет сделку (по умолчанию — **paper-режим**).

Стратегия описана в [docs/Requirements Polymarket.md](docs/Requirements%20Polymarket.md).

---

## Архитектура

Четыре независимых модуля + SQLite:

```
[ Polymarket Data API ]   [ Gamma API ]   [ CLOB API ]
          │                     │                │
          ▼                     ▼                │
 ┌─────────────────┐   ┌──────────────────┐      │
 │  1. scout.py    │   │ Данные рынков    │      │
 │  Whale Scouter  │◄──┤ Top Holders      │      │
 │  WinRate/PnL    │   │ /positions PnL   │      │
 └────────┬────────┘   └──────────────────┘      │
          │ SQLite (whales)                       │
          ▼                                       │
 ┌─────────────────┐                             │
 │  2. tracker.py  │ ◄── /trades (polling)       │
 │  Live Tracker   │                             │
 └────────┬────────┘                             │
          │                                       │
          ▼                                       ▼
 ┌─────────────────┐                  ┌──────────────────┐
 │  3. engine.py   │                  │  4. notifier.py  │
 │  Filter Engine  │─── сигнал ──────►│  Alert Telegram  │
 │  консенсус/MEV  │                  └──────────────────┘
 └─────────────────┘
          │
          ▼
     trader.py (paper / live)
```

| Файл | Назначение |
|---|---|
| [src/scout.py](src/scout.py) | Whale Scouter: топ-холдеры → WinRate/PnL/возраст → SQLite |
| [src/tracker.py](src/tracker.py) | Live Tracker: главный цикл, точка входа |
| [src/engine.py](src/engine.py) | Filter Engine: консенсус, анти-MEV, объём, дельта-нейтрал |
| [src/notifier.py](src/notifier.py) | Alert Telegram: форматирование и отправка сигналов |
| [src/trader.py](src/trader.py) | Исполнение сделок (paper / live) |
| [src/api.py](src/api.py) | Единый клиент Data/Gamma/CLOB API с retry и кэшем |
| [src/db.py](src/db.py) | SQLite: таблицы `whales` и `tx_history` |
| [src/config.py](src/config.py) | Вся конфигурация (пороги, таймауты, режимы) |
| [src/cache.py](src/cache.py) | TelegramBatcher (батчинг сообщений) |
| [src/logger.py](src/logger.py) | JSON + plain логирование с ротацией |
| [src/get_chat_id.py](src/get_chat_id.py) | Утилита: узнать TELEGRAM_CHAT_ID |

---

## Установка

```bash
# Общий .venv для всех проектов Antigravity (используется всеми ботами)
source "/Users/vitalyn/00 Antigravity/.venv/bin/activate"

# Зависимости уже установлены. Если нужно переустановить:
pip install -r requirements.txt

# Конфиг
cp .env.example .env   # заполнить TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
```

---

## Запуск

**Шаг 1 — собрать список китов** (Whale Scouter, запускать по cron раз в день):

```bash
PYTHONPATH=. python3 -m src.scout
```

Скаут сканирует Top Holders 40 крупнейших рынков (ликвидность > $500k), считает WinRate/PnL по каждому кошельку через `/positions` и записывает прошедших фильтр в SQLite (`data/polymarket.db`) и CSV (`data/top_wallets.csv`).

**Шаг 2 — запустить мониторинг** (Live Tracker):

```bash
./run.sh                       # фон, логи → logs/monitor.log
# или напрямую:
PYTHONPATH=. python3 -m src.tracker
```

**Ежедневное обновление** (рескан китов + рестарт трекера):

```bash
./update_daily.sh              # повесить на cron
```

---

## Критерии отбора китов

| Тип | Условие |
|---|---|
| 💎 Бриллиант | WinRate ≥ 80% **и** Total PnL ≥ $100k **и** ≥ 5 разрешённых позиций |
| 🥷 Инсайдер | Возраст кошелька < 14 дней **и** объём сделок > $10k |

Настраивается в [src/config.py](src/config.py) (`ScoutConfig`) или через `.env`:

```
SCOUT_MIN_WINRATE=0.80
SCOUT_MIN_PNL=100000
```

### Методология WinRate/PnL

Считается по `/positions` (поля `realizedPnl + cashPnl`). Позиция считается «решённой», если `redeemable=true` или цена ушла к 0/1 (`curPrice ≤ 0.03` или `≥ 0.97`). Это текущий срез по активным позициям кошелька — лучший доступный сигнал без парсинга всей истории разрешённых рынков.

---

## Режимы торговли

Задаётся `PAPER_MODE` в `.env`:

| Режим | Поведение |
|---|---|
| `PAPER_MODE=true` (default) | Сделки симулируются, реальных денег нет. Виртуальный банкролл и филлы — в `data/paper_fills.json` |
| `PAPER_MODE=false` | Реальные ордера через Polymarket CLOB V2. Нужны ключи `POLY_*` в `.env` |

---

## Тесты

```bash
PYTHONPATH=. python3 -m unittest tests.test_bot -v
```

16 mock-тестов: API notional, скоринг WinRate/PnL, консенсус, MEV-мьют, дельта-нейтрал, фильтр рынков, SQLite roundtrip, формат алерта.

---

## Структура проекта

```
src/            основные модули
tests/          mock-тесты
data/           рантайм-данные (gitignored: *.db *.csv *.json)
logs/           логи (gitignored)
docs/           требования и документация
run.sh          запуск трекера в фоне
update_daily.sh ежедневный рескан китов + рестарт
.env.example    шаблон переменных окружения
```
## Шпаргалка по управлению:
- ssh root@92.62.132.69 | #вход теперь по ключу, без пароля
- journalctl -u polymarket-tracker -f | #живые логи
- sudo systemctl restart polymarket-tracker | #перезапуск
- cd /root/polymarket-bot && git pull && sudo systemctl restart polymarket-tracker | #обновление
