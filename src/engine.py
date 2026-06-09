"""
Модуль 3 — Filter Engine.

Отсеивает шум перед уведомлением:
  • Анти-MEV/арбитраж: кошельки с аномальной частотой сделок временно мьютятся.
  • Консенсус: сигнал только если одна сторона доминирует (в N раз) и набрала min китов.
  • Калькулятор объёма: алерт только если суммарный notional доминирующей стороны > порога.
  • Дельта-нейтрал: помечает рынки, где киты одновременно в противоположных исходах
    (возможный хедж — односторонняя ставка опасна).
"""

import logging
from collections import defaultdict, deque
from statistics import median
from typing import List, Dict, Any, Optional

from src.config import CONFIG

logger = logging.getLogger("polymarket_bot.engine")


class FilterEngine:
    def __init__(self):
        # wallet -> deque[timestamps] последних сделок (для детекта частоты)
        self._trade_times: Dict[str, deque] = defaultdict(deque)
        # wallet -> ts, до которого кошелёк замьючен
        self._muted_until: Dict[str, float] = {}

    # ---- Анти-MEV / арбитраж ------------------------------------------------

    def observe(self, wallet: str, ts: float) -> None:
        """Регистрирует сделку кошелька и мьютит его при аномальной частоте."""
        if not wallet:
            return
        window = CONFIG.engine.mev_window_sec
        times = self._trade_times[wallet]
        times.append(ts)
        # выкидываем старше окна
        while times and ts - times[0] > window:
            times.popleft()
        if len(times) >= CONFIG.engine.mev_max_trades_per_window:
            self._muted_until[wallet] = ts + CONFIG.engine.mev_mute_sec
            logger.info(
                f"🔇 Мьютим {wallet[:10]}… "
                f"({len(times)} сделок/{window}с — похоже на MEV/арбитраж-бота)"
            )

    def is_muted(self, wallet: str, now: float) -> bool:
        until = self._muted_until.get(wallet)
        if until is None:
            return False
        if now >= until:
            del self._muted_until[wallet]
            return False
        return True

    # ---- Консенсус и анализ рынка ------------------------------------------

    def evaluate_market(self, entries: List[Dict[str, Any]], now: float) -> Optional[Dict[str, Any]]:
        """
        Анализирует все сделки одного рынка в окне. Возвращает сигнал или None.

        entries: dict с ключами wallet, side, price, outcome, notional, market,
                 cond_id, event_slug.
        """
        # Исключаем замьюченных (MEV/арбитраж)
        live = [e for e in entries if not self.is_muted(e["wallet"], now)]
        if not live:
            return None

        buy_w = {e["wallet"] for e in live if e["side"] == "BUY"}
        sell_w = {e["wallet"] for e in live if e["side"] == "SELL"}

        dom = CONFIG.engine.consensus_dominance
        min_w = CONFIG.monitor.min_wallets

        side = None
        if len(buy_w) >= len(sell_w) * dom and len(buy_w) >= min_w:
            side = "BUY"
        elif len(sell_w) >= len(buy_w) * dom and len(sell_w) >= min_w:
            side = "SELL"
        if not side:
            return None

        side_entries = [e for e in live if e["side"] == side]
        total_notional = sum(e["notional"] for e in side_entries)

        # Калькулятор объёма: алерт только при достаточном объёме
        if total_notional < CONFIG.engine.min_alert_notional:
            return None

        return {
            "side": side,
            "n_wallets": len(buy_w) if side == "BUY" else len(sell_w),
            "total_notional": total_notional,
            "consensus_outcome": self._consensus_outcome(live, side),
            "median_price": self._median_price(live, side),
            "delta_neutral": self._is_delta_neutral(live),
            "market": live[0].get("market", ""),
            "cond_id": live[0].get("cond_id", ""),
            "event_slug": live[0].get("event_slug", ""),
        }

    @staticmethod
    def _consensus_outcome(entries: List[Dict[str, Any]], side: str) -> Optional[str]:
        counts = defaultdict(int)
        for e in entries:
            if e["side"] == side:
                counts[e["outcome"]] += 1
        return max(counts, key=counts.get) if counts else None

    @staticmethod
    def _median_price(entries: List[Dict[str, Any]], side: str) -> float:
        prices = [e["price"] for e in entries if e["side"] == side and e["price"] > 0]
        if not prices:
            prices = [e["price"] for e in entries if e["price"] > 0]
        return median(prices) if prices else 0.5

    @staticmethod
    def _is_delta_neutral(entries: List[Dict[str, Any]]) -> bool:
        """
        Эвристика хеджа: киты одновременно ПОКУПАЮТ противоположные исходы.
        Если на BUY есть >= 2 разных outcome от разных кошельков — флаг риска.
        """
        if not CONFIG.engine.flag_delta_neutral:
            return False
        by_outcome = defaultdict(set)
        for e in entries:
            if e["side"] == "BUY":
                by_outcome[e["outcome"]].add(e["wallet"])
        active = [o for o, wallets in by_outcome.items() if wallets]
        return len(active) >= 2
