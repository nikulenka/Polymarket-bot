# CLAUDE.md — Руководство по проекту Polymarket Whale Tracker

## Что это за проект

Бот копирует сделки «умных денег» на Polymarket. Стратегия: найти кошельки с WinRate ≥ 80% и PnL ≥ $100k (или молодые инсайдерские), дождаться их консенсусной ставки, отправить алерт в Telegram и опционально повторить сделку.

**Требования целиком** — в [docs/Requirements Polymarket.md](docs/Requirements%20Polymarket.md). Это источник правды по стратегии.

---

## Архитектура: 4 модуля + общий слой

```
src/scout.py    → модуль 1: Whale Scouter (отбор китов → SQLite)
src/tracker.py  → модуль 2: Live Tracker (главный цикл, точка входа)
src/engine.py   → модуль 3: Filter Engine (консенсус, анти-MEV, объём)
src/notifier.py → модуль 4: Alert Telegram
src/trader.py   → исполнение сделок (paper / live)
src/api.py      → единый клиент Polymarket API
src/db.py       → SQLite (whales, tx_history)
src/config.py   → вся конфигурация
src/cache.py    → TelegramBatcher
src/logger.py   → логирование
```

---

## Критичные правила

### 1. Никогда не трогай `PAPER_MODE` без явного запроса

По умолчанию `PAPER_MODE=true` — сделки симулируются. Переключение в `false` отправляет реальные ордера на живой кошелёк. Не включай LIVE-режим сам по себе, только если пользователь явно попросил.

### 2. API: используй только `src/api.py`

Все обращения к Polymarket идут через функции в `src/api.py`. Не пиши inline `httpx.get(...)` в других модулях — добавляй функцию туда.

**Схема полей API (проверено на живом, июнь 2026):**
- `/trades` → поле `size` это **количество шеров**, не USDC. Notional = `size × price`. Используй `api.usdc_notional(trade)`.
- `/positions` → `realizedPnl + cashPnl` = PnL позиции. Используй для WinRate/PnL скоринга, не парси историю разрешённых рынков через Gamma.
- `/holders?market=<conditionId>` → топ-холдеры. Сортировка: `order=volumeNum&ascending=false` (не `volume24hr`!).
- `/value?user=` → текущая стоимость портфеля.

### 3. Конфигурация только через `src/config.py`

Пороги, таймауты, пути — всё в `BotConfig`. Не хардкоди числа в логике модулей. Числа, которые может захотеть поменять пользователь, выставляй в `.env.example`.

### 4. SQLite — единый источник правды по китам

Список отслеживаемых кошельков читается из `data/polymarket.db` (таблица `whales`), не из CSV. CSV (`data/top_wallets.csv`) — экспорт для обзора, пишется через `db.export_whales_csv()`.

### 5. Сигнал = Engine, не LLM

Фильтрация сигналов — только через `FilterEngine` в `src/engine.py`: консенсус × 2, порог notional, мьют MEV. LLM-валидации нет и добавлять не нужно (она не давала полезного сигнала).

---

## Запуск

```bash
# Окружение (общий .venv для всех проектов Antigravity)
source "/Users/vitalyn/00 Antigravity/.venv/bin/activate"
export PYTHONPATH=.

# Собрать китов (первый раз и по cron)
python3 -m src.scout

# Запустить мониторинг
./run.sh
# или: python3 -m src.tracker

# Тесты (без API, на моках)
python3 -m unittest tests.test_bot -v
```

---

## Как вносить изменения

### Поменять пороги отбора китов
→ `src/config.py`, класс `ScoutConfig` (или через `.env`: `SCOUT_MIN_WINRATE`, `SCOUT_MIN_PNL`).

### Поменять логику консенсуса / анти-MEV
→ `src/engine.py`, класс `FilterEngine`. Параметры — `EngineConfig` в `src/config.py`.

### Добавить поле в алерт
→ `src/notifier.py`, метод `format_signal`. Данные о ките берутся из `db.get_whale()`.

### Добавить поле в таблицу `whales`
1. Добавить колонку в `SCHEMA` в `src/db.py`
2. Добавить поле в `upsert_whale` и `export_whales_csv`
3. Заполнить в `src/scout.py` → `score_wallet`

### Добавить новый источник данных
→ Добавить функцию в `src/api.py`, вызвать из нужного модуля.

---

## Частые ловушки

| Ловушка | Правило |
|---|---|
| `size` в `/trades` — это шеры, не USDC | Всегда `api.usdc_notional(t)`, не `t["size"]` |
| Сортировка рынков Gamma | `order=volumeNum`, не `volume24hr` (volume24hr даёт пустые рынки) |
| `PYTHONPATH` | Всегда `export PYTHONPATH=.` или `python3 -m src.tracker`. Без этого `from src.` не работает |
| Батчинг Telegram | Сообщения идут через `notifier.send()`, отправляются батчем. Не создавай параллельный batcher |
| Возраст кошелька | `get_first_trade_ts()` пагинирует до `max_pages=4` (2000 сделок) — если кошелёк старый, флаг «инсайдер» корректно не ставится (он точно не молодой) |

---

## Тесты

`tests/test_bot.py` — 16 mock-тестов без сетевых вызовов. Проверяют:
- `TestNotional` — правильность расчёта notional
- `TestScoutScoring` — WinRate/PnL/инсайдер скоринг
- `TestEngine` — консенсус, MEV-мьют, объём, дельта-нейтрал
- `TestMarketFilter` — regex фильтр спорта/шума
- `TestDB` — SQLite roundtrip, дедупликация tx
- `TestNotifier` — все обязательные поля в алерте

При любом изменении логики скоринга или Engine — добавь тест.

---

## Python-окружение

Проект использует **общий `.venv`** для всех Antigravity-проектов:

```
/Users/vitalyn/00 Antigravity/.venv/
```

**Никогда не создавай локальный `venv/` в папке проекта.** Если нужен новый пакет — устанавливай в общий:

```bash
"/Users/vitalyn/00 Antigravity/.venv/bin/pip" install <package>
```

Затем добавь его в `requirements.txt`.

---

## Что не трогать без нужды

- `src/cache.py` — TelegramBatcher стабилен, менять только если нужен другой transport
- `src/logger.py` — JSON + plain, ротация 10MB/5 файлов, трогать не надо
- `data/polymarket.db` — живая БД с китами, gitignored. Не удалять руками в проде

---

## Структура данных в БД

```sql
-- Отслеживаемые кошельки
whales (
    address TEXT PRIMARY KEY,
    pseudonym TEXT,
    winrate REAL,          -- 0.0 – 1.0
    total_pnl REAL,        -- USD
    portfolio_value REAL,
    resolved_trades INTEGER,
    is_insider INTEGER,    -- 0 / 1
    age_days REAL,
    score REAL,
    first_seen TEXT,       -- ISO timestamp
    created_at TEXT,
    last_active TEXT,
    last_scored TEXT
)

-- История зафиксированных сделок (антидубликат + аудит)
tx_history (
    tx_hash TEXT, outcome TEXT,  -- PK: (tx_hash, outcome)
    address TEXT, condition_id TEXT,
    market_title TEXT, event_slug TEXT,
    side TEXT,             -- BUY / SELL
    amount_usd REAL,       -- notional = size × price
    price REAL,
    timestamp INTEGER
)
```
