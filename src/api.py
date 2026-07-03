"""
Централизованный клиент Polymarket API.

Объединяет все обращения к Data API / Gamma API / CLOB API в одном месте
с единообразным retry, backoff на 429 и кэшированием цен.

Открытия по реальной схеме API (проверено июнь 2026):
  • /trades       — поле `size` это КОЛИЧЕСТВО ШЕРОВ, не USDC. Notional = size * price.
  • /positions    — отдаёт готовые realizedPnl / cashPnl по каждой позиции кошелька.
  • /value        — текущая стоимость портфеля кошелька.
"""

import time
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Callable

import httpx

from src.config import CONFIG

logger = logging.getLogger("polymarket_bot.api")


def usdc_notional(trade: Dict[str, Any]) -> float:
    """Корректный долларовый объём сделки: size (шеры) * price."""
    try:
        return float(trade.get("size", 0)) * float(trade.get("price", 0))
    except (TypeError, ValueError):
        return 0.0


def _get(url: str, params: Optional[dict] = None, timeout: int = 15,
         retries: int = 3) -> Optional[Any]:
    """GET с retry и backoff на 429. Возвращает распарсенный JSON или None."""
    for attempt in range(retries):
        try:
            resp = httpx.get(url, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(f"Rate limit (429) на {url}, ждём {wait}с...")
                time.sleep(wait)
                continue
            logger.warning(f"{url} вернул {resp.status_code}")
            return None
        except Exception as e:
            logger.warning(f"Сеть {url} (попытка {attempt + 1}/{retries}): {e}")
            time.sleep(2 * (attempt + 1))
    return None


# ============================================================
#  Data API
# ============================================================

def get_trades(limit: int = 500, market: Optional[str] = None,
               user: Optional[str] = None, offset: int = 0) -> List[Dict[str, Any]]:
    """Сделки. Можно фильтровать по market (conditionId) или user (адрес)."""
    params: Dict[str, Any] = {"limit": limit}
    if market:
        params["market"] = market
    if user:
        params["user"] = user
    if offset:
        params["offset"] = offset
    data = _get(f"{CONFIG.api.data_api}/trades", params,
                timeout=CONFIG.timeout.fetch_trades_timeout)
    return data if isinstance(data, list) else []


def get_positions(user: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Позиции кошелька с готовыми realizedPnl/cashPnl. Основа скоринга WinRate/PnL."""
    data = _get(f"{CONFIG.api.data_api}/positions",
                {"user": user, "limit": limit},
                timeout=CONFIG.timeout.positions_timeout)
    return data if isinstance(data, list) else []


def get_portfolio_value(user: str) -> float:
    """Текущая стоимость портфеля кошелька в USDC."""
    data = _get(f"{CONFIG.api.data_api}/value", {"user": user},
                timeout=CONFIG.timeout.default_timeout)
    if isinstance(data, list) and data:
        try:
            return float(data[0].get("value", 0))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def get_leaderboard(rank_type: str = "pnl", window: str = "30d",
                    limit: int = 50) -> List[Dict[str, Any]]:
    """
    Топ трейдеров Polymarket (страница /leaderboard).
    Поля записи: proxyWallet, userName, pnl, vol, rank.
    API отдаёт максимум 50 записей; параметр window принимается,
    но (проверено июнь 2026) на выдачу влияет слабо.
    """
    data = _get(f"{CONFIG.api.data_api}/v1/leaderboard",
                {"window": window, "rankType": rank_type, "limit": limit},
                timeout=CONFIG.timeout.default_timeout)
    return data if isinstance(data, list) else []


def get_first_trade_ts(user: str, max_pages: int = 4) -> Optional[int]:
    """
    Возраст кошелька: timestamp первой сделки (best-effort).
    Data API отдаёт сделки от свежих к старым — пагинируем до конца или до max_pages.

    Возвращает точный timestamp первого трейда, только если история исчерпана
    в пределах max_pages. Если упёрлись в лимит — возвращает None («возраст
    неизвестен»): у гиперактивного бота все 2000 последних сделок могут быть
    за последние сутки, и min(timestamp) ложно пометил бы его «молодым инсайдером».
    """
    oldest: Optional[int] = None
    offset = 0
    page = 500
    for _ in range(max_pages):
        batch = get_trades(limit=page, user=user, offset=offset)
        if not batch:
            return oldest
        ts_values = [int(t.get("timestamp", 0)) for t in batch if t.get("timestamp")]
        if ts_values:
            oldest = min(ts_values) if oldest is None else min(oldest, min(ts_values))
        if len(batch) < page:
            return oldest
        offset += page
    return None  # история глубже max_pages — возраст неизвестен


# ============================================================
#  Gamma API (метаданные и разрешение рынков)
# ============================================================

def get_market_resolution(condition_id: str) -> Optional[str]:
    """Возвращает выигравший outcome (lower) если рынок фактически разрешён, иначе None.

    ВАЖНО: фильтр Gamma — это `condition_ids` (мн.ч.). `conditionId` игнорируется
    и отдаёт случайный список рынков.

    Polymarket держит closed=False ещё долго после фактического исхода (задержка
    UMA, статус disputed), поэтому опираться только на флаг closed нельзя — петля
    обратной связи зависала бы на сутки-двое. Рынок считаем разрешённым, если он
    официально closed ЛИБО его endDate уже прошёл И цена одного исхода вышла на
    экстремум (>= 0.99).
    """
    data = _get(f"{CONFIG.api.gamma_api}/markets", {"condition_ids": condition_id},
                timeout=CONFIG.timeout.default_timeout)
    if not isinstance(data, list) or not data:
        return None
    m = data[0]
    try:
        outcomes = json.loads(m.get("outcomes", "[]"))
        prices = json.loads(m.get("outcomePrices", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None
    if len(outcomes) != len(prices) or not outcomes:
        return None
    winner, max_price = None, 0.0
    for o, p in zip(outcomes, prices):
        try:
            pf = float(p)
        except (TypeError, ValueError):
            continue
        if pf > max_price:
            max_price, winner = pf, o
    if not winner or max_price < 0.99:
        return None
    # Официально закрыт — доверяем безусловно.
    if m.get("closed"):
        return winner.strip().lower()
    # Ещё не closed, но событие прошло и цена на экстремуме → фактически решён.
    end = m.get("endDate")
    if end:
        try:
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= end_dt:
                return winner.strip().lower()
        except (ValueError, TypeError):
            pass
    return None


def get_market_end_date(condition_id: str) -> Optional[datetime]:
    """
    endDate рынка из Gamma (aware UTC datetime) или None.
    Патч G: дешёвый лонгшот без стопа держим до разрешения рынка,
    поэтому close_at позиции привязывается к endDate, а не к 24ч-таймеру.
    """
    data = _get(f"{CONFIG.api.gamma_api}/markets", {"condition_ids": condition_id},
                timeout=CONFIG.timeout.default_timeout)
    if not isinstance(data, list) or not data:
        return None
    end = data[0].get("endDate")
    if not end:
        return None
    try:
        dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# ============================================================
#  CLOB API (токены и цены для торговли)
# ============================================================

def get_market_tokens(condition_id: str) -> Optional[Any]:
    """tokenId для каждого outcome. Возвращает dict {outcome: token_id}, 'CLOSED' или None."""
    data = _get(f"{CONFIG.api.clob_api}/markets/{condition_id}",
                timeout=CONFIG.timeout.market_tokens_timeout)
    if isinstance(data, dict):
        if data.get("closed"):
            return "CLOSED"
        tokens = data.get("tokens", [])
        if tokens:
            return {t.get("outcome", "").lower(): t.get("token_id") for t in tokens}

    # Fallback через Gamma API
    gamma = _get(f"{CONFIG.api.gamma_api}/markets", {"condition_ids": condition_id},
                 timeout=CONFIG.timeout.market_tokens_timeout)
    if isinstance(gamma, list) and gamma:
        m = gamma[0]
        tokens_raw = m.get("clobTokenIds")
        if tokens_raw:
            try:
                tokens = json.loads(tokens_raw)
                outcomes = json.loads(m.get("outcomes", '["Yes", "No"]'))
                return {outcomes[i].lower(): tokens[i] for i in range(len(tokens))}
            except (json.JSONDecodeError, IndexError):
                pass
    return None


def _fetch_price(token_id: str, side: str = "sell") -> Optional[float]:
    data = _get(f"{CONFIG.api.clob_api}/price",
                {"token_id": token_id, "side": side},
                timeout=CONFIG.timeout.price_timeout, retries=1)
    if isinstance(data, dict):
        try:
            return float(data.get("price", 0))
        except (TypeError, ValueError):
            return None
    return None


# Кэш цен (TTL из конфига), ключ — (token_id, side): bid и ask не смешиваем
_price_cache: Dict[tuple, tuple] = {}


def get_price(token_id: str, side: str = "sell") -> Optional[float]:
    """Цена токена с TTL-кэшем. side='sell' — bid (для выхода), 'buy' — ask (для входа)."""
    now = time.time()
    cached = _price_cache.get((token_id, side))
    if cached and now - cached[1] < CONFIG.cache.price_cache_ttl_sec:
        return cached[0]
    price = _fetch_price(token_id, side)
    if price is not None:
        _price_cache[(token_id, side)] = (price, now)
    return price
