"""
Централизованная конфигурация бота Polymarket.
Загружается из .env и может переопределяться через env-переменные.

Архитектура (по docs/Requirements Polymarket.md):
  1. Whale Scouter  — скоринг кошельков по WinRate/PnL + инсайдерский фильтр   (ScoutConfig)
  2. Live Tracker   — мониторинг сделок отслеживаемых китов в реальном времени (MonitorConfig)
  3. Filter Engine  — консенсус, анти-шум (MEV/арбитраж), дельта-нейтрал       (EngineConfig)
  4. Alert Telegram — обогащённые уведомления                                  (TelegramConfig)
"""

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "y")


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    try:
        return float(val) if val is not None else default
    except ValueError:
        return default


def _env_float_or_none(name: str, default):
    """Как _env_float, но "none"/"off"/"" → None (стоп выключен)."""
    val = os.getenv(name)
    if val is None:
        return default
    if val.strip().lower() in ("none", "off", ""):
        return None
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default


@dataclass
class APIConfig:
    """Конфигурация API endpoints Polymarket."""
    data_api: str = "https://data-api.polymarket.com"
    gamma_api: str = "https://gamma-api.polymarket.com"
    clob_api: str = "https://clob.polymarket.com"
    site_url: str = "https://polymarket.com"  # для ссылок в алертах


@dataclass
class MonitorConfig:
    """Конфигурация Live Tracker (модуль 2)."""
    poll_interval: int = 30          # сек между проверками
    signal_window: int = 43200       # окно анализа сигналов (12 ч)
    min_wallets: int = 2             # минимум китов для консенсуса
    min_size_usdc: float = 50.0      # порог notional для известного кита
    heartbeat_interval: int = 600    # пульс в лог (10 мин)
    # Персональный опрос китов (/trades?user=) вместо глобальной ленты:
    # в глобальной ленте сделка видна от имени ТЕЙКЕРА, лимитные ордера
    # китов туда не попадают — бот слеп к большинству их сделок.
    whales_per_cycle: int = 25       # сколько китов опрашиваем за цикл (round-robin)
    per_whale_limit: int = 50        # сделок на кита за обычный опрос
    first_cycle_limit: int = 200     # на первом проходе — глубже (backfill окна)
    daily_report_hour_utc: int = 8   # ежедневный heartbeat-отчёт в Telegram (час UTC)


@dataclass
class ScoutConfig:
    """Конфигурация Whale Scouter (модуль 1) — критерии из требований."""
    # Критерии «бриллиантового» кошелька
    # /positions — snapshot текущего портфеля, не lifetime история.
    # Поэтому реалистичные (не aspirational) пороги:
    min_winrate: float = 0.55        # WinRate >= 55% (чуть выше случайного)
    min_total_pnl: float = 1_000.0   # Total PnL >= $1k (хотя бы один крупный правильный бет)
    min_resolved_trades: int = 2     # минимум 2 разрешённых позиции

    # Инсайдерский паттерн: молодой кошелёк с КРУПНЫМ объёмом и положительным результатом.
    # Возраст должен быть ТОЧНЫМ (история исчерпана в get_first_trade_ts), иначе не инсайдер.
    insider_max_age_days: int = 7    # кошелёк < 7 дней
    insider_min_volume: float = 50_000.0   # объём > $50k — только серьёзные игроки
    insider_min_winrate: float = 0.50      # инсайдер должен выигрывать, а не просто торговать
    insider_min_pnl: float = 0.0           # PnL строго > этого значения (убыточных не копируем)

    # Стратегия: кандидаты из ленты активных сделок (вместо топ-холдеров)
    trade_feed_pages: int = 10       # страниц по 500 сделок = 5000 сделок
    trade_min_notional: float = 200.0  # min $200 на сделку — отсекаем мелочь
    max_candidates: int = 300        # максимум уникальных адресов за прогон

    # Анти-бот при скоринге
    max_market_diversity: int = 60   # слишком много разных рынков = бот/биржа

    # Leaderboard как источник китов (lifetime PnL вместо шумного снапшота /positions)
    leaderboard_limit: int = 50      # API отдаёт максимум 50
    lb_min_pnl: float = 25_000.0     # кит с leaderboard: PnL >= $25k
    lb_min_winrate: float = 0.50     # sanity-проверка по снапшоту /positions
    lb_min_resolved_for_check: int = 5  # если решённых позиций меньше — winrate не проверяем (мало данных)

    # Авточистка по исходам скопированных сигналов (signal_outcomes)
    prune_min_signals: int = 5       # минимум разрешённых сигналов для оценки кита
    prune_min_winshare: float = 0.40 # доля правоты ниже → кит удаляется из БД


