# CLAUDE.md — Руководство по проекту Polymarket Whale Tracker

## Бизнес-контекст
См. `/Users/vitalyn/MyDocuments/00 My Projects/00 brain/projects/polymarket-bot/` (business.md, architecture.md, retro/).

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
- `/v1/leaderboard?rankType=pnl` → топ-50 трейдеров по PnL (`proxyWallet`, `userName`, `pnl`). Главный источник «бриллиантовых» китов: PnL там стабильный, в отличие от шумного снапшота `/positions`. Максимум 50 записей, `window` почти не влияет.

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
source "/Users/vitalyn/MyDocuments/00 My Projects/.venv/bin/activate"
export PYTHONPATH=.

# Собрать китов (первый раз и по cron)
python3 -m src.scout

# Запустить мониторинг
./run.sh
# или: python3 -m src.tracker

# Деплой на Linux-сервер (systemd): см. deploy/DEPLOY.md
#   sudo bash deploy/install.sh

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
| Возраст кошелька | `get_first_trade_ts()` пагинирует до `max_pages=4` (2000 сделок). Если история глубже — возвращает `None` («возраст неизвестен»), а НЕ min из последних сделок: иначе гиперактивный бот, у которого 2000 сделок за сутки, ложно помечается «молодым инсайдером» |
| TP/SL на бинарных рынках | Только в пунктах вероятности (`take_profit_delta`/`stop_loss_delta`), не в % от цены: при входе по 0.9 «+25%» недостижимы. С патча A (2026-06-19) TP/флип зависят от цены входа, с патча F (2026-06-24) — и SL тоже (для дешёвых входов `< 0.35` стоп выключен, `sl_delta=None`). См. `exit_params()`, возвращает кортеж из 4 значений |
| Цена входа | Входим по текущему ask (`api.get_price(token_id, side="buy")`), не по цене сделки кита — ей может быть до 12 часов. При SELL-сигнале покупается противоположный токен, его справедливая цена ≈ 1 − цена кита |
| Разрешённый рынок | CLOB `/price` отдаёт 404 → исход берём из `api.get_market_resolution()` и закрываем по 1.0/0.0. Закрытие «по входу» конфискует выигрыши |

---

## Тесты

`tests/test_bot.py` — 53 mock-теста без сетевых вызовов. Проверяют:
- `TestNotional` — правильность расчёта notional
- `TestScoutScoring` — WinRate/PnL/инсайдер скоринг
- `TestEngine` — консенсус, MEV-мьют, объём, дельта-нейтрал
- `TestCapitalManagement` — Kelly-сайзинг, тиерный кап, лимиты входа, дневной стоп
- `TestExitProfile` — профиль выхода (`exit_params`) по цене входа: TP/флип (патч A) и SL (патч F, дешёвый вход без стопа, дорогой со стопом)
- `TestSignalGates` — гейты одиночного `trusted_whale` (фавориты/SELL/нет края)
- `TestMarketFilter` — regex фильтр спорта/шума
- `TestDB` — SQLite roundtrip, дедупликация tx
- `TestNotifier` — все обязательные поля в алерте

При любом изменении логики скоринга или Engine — добавь тест.

---

## Python-окружение

Проект использует **общий `.venv`** для всех Antigravity-проектов:

```
/Users/vitalyn/MyDocuments/00 My Projects/.venv/
```

**Никогда не создавай локальный `venv/` в папке проекта.** Если нужен новый пакет — устанавливай в общий:

```bash
"/Users/vitalyn/MyDocuments/00 My Projects/.venv/bin/pip" install <package>
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
    lifetime_pnl REAL,     -- подтверждённый PnL с leaderboard (0 = кит не оттуда)
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

-- Исходы сигналов: был ли кит прав (петля обратной связи)
signal_outcomes (
    id INTEGER PK,
    cond_id TEXT, market_title TEXT,
    side TEXT, outcome TEXT,   -- направление сигнала и consensus_outcome
    entry_price REAL, signal_type TEXT,
    wallets TEXT,              -- адреса на стороне сигнала, через запятую
    created_at TEXT,
    resolved_at TEXT, winner TEXT, won INTEGER  -- NULL пока рынок не разрешён
)
```

