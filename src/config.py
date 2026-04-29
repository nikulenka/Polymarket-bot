"""
Централизованная конфигурация бота Polymarket.
Загружается из .env и может переопределяться через CLI/env переменные.
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class APIConfig:
    """Конфигурация API endpoints"""
    data_api: str = "https://data-api.polymarket.com"
    gamma_api: str = "https://gamma-api.polymarket.com"
    clob_api: str = "https://clob.polymarket.com"
    openrouter_api: str = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class MonitorConfig:
    """Конфигурация мониторинга и сигналов"""
    poll_interval: int = 30  # сек между проверками
    signal_window: int = 43200  # 12 часов для анализа
    min_wallets: int = 2  # минимум китов на сигнал
    min_size_usdc: float = 50.0  # порог для известных китов
    whale_min_size: float = 1000.0  # любая сделка >= $1000 = кит
    heartbeat_interval: int = 600  # пульс в лог (10 мин)


@dataclass
class ValidationConfig:
    """Конфигурация валидации сигналов"""
    use_claude: bool = True
    claude_min_confidence: int = 80  # процент
    claude_timeout: int = 15  # сек
    claude_model: str = "anthropic/claude-3.5-haiku"
    fallback_to_heuristics: bool = True  # если Claude недоступен


@dataclass
class TradingConfig:
    """Конфигурация торговли"""
    trade_amount_usd: float = 2.0  # сумма на сделку
    min_tokens: float = 5.0  # минимум токенов для CLOB
    take_profit_pct: float = 0.25  # 25%
    stop_loss_pct: float = -0.20  # -20%
    position_hold_hours: int = 24  # сколько часов держать позицию
    max_price: float = 0.98  # не торговать выше этой цены


@dataclass
class CacheConfig:
    """Конфигурация кэширования"""
    max_seen_hashes: int = 30_000  # лимит хэшей в памяти
    max_buffer_size: int = 50_000  # лимит записей в rolling_buffer
    api_cache_ttl_sec: int = 60  # TTL для API кэша (в секундах)
    balance_cache_ttl_sec: int = 30  # кэш баланса на 30 сек
    price_cache_ttl_sec: int = 30  # кэш цен на 30 сек


@dataclass
class FileConfig:
    """Конфигурация файлов и папок"""
    top_wallets_path: str = "data/top_wallets.csv"
    signals_file: str = "data/sent_signals.json"
    positions_file: str = "data/open_positions.json"
    metrics_file: str = "data/metrics.json"
    log_dir: str = "logs"
    log_file: str = "logs/signals.log"
    monitor_log: str = "logs/monitor.log"
    signals_ttl_hours: int = 48  # время жизни записей в sent_signals.json


@dataclass
class TelegramConfig:
    """Конфигурация Telegram"""
    enabled: bool = True
    token: Optional[str] = None
    chat_id: Optional[str] = None
    batch_interval_sec: int = 300  # отправлять батч раз в 5 минут
    max_batch_size: int = 10  # максимум сообщений в батче


@dataclass
class TimeoutConfig:
    """Конфигурация таймаутов (в секундах)"""
    default_timeout: int = 15
    fetch_trades_timeout: int = 15
    market_tokens_timeout: int = 10
    price_timeout: int = 5
    telegram_timeout: int = 5


@dataclass
class MarketFilterConfig:
    """Конфигурация фильтрации рынков"""
    skip_patterns: List[str] = None
    compiled_filter: Optional[re.Pattern] = None
    
    def __post_init__(self):
        if self.skip_patterns is None:
            self.skip_patterns = [
                "NBA", "NFL", "NHL", "MLB", "soccer", "win the", 
                "beat the", "Series", "Finals", "Championship",
                "Buccaneers", "Lakers", "Spurs", "Hawks", "Knicks",
                "Celtics", "Warriors", "Nuggets", "Playoffs",
                "AM-", "PM-", "AM ET", "PM ET", ":00AM", ":00PM", "Up or Down -",
                " vs ", " vs. ", " FC ", " United ", " Real ", " City ", " Atletico ",
                "Madrid Open", "Tennis", "ATP", "WTA", "Winner", "Map 1", "Map 2",
                "Counter-Strike", "CS2", "Dota", "Esports", "UFC", "MMA", "Boxing",
                "Total Sets", "O/U 2.5", "O/U 3.5", "O/U 4.5", "Total Goals",
                "Premier League", "Champions League", "La Liga", "Bundesliga"
            ]
        # QUICK WIN: Компилируем regex один раз вместо проверки каждого паттерна
        self.compiled_filter = re.compile(
            '|'.join(re.escape(p) for p in self.skip_patterns),
            re.IGNORECASE
        )
    
    def should_skip(self, market_name: str) -> bool:
        """O(1) проверка вместо O(N) цикла"""
        return bool(self.compiled_filter.search(market_name))


@dataclass
class BotConfig:
    """Главная конфигурация бота"""
    api: APIConfig = None
    monitor: MonitorConfig = None
    validation: ValidationConfig = None
    trading: TradingConfig = None
    cache: CacheConfig = None
    files: FileConfig = None
    telegram: TelegramConfig = None
    timeout: TimeoutConfig = None
    market_filter: MarketFilterConfig = None
    
    def __post_init__(self):
        if self.api is None:
            self.api = APIConfig()
        if self.monitor is None:
            self.monitor = MonitorConfig()
        if self.validation is None:
            self.validation = ValidationConfig()
        if self.trading is None:
            self.trading = TradingConfig()
        if self.cache is None:
            self.cache = CacheConfig()
        if self.files is None:
            self.files = FileConfig()
        if self.telegram is None:
            self.telegram = TelegramConfig()
        if self.timeout is None:
            self.timeout = TimeoutConfig()
        if self.market_filter is None:
            self.market_filter = MarketFilterConfig()


def load_config() -> BotConfig:
    """Загружает конфигурацию из .env и env переменных"""
    config = BotConfig()
    
    # Переопределяем из env переменных если они заданы
    config.telegram.token = os.getenv("TELEGRAM_TOKEN")
    config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID")
    config.validation.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    
    # Числовые параметры могут быть переопределены через env
    if poll_int := os.getenv("POLL_INTERVAL"):
        config.monitor.poll_interval = int(poll_int)
    
    return config


# Глобальный экземпляр конфигурации
CONFIG = load_config()