@dataclass
class EngineConfig:
    """Конфигурация Filter Engine (модуль 3) — анти-шум и риск."""
    consensus_dominance: float = 2.0     # одна сторона должна превышать другую в N раз
    min_alert_notional: float = 200.0   # консенсус: суммарный объём группы > $200
    # Анти-MEV/арбитраж: если кошелёк делает > N сделок за окно — мьютим
    mev_max_trades_per_window: int = 50
    mev_window_sec: int = 60
    mev_mute_sec: int = 3600             # на сколько мьютим бота
    # Дельта-нейтрал: помечаем рынки где одни киты в YES, другие в NO одновременно
    flag_delta_neutral: bool = True
    # Порог для одиночного алерта по известному киту (без консенсуса).
    # По требованиям — $1,000: копировать каждую мелкую сделку кита убыточно.
    trusted_whale_min_notional: float = 1_000.0
    # Одиночный сигнал — только от «элитного» кита (высокий winrate/PnL или инсайдер).
    # Остальные отслеживаемые киты дают сигнал только консенсусом (2+ кошелька).
    elite_min_winrate: float = 0.70
    elite_min_pnl: float = 10_000.0
    # Как часто проверять разрешение рынков по записанным сигналам (сек)
    outcome_check_interval: int = 1800
    # Патч G: размер батча сверки (очередь ротируется: давно не проверенные
    # первыми) и срок, после которого неразрешённый сигнал безнадёжен (gave_up).
    outcome_check_batch: int = 100
    outcome_give_up_days: int = 30