**Петля обратной связи:** каждый сигнал пишется в `signal_outcomes` (даже если позиция не открыта — оцениваем кита). `tracker.check_signal_outcomes()` каждые 30 минут сверяет с разрешением рынков; киты с ≥ 5 разрешёнными сигналами и долей правоты < 40% удаляются автоматически (`db.prune_bad_performers`).

**Элита:** одиночный сигнал (без консенсуса) даёт только элитный кит — WinRate ≥ 70% и PnL ≥ $10k, инсайдер или leaderboard-кит (`db.get_elite_addresses`). Остальные отслеживаемые — только консенсусом 2+.

**Управление капиталом (`src/tracker.py`):** размер позиции — fractional Kelly `position_size_usd()` (0.25 Келли); кап банкролла зависит от цены входа — 2% обычно, **4% для дешёвых входов** (`< cheap_entry_max=0.35`, см. патч B ниже). Лимиты `entry_blocked()` — максимум 10 позиций, одна на рынок. Дневной стоп `daily_stop_active()` — просадка ≥ 5% за день UTC → пауза входов до завтра (состояние в `data/metrics.json`).

**Профиль выхода зависит от цены входа** (`exit_params()` в `src/tracker.py`, патч A от 2026-06-19, патч F от 2026-06-24) — TP/флип/SL не фиксированы, а считаются по входу. `exit_params()` возвращает кортеж `(partial_take_delta, partial_take_fraction, take_profit_delta, stop_loss_delta)`, где `stop_loss_delta == None` → стоп выключен:
- дешёвый лонгшот (вход `< 0.35`): флип **выключен** (`cheap_partial_take_fraction=0`), широкий TP `+0.45`, **стоп выключен** (`cheap_stop_loss_delta=None`, патч F) — едет до разрешения. Дешёвые входы дают мунбэги (×3–×5), флип на +5ц их обрубает, а плоский стоп −15ц выбивает их рыночным шумом до разрешения (вход 0.18 → −15ц это −83% капитала позиции).
- дорогой фаворит (вход `≥ 0.50`): быстрый флип на `+0.03`, узкий TP `+0.06`, стоп `−0.15` (`expensive_stop_loss_delta`) — апсайд там мал, забираем быстро, риск режем.
- середина (`0.35–0.50`): базовый профиль — флип `+0.05` (50% позиции), TP `+0.10`, SL `−0.15`.

**Патч F (2026-06-24, почему):** анализ paper-торговли 10–24 июня — слив −$89 при том, что 70% сигналов в итоге правы. Деньги терялись на ВЫХОДЕ: 22 стоп-лосса = −$210, почти все на дешёвых входах, выбитых шумом за 15мин–6ч ДО разрешения. Раньше стоп был плоским `−0.15` для всех; теперь зависит от цены входа, как уже было сделано для TP патчем A.

**Гейты одиночного сигнала** (патчи B/C в `execute_trade()`, `src/tracker.py`) — применяются ТОЛЬКО к `signal_type == "trusted_whale"` (одиночный элитный кит); консенсус (`signal_type == "consensus"`) их не проходит, его край в группе, а не в WinRate одного кита:
- цена входа `≥ single_whale_max_price` (0.50) → скип, фавориты только консенсусом.
- `side == "SELL"` и `single_whale_allow_sell=False` (дефолт) → скип — SELL одиночных китов исторически даёт низкую правоту.
- `skip_when_no_edge=True` (дефолт) и `WinRate кита ≤ цена` → скип вместо ставки минимального размера ($2 в никуда).

Все пороги выше переопределяются через `.env` (см. `.env.example`) — `CHEAP_ENTRY_MAX`, `CHEAP_TAKE_PROFIT_DELTA`, `CHEAP_STOP_LOSS_DELTA` (`none`/`off` = стоп выкл), `EXPENSIVE_STOP_LOSS_DELTA`, `POSITION_MAX_PCT_CHEAP`, `SINGLE_WHALE_MAX_PRICE`, `SINGLE_WHALE_ALLOW_SELL`, `SKIP_WHEN_NO_EDGE`, `KELLY_FRACTION`, `PAPER_START_BALANCE`.

**Диагностика правоты сигналов** — `db.signal_outcome_breakdown()` (патч E) даёт разбивку правоты по стороне/типу/цене входа; видна и в `scripts/calibration_check.py`, и в ежедневном heartbeat (`maybe_daily_report()`).
