"""
QUICK WIN #3: Модуль кэширования и батчинга
- LRU cache для API запросов
- Batch Telegram messenger
- TimeboxedCache для кэширования с TTL
"""

import httpx
import time
import logging
from functools import lru_cache, wraps
from typing import Optional, Dict, Any, Callable
from datetime import datetime, timezone, timedelta
from collections import deque


logger = logging.getLogger(__name__)


class TimeboxedCache:
    """Простой кэш с TTL для значений (не требует Redis)"""
    
    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = ttl_seconds
        self.cache: Dict[str, tuple[Any, float]] = {}  # value, timestamp
    
    def get(self, key: str) -> Optional[Any]:
        if key not in self.cache:
            return None
        value, timestamp = self.cache[key]
        if datetime.now(timezone.utc).timestamp() - timestamp > self.ttl_seconds:
            del self.cache[key]
            return None
        return value
    
    def set(self, key: str, value: Any) -> None:
        self.cache[key] = (value, datetime.now(timezone.utc).timestamp())
    
    def clear(self) -> None:
        self.cache.clear()


def cache_with_ttl(ttl_seconds: int = 60):
    """Декоратор для кэширования с TTL"""
    cache = TimeboxedCache(ttl_seconds)
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Генерируем ключ кэша из имени функции и аргументов
            cache_key = f"{func.__name__}_{args}_{kwargs}"
            
            # Проверяем кэш
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
            
            # Вычисляем и кэшируем
            result = func(*args, **kwargs)
            cache.set(cache_key, result)
            return result
        
        wrapper.cache = cache
        return wrapper
    
    return decorator


# ============================================================
#  QUICK WIN: API кэш с LRU
# ============================================================

# Глобальный кэш для цен (LRU 100 entry)
@lru_cache(maxsize=100)
def cached_price(token_id: str, ttl_marker: int = 0) -> Optional[float]:
    """
    Кэшированный запрос цены. TTL = 30 сек (ttl_marker меняется каждые 30 сек)
    Использование: cached_price(token_id, ttl_marker=int(time.time() // 30))
    """
    # Реальный запрос должен быть в monitor.py, здесь stub
    return None


class PriceCacheManager:
    """Менеджер кэша цен с TTL"""
    
    def __init__(self, ttl_seconds: int = 30):
        self.cache = TimeboxedCache(ttl_seconds)
        self.ttl = ttl_seconds
    
    def get_price(self, token_id: str, fetch_fn: Callable) -> Optional[float]:
        """Получает цену из кэша или вызывает fetch_fn"""
        cached = self.cache.get(token_id)
        if cached is not None:
            return cached
        
        price = fetch_fn(token_id)
        if price is not None:
            self.cache.set(token_id, price)
        return price


# ============================================================
#  QUICK WIN #4: Batch Telegram
# ============================================================

class TelegramBatcher:
    """Батчинг сообщений Telegram (отправляет раз в N сек вместо на каждый сигнал)"""
    
    def __init__(
        self,
        token: str,
        chat_id: str,
        batch_interval_sec: int = 300,  # 5 минут
        max_batch_size: int = 10,
        timeout: int = 5
    ):
        self.token = token
        self.chat_id = chat_id
        self.batch_interval_sec = batch_interval_sec
        self.max_batch_size = max_batch_size
        self.timeout = timeout
        
        self.queue: deque[str] = deque(maxlen=max_batch_size)
        self.last_flush_time = time.time()
    
    def add_message(self, text: str) -> None:
        """Добавить сообщение в очередь"""
        self.queue.append(text)
        
        # Отправить если очередь полна
        if len(self.queue) >= self.max_batch_size:
            self.flush()
        # Или если прошло много времени
        elif time.time() - self.last_flush_time > self.batch_interval_sec:
            self.flush()
    
    def flush(self) -> None:
        """Отправить все сообщения батчем"""
        if not self.queue:
            return
        
        # Объединяем все сообщения
        batch_text = "\n\n".join(self.queue)
        self._send_telegram(batch_text)
        self.queue.clear()
        self.last_flush_time = time.time()
    
    def _send_telegram(self, text: str) -> None:
        """Отправляет сообщение в Telegram"""
        if not self.token or not self.chat_id:
            return
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            httpx.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML"
                },
                timeout=self.timeout
            )
            logger.info(f"📤 Telegram batch отправлен ({len(text)} chars)")
        except Exception as e:
            logger.error(f"Ошибка отправки в Telegram: {e}")
    
    def should_flush(self) -> bool:
        """Проверить нужно ли отправлять батч"""
        return (
            len(self.queue) >= self.max_batch_size or
            time.time() - self.last_flush_time > self.batch_interval_sec
        )


# Глобальный экземпляр батчера (инициализируется в monitor.py)
telegram_batcher: Optional[TelegramBatcher] = None


def send_telegram_batched(text: str) -> None:
    """Отправить сообщение в очередь батчера"""
    global telegram_batcher
    if telegram_batcher:
        telegram_batcher.add_message(text)