@dataclass
class TradingConfig:
    """Конфигурация торговли. paper_mode=True → ордера симулируются, реальных сделок нет."""
    paper_mode: bool = True           # ВАЖНО: по умолчанию paper (без реальных денег)
    paper_start_balance: float = 1000.0  # стартовый банкролл для симуляции
    trade_amount_usd: float = 2.0
    min_tokens: float = 5.0           # минимум токенов для CLOB
    # TP/SL в ПУНКТАХ ВЕРОЯТНОСТИ (центах), не в % от цены: на бинарном рынке
    # +25% от 0.9 недостижимы (цена ограничена 1.0), а пункты работают на любой цене.
    take_profit_delta: float = 0.10   # +10 центов вероятности
    stop_loss_delta: float = -0.15    # -15 центов вероятности
    position_hold_hours: int = 24
    max_price: float = 0.80           # выше — апсайд мизерный при риске -100%
    max_entry_slippage: float = 0.05  # рынок убежал от цены кита > чем на 5 центов → пропуск
    resolution_grace_hours: int = 24  # ждём разрешения рынка после close_at, прежде чем закрыть по входу

    # --- Управление капиталом (Фаза 3) ---
    # Sizing: fractional Kelly от качества кита. f* = (p − c)/(1 − c), где
    # p — оценка вероятности (winrate лучшего кита сигнала), c — цена входа.
    # Итог: clamp(банкролл × kelly_fraction × f*, trade_amount_usd, банкролл × position_max_pct)
    kelly_fraction: float = 0.25      # доля полного Келли (полный — слишком агрессивен)
    position_max_pct: float = 0.02    # жёсткий кап: максимум 2% банкролла на позицию
    max_open_positions: int = 10      # лимит одновременных позиций
    daily_max_loss_pct: float = 0.05  # просадка за день UTC >= 5% → пауза входов до завтра
    # Флиппинг (из требований): частичная фиксация при движении вероятности
    partial_take_delta: float = 0.05  # +5 центов → фиксируем часть
    partial_take_fraction: float = 0.5  # какую долю позиции фиксируем (0 = выключено)

    # --- Патч A: профиль выхода зависит от цены входа ---
    # Анализ 76 закрытых сделок: 77% валовой прибыли пришло с 19 мунбэгов
    # (доходность >30%), а флип резал их пополам уже на +5 центов. Поэтому:
    # дешёвый лонгшот (вход < cheap_entry_max) — НЕ фиксируем частично,
    # даём ехать до широкого TP/разрешения (там и зашит правый хвост).
    cheap_entry_max: float = 0.35
    cheap_take_profit_delta: float = 0.45      # дешёвый вход: TP далеко (почти до 1.0)
    cheap_partial_take_fraction: float = 0.0   # дешёвый вход: без частичной фиксации
    # Дорогой вход (фаворит, >= expensive_entry_min) — апсайд мал, забираем быстро.
    expensive_entry_min: float = 0.50
    expensive_take_profit_delta: float = 0.06
    expensive_partial_take_delta: float = 0.03

    # --- Патч F: стоп-лосс тоже зависит от цены входа ---
    # Анализ 10–24 июня: 22 стоп-лосса = −$210 при общем сливе −$89, при том что
    # 70% сигналов в итоге правы. Плоский −15c выбивает дешёвые лонгшоты шумом
    # (вход 0.18 → −15c это −83% капитала позиции, но всего один тик рынка)
    # ДО разрешения, где у них и зашит правый хвост. Поэтому:
    #   дешёвый вход (< cheap_entry_max): стоп выключен (None), едем до разрешения;
    #   дорогой фаворит (>= expensive_entry_min): −15c оправдан (апсайд мал, режем риск);
    #   середина: базовый stop_loss_delta.
    cheap_stop_loss_delta: float | None = None     # None = без стопа для дешёвых входов
    expensive_stop_loss_delta: float = -0.15

    # --- Патч G (2026-07-04): повышенный кап дешёвых входов ОТМЕНЁН ---
    # Патч B давал бакету <0.35 кап 4% («асимметричный край»), но окно
    # 19.06–03.07 показало: бакет дал −$236 при 10/32 прибыльных и ни одного
    # выигранного разрешения. Правота китов на лонгшотах петлёй исходов пока
    # не подтверждена → кап как у всех (2%). Вернуть выше — только когда
    # signal_outcome_breakdown() покажет право́ту > цены на этом бакете.
    position_max_pct_cheap: float = 0.02
    # Совсем дешёвые (лотерейные) входы — только консенсусом 2+ китов:
    # одиночный кит с глобальным WinRate 80% ничего не говорит о рынке за 15ц.
    cheap_consensus_below: float = 0.20
    # Дешёвый лонгшот без стопа держим до разрешения (close_at = endDate рынка),
    # но не дольше горизонта: рынок с разрешением дальше N дней — пропуск входа.
    cheap_max_horizon_days: int = 7

    # --- Патчи B/C: ограничения ОДИНОЧНОГО сигнала trusted_whale ---
    # Консенсус (2+ кошелька) всегда проходит — его край в группе, а не в
    # winrate одного кита. Одиночному киту в зоне фаворитов / на SELL / без
    # оценённого края не доверяем (исторически убыточно).
    single_whale_max_price: float = 0.50   # одиночный кит выше этой цены → скип
    single_whale_allow_sell: bool = False  # одиночный кит на SELL → скип (SELL: 25% правоты)
    skip_when_no_edge: bool = True         # WinRate кита <= цена → нет края → скип одиночного


@dataclass
class CacheConfig:
    max_seen_hashes: int = 30_000
    max_buffer_size: int = 50_000
    api_cache_ttl_sec: int = 60
    balance_cache_ttl_sec: int = 30
    price_cache_ttl_sec: int = 30


