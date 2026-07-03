"""
Атомарные JSON-файлы состояния (позиции, paper-филлы, метрики, антидубль).

Патч G (2026-07-04). Раньше запись шла через open("w") + json.dump: процесс,
убитый посреди записи (деплой-рестарт), оставлял битый файл, а load-функции
молча возвращали {} — позиции «испарялись» вместе с вложенными деньгами
(19–25 июня 2026 так списалось ~$179 без единого SELL-филла и строки в логах).

Правила:
  • запись — во временный файл рядом + os.replace (атомарно на POSIX);
  • битый файл при чтении НЕ затирается: переезжает в *.corrupt-<ts>,
    шлётся CRITICAL-алерт (Telegram через set_alert_hook), возвращается default.
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger("polymarket_bot.state")

# Хук для алертов (notifier.send + flush); ставится из tracker при старте,
# чтобы не тянуть Telegram-зависимость в этот модуль.
_alert_hook: Optional[Callable[[str], None]] = None


def set_alert_hook(fn: Callable[[str], None]) -> None:
    global _alert_hook
    _alert_hook = fn


def _alert(msg: str) -> None:
    logger.critical(msg)
    if _alert_hook:
        try:
            _alert_hook(msg)
        except Exception as e:  # алерт не должен ронять торговый цикл
            logger.error(f"alert hook: {e}")


def save_json(path: str, obj: Any, indent: Optional[int] = 2) -> None:
    """Атомарная запись: tmp-файл + fsync + os.replace."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_json(path: str, default: Any) -> Any:
    """
    Чтение состояния. Битый файл — в карантин + CRITICAL-алерт, возврат default.
    default может быть callable (фабрика), чтобы не делить один mutable объект.
    """
    if not os.path.exists(path):
        return default() if callable(default) else default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        quarantine = f"{path}.corrupt-{stamp}"
        try:
            os.replace(path, quarantine)
        except OSError:
            quarantine = "(переименовать не удалось)"
        _alert(
            f"🚨 <b>Файл состояния повреждён</b>: <code>{path}</code>\n"
            f"{e}\nПеремещён в {quarantine}. Продолжаю с пустым состоянием — "
            f"проверь вручную, в файле могли быть открытые позиции!"
        )
        return default() if callable(default) else default
