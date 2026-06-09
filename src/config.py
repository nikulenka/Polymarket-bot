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


@dataclass
class ScoutConfig:
    """Конфигурация Whale Scouter (модуль 1) — критерии из требований."""
    # Критерии «бриллиантового» кошелька
    # /positions — snapshot текущего портфеля, не lifetime история.
    # Поэтому реалистичные (не aspirational) пороги:
    min_winrate: float = 0.55        # WinRate >= 55% (чуть выше случайного)
    min_total_pnl: float = 1_000.0   # Total PnL >= $1k (хотя бы один крупный правильный бет)
    min_resolved_trades: int = 2     # минимум 2 разрешённых позиции

    # Инсайдерский паттерн: молодой кошелёк с КРУПНЫМ объёмом и хоть какими победами
    insider_max_age_days: int = 7    # кошелёк < 7 дней
    insider_min_volume: float = 50_000.0   # объём > $50k — только серьёзные игроки
    insider_min_winrate: float = 0.25      # хотя бы 25% побед — фильтр чистых лузеров

    # Стратегия: кандидаты из ленты активных сделок (вместо топ-холдеров)
    trade_feed_pages: int = 10       # страниц по 500 сделок = 5000 сделок
    trade_min_notional: float = 200.0  # min $200 на сделку — отсекаем мелочь
    max_candidates: int = 300        # максимум уникальных адресов за прогон

    # Анти-бот при скоринге
    max_market_diversity: int = 60   # слишком много разных рынков = бот/биржа


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
    # Порог для одиночного алерта по известному киту (без консенсуса)
    trusted_whale_min_notional: float = 100.0  # $100+ от проверенного кита → сразу алерт


@dataclass
class TradingConfig:
    """Конфигурация торговли. paper_mode=True → ордера симулируются, реальных сделок нет."""
    paper_mode: bool = True           # ВАЖНО: по умолчанию paper (без реальных денег)
    paper_start_balance: float = 1000.0  # стартовый банкролл для симуляции
    trade_amount_usd: float = 2.0
    min_tokens: float = 5.0           # минимум токенов для CLOB
    take_profit_pct: float = 0.25     # +25%
    stop_loss_pct: float = -0.20      # -20%
    position_hold_hours: int = 24
    max_price: float = 0.98           # не входить выше этой цены


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
    holders_timeout: int = 15


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

    return config


# Глобальный экземпляр конфигурации
CONFIG = load_config()