@dataclass
class FileConfig:
    db_path: str = "data/polymarket.db"      # SQLite — единый источник правды
    top_wallets_path: str = "data/top_wallets.csv"  # экспорт для совместимости/обзора
    signals_file: str = "data/sent_signals.json"
    positions_file: str = "data/open_positions.json"
    paper_fills_file: str = "data/paper_fills.json"
    metrics_file: str = "data/metrics.json"
    log_dir: str = "logs"
    log_file: str = "logs/signals.log"
    monitor_log: str = "logs/monitor.log"
    signals_ttl_hours: int = 48


@dataclass
class TelegramConfig:
    """Конфигурация Alert Telegram (модуль 4)."""
    enabled: bool = True
    token: Optional[str] = None
    chat_id: Optional[str] = None
    batch_interval_sec: int = 300
    max_batch_size: int = 10


@dataclass
class TimeoutConfig:
    default_timeout: int = 15
    fetch_trades_timeout: int = 15
    market_tokens_timeout: int = 10
    price_timeout: int = 5
    telegram_timeout: int = 5
    positions_timeout: int = 15


@dataclass
class MarketFilterConfig:
    """Фильтр шумных рынков (спорт, краткосрочные up/down и т.п.)."""
    skip_patterns: List[str] = None
    compiled_filter: Optional[re.Pattern] = None

    def __post_init__(self):
        if self.skip_patterns is None:
            self.skip_patterns = [
                "NBA", "NFL", "NHL", "MLB", "soccer",
                "beat the", "Series", "Finals", "Championship",
                "Buccaneers", "Lakers", "Spurs", "Hawks", "Knicks",
                "Celtics", "Warriors", "Nuggets", "Playoffs",
                "AM-", "PM-", "AM ET", "PM ET", ":00AM", ":00PM",
                "Up or Down -", "updown", "Up or Down,",
                "Spread:", "Spread -", "moneyline", "Over/Under",
                "1st Half", "2nd Half", "1H ", "2H ", "Live:",
                " win on ", " beat ", " score more ",
                "CONCACAF", "CONMEBOL", "UEFA", "FIFA", "AFCON", "Copa",
                "World Cup", "Olympic", "Tour de France", "Grand Prix", "Formula 1",
                # Крипто price-prediction рынки (не ETF/регуляторные вопросы)
                "price of Bitcoin", "price of Ethereum", "price of Solana",
                "price of XRP", "price of BNB", "price of DOGE",
                " dip to $", "Bitcoin dip", "Ethereum dip",
                "be above $", "be below $", "be less than $", "be more than $",
                "above $1", "above $2", "above $3", "above $4", "above $5",
                "above $6", "above $7", "above $8", "above $9",
                " vs ", " vs. ", " FC ", " United ", " Real ", " City ", " Atletico ",
                "Madrid Open", "Tennis", "ATP", "WTA", "Winner", "Map 1", "Map 2",
                "Counter-Strike", "CS2", "Dota", "Esports", "UFC", "MMA", "Boxing",
                "Total Sets", "O/U 2.5", "O/U 3.5", "O/U 4.5", "Total Goals",
                "Premier League", "Champions League", "La Liga", "Bundesliga",
            ]
        self.compiled_filter = re.compile(
            '|'.join(re.escape(p) for p in self.skip_patterns),
            re.IGNORECASE,
        )

    def should_skip(self, market_name: str) -> bool:
        """O(1) проверка вместо O(N) цикла."""
        if not market_name:
            return False
        return bool(self.compiled_filter.search(market_name))


