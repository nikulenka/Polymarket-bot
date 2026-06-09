"""
Модуль 4 — Alert Telegram.

Формирует человекочитаемый сигнал. По требованиям в сообщении обязательно:
  1. Ссылка на рынок Polymarket.
  2. Суть ставки (киты зашли в YES/NO, BUY/SELL).
  3. Статистика кошельков (WinRate, PnL, возраст, инсайдер).
  4. Цена контракта в момент входа (для оценки потенциала флиппинга).
"""

import logging
from html import escape
from typing import List, Dict, Any, Optional

from src.config import CONFIG
from src.cache import TelegramBatcher

logger = logging.getLogger("polymarket_bot.notifier")


class Notifier:
    def __init__(self):
        self.batcher: Optional[TelegramBatcher] = None
        if CONFIG.telegram.enabled and CONFIG.telegram.token and CONFIG.telegram.chat_id:
            self.batcher = TelegramBatcher(
                token=CONFIG.telegram.token,
                chat_id=CONFIG.telegram.chat_id,
                batch_interval_sec=CONFIG.telegram.batch_interval_sec,
                max_batch_size=CONFIG.telegram.max_batch_size,
                timeout=CONFIG.timeout.telegram_timeout,
            )
        else:
            logger.warning(
                "Telegram отключён или нет TELEGRAM_TOKEN/CHAT_ID — алерты идут только в лог."
            )

    @property
    def active(self) -> bool:
        return self.batcher is not None

    def send(self, text: str) -> None:
        if self.batcher:
            self.batcher.add_message(text)
        else:
            logger.info(f"[ALERT-LOG]\n{text}")

    def flush(self) -> None:
        if self.batcher:
            self.batcher.flush()

    def maybe_flush(self) -> None:
        if self.batcher and self.batcher.should_flush():
            self.batcher.flush()

    # ------------------------------------------------------------------

    @staticmethod
    def market_url(signal: Dict[str, Any]) -> str:
        slug = signal.get("event_slug") or ""
        if slug:
            return f"{CONFIG.api.site_url}/event/{slug}"
        return CONFIG.api.site_url

    @staticmethod
    def _whale_summary(whales: List[Dict[str, Any]]) -> str:
        """Агрегированная стата по кошелькам сигнала."""
        if not whales:
            return "крупные сделки (не из списка отслеживаемых)"
        best_wr = max((w.get("winrate", 0) for w in whales), default=0) * 100
        total_pnl = sum(w.get("total_pnl", 0) for w in whales)
        insiders = [w for w in whales if w.get("is_insider")]
        parts = [f"WinRate до {best_wr:.0f}%", f"PnL ${total_pnl:,.0f}"]
        if insiders:
            ages = [w.get("age_days") for w in insiders if w.get("age_days") is not None]
            age_str = f", age {min(ages):.0f}д" if ages else ""
            parts.append(f"{len(insiders)}× 🥷 INSIDER{age_str}")
        return " | ".join(parts)

    def format_signal(self, n: int, signal: Dict[str, Any],
                      whales: List[Dict[str, Any]], trade_status: str) -> str:
        """Собирает HTML-сообщение для Telegram."""
        side = signal["side"]
        outcome = signal.get("consensus_outcome") or "?"
        action = "зашли в" if side == "BUY" else "выходят из"
        market = escape(signal.get("market", "")[:120])
        url = self.market_url(signal)

        signal_type = signal.get("signal_type", "consensus")
        type_label = "💎 Trusted Whale" if signal_type == "trusted_whale" else "🤝 Консенсус"

        lines = [
            f"🚨 <b>СИГНАЛ #{n}</b> [{type_label}]",
            f'<a href="{url}">{market}</a>',
            f"Киты {action} <b>{escape(str(outcome).upper())}</b> ({side}) — {signal['n_wallets']} кош.",
            f"💰 Объём: ${signal['total_notional']:,.0f} | Цена входа: {signal['median_price']:.3f}",
            f"👤 {escape(self._whale_summary(whales))}",
        ]
        if signal.get("delta_neutral"):
            lines.append("⚠️ <i>Возможен дельта-нейтральный хедж — односторонняя ставка рискованна</i>")
        lines.append(f"<b>{escape(trade_status)}</b>")
        return "\n".join(lines)