@dataclass
class BotConfig:
    """Главная конфигурация бота."""
    api: APIConfig = field(default_factory=APIConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    scout: ScoutConfig = field(default_factory=ScoutConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    files: FileConfig = field(default_factory=FileConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)
    market_filter: MarketFilterConfig = field(default_factory=MarketFilterConfig)


def load_config() -> BotConfig:
    """Загружает конфигурацию из .env и env-переменных."""
    config = BotConfig()

    # Секреты
    config.telegram.token = os.getenv("TELEGRAM_TOKEN")
    config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID")

    # Переопределяемые числовые/булевы параметры
    config.monitor.poll_interval = _env_int("POLL_INTERVAL", config.monitor.poll_interval)
    config.trading.paper_mode = _env_bool("PAPER_MODE", config.trading.paper_mode)
    config.trading.trade_amount_usd = _env_float("TRADE_AMOUNT_USD", config.trading.trade_amount_usd)
    config.scout.min_winrate = _env_float("SCOUT_MIN_WINRATE", config.scout.min_winrate)
    config.scout.min_total_pnl = _env_float("SCOUT_MIN_PNL", config.scout.min_total_pnl)
    config.engine.min_alert_notional = _env_float("MIN_ALERT_NOTIONAL", config.engine.min_alert_notional)
    config.engine.trusted_whale_min_notional = _env_float(
        "TRUSTED_WHALE_MIN_NOTIONAL", config.engine.trusted_whale_min_notional)
    config.trading.max_price = _env_float("MAX_PRICE", config.trading.max_price)
    config.trading.max_entry_slippage = _env_float("MAX_ENTRY_SLIPPAGE", config.trading.max_entry_slippage)
    config.trading.max_open_positions = _env_int("MAX_OPEN_POSITIONS", config.trading.max_open_positions)
    config.trading.daily_max_loss_pct = _env_float("DAILY_MAX_LOSS_PCT", config.trading.daily_max_loss_pct)
    config.trading.position_max_pct = _env_float("POSITION_MAX_PCT", config.trading.position_max_pct)

    # Патч D: масштабирование капитала и профиля выхода через .env — без правки
    # кода и БЕЗ переключения PAPER_MODE. Позволяет наращивать банк/агрессию,
    # когда петля обратной связи подтвердит край на разрешённых рынках.
    config.trading.paper_start_balance = _env_float("PAPER_START_BALANCE", config.trading.paper_start_balance)
    config.trading.kelly_fraction = _env_float("KELLY_FRACTION", config.trading.kelly_fraction)
    config.trading.position_max_pct_cheap = _env_float("POSITION_MAX_PCT_CHEAP", config.trading.position_max_pct_cheap)
    config.trading.take_profit_delta = _env_float("TAKE_PROFIT_DELTA", config.trading.take_profit_delta)
    config.trading.stop_loss_delta = _env_float("STOP_LOSS_DELTA", config.trading.stop_loss_delta)
    config.trading.cheap_entry_max = _env_float("CHEAP_ENTRY_MAX", config.trading.cheap_entry_max)
    config.trading.cheap_take_profit_delta = _env_float("CHEAP_TAKE_PROFIT_DELTA", config.trading.cheap_take_profit_delta)
    config.trading.cheap_stop_loss_delta = _env_float_or_none("CHEAP_STOP_LOSS_DELTA", config.trading.cheap_stop_loss_delta)
    config.trading.expensive_stop_loss_delta = _env_float("EXPENSIVE_STOP_LOSS_DELTA", config.trading.expensive_stop_loss_delta)
    config.trading.single_whale_max_price = _env_float("SINGLE_WHALE_MAX_PRICE", config.trading.single_whale_max_price)
    config.trading.single_whale_allow_sell = _env_bool("SINGLE_WHALE_ALLOW_SELL", config.trading.single_whale_allow_sell)
    config.trading.skip_when_no_edge = _env_bool("SKIP_WHEN_NO_EDGE", config.trading.skip_when_no_edge)

    # Патч G: гейты дешёвых входов
    config.trading.cheap_consensus_below = _env_float("CHEAP_CONSENSUS_BELOW", config.trading.cheap_consensus_below)
    config.trading.cheap_max_horizon_days = _env_int("CHEAP_MAX_HORIZON_DAYS", config.trading.cheap_max_horizon_days)

    return config


# Глобальный экземпляр конфигурации
CONFIG = load_config()
