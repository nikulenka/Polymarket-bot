"""
Модуль 2 — Live Tracker (главный цикл).

Следит за сделками отслеживаемых китов (из БД), прогоняет их через Filter Engine
и шлёт обогащённый алерт. В paper-режиме симулирует сделки.

Запуск:  PYTHONPATH=. python3 -m src.tracker   (или ./run.sh)
"""

import os
import json
import time
import logging
import traceback
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone, timedelta

from src import api, db
from src.config import CONFIG
from src.engine import FilterEngine
from src.notifier import Notifier
from src.trader import place_bet, close_position, get_usdc_balance
from src.logger import setup_logging

logger = setup_logging(log_file=CONFIG.files.log_file, json_format=False)
engine = FilterEngine()
notifier = Notifier()

os.makedirs(CONFIG.files.log_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)


# ============================================================
#  Антидубликат сигналов (cooldown, persist в sent_signals.json)
# ============================================================

_sent_cache = None


def _load_sent():
    if os.path.exists(CONFIG.files.signals_file):
        try:
            with open(CONFIG.files.signals_file) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_sent(sent):
    with open(CONFIG.files.signals_file, "w") as f:
        json.dump(sent, f)


def is_duplicate(key, cooldown_hours=12):
    global _sent_cache
    if _sent_cache is None:
        _sent_cache = _load_sent()
    if key in _sent_cache:
        try:
            last = datetime.fromisoformat(_sent_cache[key])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) - last < timedelta(hours=cooldown_hours)
        except Exception:
            return False
    return False


def mark_sent(key):
    global _sent_cache
    if _sent_cache is None:
        _sent_cache = _load_sent()
    _sent_cache[key] = datetime.now(timezone.utc).isoformat()
    now = datetime.now(timezone.utc)
    ttl = timedelta(hours=CONFIG.files.signals_ttl_hours)
    _sent_cache = {
        k: v for k, v in _sent_cache.items()
        if _within_ttl(v, now, ttl)
    }
    _save_sent(_sent_cache)


def _within_ttl(v, now, ttl):
    try:
        ts = datetime.fromisoformat(v)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return now - ts < ttl
    except Exception:
        return False


# ============================================================
#  Позиции (paper/live)
# ============================================================

def load_positions():
    if os.path.exists(CONFIG.files.positions_file):
        try:
            with open(CONFIG.files.positions_file) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_positions(pos):
    with open(CONFIG.files.positions_file, "w") as f:
        json.dump(pos, f, indent=2)


def committed_value(positions: dict) -> float:
    """Сумма по цене входа (cost basis), вложенная в открытые позиции сейчас."""
    return sum(p.get("entry_price", 0) * p.get("tokens", 0) for p in positions.values())


def resolve_token_id(tokens_map, target_outcome, signal_side):
    """
    BUY-сигнал → покупаем токен target_outcome.
    SELL-сигнал → покупаем ПРОТИВОПОЛОЖНЫЙ (шортить на Polymarket нельзя).
    Возвращает (token_id, outcome) купленного исхода — outcome нужен,
    чтобы при разрешении рынка определить выигрыш. Или (None, None).
    """
    if not tokens_map or not target_outcome:
        return None, None
    target = target_outcome.lower()
    if signal_side == "BUY":
        if target in tokens_map:
            return tokens_map[target], target
        if "yes" in tokens_map:
            return tokens_map["yes"], "yes"
        return None, None
    # SELL → противоположный
    opposite = {k: v for k, v in tokens_map.items() if k != target}
    if "no" in opposite and target == "yes":
        return opposite["no"], "no"
    if "yes" in opposite and target == "no":
        return opposite["yes"], "yes"
    if len(opposite) == 1:
        outcome = next(iter(opposite))
        return opposite[outcome], outcome
    if "no" in tokens_map:
        return tokens_map["no"], "no"
    return None, None


def _close_msg(label: str, p: dict, entry: float, exit_price: float, balance: float, committed: float = 0.0) -> str:
    """Сообщение о закрытии позиции с P&L сделки и состоянием счёта."""
    tokens = p.get("tokens", 0)
    cost = tokens * entry
    proceeds = tokens * exit_price
    pnl = proceeds - cost
    pnl_pct = (pnl / cost * 100) if cost > 0 else 0
    start = CONFIG.trading.paper_start_balance
    total_pnl = balance - start
    pnl_sign = "+" if pnl >= 0 else ""
    total_sign = "+" if total_pnl >= 0 else ""
    mode = "PAPER" if CONFIG.trading.paper_mode else "LIVE"
    market = p.get("market", "")[:80]

    logger.info(
        f"{label} [{mode}] | {market} | "
        f"{entry:.3f}→{exit_price:.3f} | P&L: {pnl_sign}{pnl:.2f}$ ({pnl_sign}{pnl_pct:.1f}%) | "
        f"баланс ${balance:.2f} (итого {total_sign}{total_pnl:.2f}$)"
    )

    from html import escape
    market = escape(market)
    return (
        f"<b>{label}</b> [{mode}]\n"
        f"{market}\n"
        f"Вход: {entry:.3f} → Выход: {exit_price:.3f}\n"
        f"P&L сделки: <b>{pnl_sign}{pnl:.2f}$</b> ({pnl_sign}{pnl_pct:.1f}%)\n"
        f"{notifier.balance_line(balance, committed)}"
    )


def _settle_resolved(token_id: str, p: dict, exit_price: float) -> bool:
    """
    Закрытие позиции в разрешённом рынке.
    Paper — виртуальный SELL по 1.0/0.0. Live — продать разрешённый токен
    через CLOB нельзя (нужен redeem через CTF) → снимаем с трекинга,
    redeem делается вручную/отдельным шагом.
    """
    if CONFIG.trading.paper_mode:
        return close_position(token_id, p["tokens"], exit_price)
    logger.warning(f"LIVE: рынок разрешился — redeem вручную: {p.get('market', '')[:80]}")
    return True


def exit_params(entry: float):
    """
    Патчи A/F: профиль выхода зависит от цены входа.
    Возвращает (partial_take_delta, partial_take_fraction, take_profit_delta, stop_loss_delta).
    stop_loss_delta == None → стоп выключен (едем до TP/разрешения).

    • Дешёвый лонгшот (вход < cheap_entry_max): не фиксируем частично, едем до
      широкого TP/разрешения — именно тут живёт правый хвост (мунбэги); стоп выключен,
      чтобы шум рынка не выбивал позицию до разрешения (патч F).
    • Дорогой фаворит (вход >= expensive_entry_min): апсайд мал, забираем быстро,
      стоп −15c оправдан.
    • Середина: базовый профиль (флип на +5c, TP +10c, SL базовый).
    """
    t = CONFIG.trading
    if entry < t.cheap_entry_max:
        return (t.partial_take_delta, t.cheap_partial_take_fraction,
                t.cheap_take_profit_delta, t.cheap_stop_loss_delta)
    if entry >= t.expensive_entry_min:
        return (t.expensive_partial_take_delta, t.partial_take_fraction,
                t.expensive_take_profit_delta, t.expensive_stop_loss_delta)
    return (t.partial_take_delta, t.partial_take_fraction,
            t.take_profit_delta, t.stop_loss_delta)


def manage_positions():
    """TP / SL / выход по времени / закрытие по исходу разрешённого рынка."""
    positions = load_positions()
    if not positions:
        return
    now = datetime.now(timezone.utc)
    to_delete = []
    dirty = False  # позиции изменились без удаления (частичная фиксация)

    for token_id, p in positions.items():
        try:
            close_at = datetime.fromisoformat(p["close_at"])
            if close_at.tzinfo is None:
                close_at = close_at.replace(tzinfo=timezone.utc)
            cur = api.get_price(token_id)
            entry = p.get("entry_price", 0)

            if cur is None:
                # 404 = токен исчез из CLOB → рынок, скорее всего, разрешился.
                # Узнаём исход через Gamma и закрываем по 1.0/0.0 — НЕ по входу,
                # иначе все выигрыши фиксируются как PnL=0, а убытки — как убытки.
                winner = api.get_market_resolution(p.get("cond_id", "")) if p.get("cond_id") else None
                bought = (p.get("outcome") or "").lower()
                if winner is not None and bought:
                    won = (winner == bought)
                    exit_price = 1.0 if won else 0.0
                    label = "✅ РЫНОК ВЫИГРАЛ" if won else "❌ РЫНОК ПРОИГРАЛ"
                    if _settle_resolved(token_id, p, exit_price):
                        to_delete.append(token_id)
                        bal = get_usdc_balance()
                        rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                        notifier.send(_close_msg(label, p, entry, exit_price, bal, rest))
                else:
                    # Исход ещё не известен (или позиция без cond_id — старый формат).
                    # Ждём grace-период после close_at, потом закрываем нейтрально.
                    grace = timedelta(hours=CONFIG.trading.resolution_grace_hours)
                    if now > close_at + grace:
                        if close_position(token_id, p["tokens"], entry):
                            to_delete.append(token_id)
                            bal = get_usdc_balance()
                            rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                            notifier.send(_close_msg("⏰ РЫНОК РАЗРЕШИЛСЯ (исход неизвестен)", p, entry, entry, bal, rest))
                continue

            if entry > 0:
                # Патчи A/F: профиль выхода зависит от цены входа (флип/TP/SL).
                ptake_delta, ptake_frac, tp_delta, sl_delta = exit_params(entry)

                # Бинарный рынок фактически решён: цена у 1.0 (WIN) или 0.0 (LOSS)
                if cur >= 0.97:
                    if close_position(token_id, p["tokens"], cur):
                        to_delete.append(token_id)
                        bal = get_usdc_balance()
                        rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                        notifier.send(_close_msg("✅ РЫНОК ВЫИГРАЛ", p, entry, cur, bal, rest))
                    continue
                if cur <= 0.03:
                    if sl_delta is None:
                        # Патч F: для дешёвых входов стоп выключен. 3 цента на
                        # live-рынке (особенно спорт в моменте) часто шум игры,
                        # а не реальное разрешение — подтверждаем через Gamma,
                        # иначе фиксируем тот же ложный убыток, который патч F
                        # должен был устранить.
                        winner = api.get_market_resolution(p.get("cond_id", "")) if p.get("cond_id") else None
                        bought = (p.get("outcome") or "").lower()
                        if winner is not None and bought and winner != bought:
                            if _settle_resolved(token_id, p, 0.0):
                                to_delete.append(token_id)
                                bal = get_usdc_balance()
                                rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                                notifier.send(_close_msg("❌ РЫНОК ПРОИГРАЛ", p, entry, 0.0, bal, rest))
                            continue
                        # Не подтверждено Gamma — рынок ещё не решён, едем дальше
                        # (до TP/close_at), стоп по-прежнему выключен.
                    else:
                        if close_position(token_id, p["tokens"], cur):
                            to_delete.append(token_id)
                            bal = get_usdc_balance()
                            rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                            notifier.send(_close_msg("❌ РЫНОК ПРОИГРАЛ", p, entry, cur, bal, rest))
                        continue

                # TP/SL в пунктах вероятности: на бинарном рынке проценты от цены
                # не работают (при входе 0.9 даже +25% недостижимы).
                change = cur - entry

                # Флиппинг (из требований): вероятность сместилась в нашу сторону →
                # фиксируем часть позиции, остаток едет до TP/SL/разрешения.
                # Для дешёвых лонгшотов ptake_frac=0 → флип выключен, хвост едет дальше.
                if (ptake_frac > 0
                        and not p.get("partial_done")
                        and ptake_delta <= change < tp_delta):
                    part = round(p["tokens"] * ptake_frac, 4)
                    if part > 0 and close_position(token_id, part, cur):
                        p["tokens"] = round(p["tokens"] - part, 4)
                        p["partial_done"] = True
                        dirty = True
                        bal = get_usdc_balance()
                        pnl = part * (cur - entry)
                        rest = committed_value(positions)  # позиция остаётся открытой, только урезана
                        notifier.send(
                            f"💰 <b>ЧАСТИЧНАЯ ФИКСАЦИЯ</b> [{'PAPER' if CONFIG.trading.paper_mode else 'LIVE'}]\n"
                            f"{p.get('market', '')[:80]}\n"
                            f"Продано {part:.1f} шеров @ {cur:.3f} (вход {entry:.3f}), "
                            f"P&L сделки: +{pnl:.2f}$ | остаток {p['tokens']:.1f} шеров\n"
                            f"{notifier.balance_line(bal, rest)}"
                        )

                if change >= tp_delta:
                    if close_position(token_id, p["tokens"], cur):
                        to_delete.append(token_id)
                        bal = get_usdc_balance()
                        rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                        notifier.send(_close_msg("✅ TAKE PROFIT", p, entry, cur, bal, rest))
                    continue
                # Патч F: для дешёвых входов sl_delta=None → стоп выключен,
                # позиция едет до TP/разрешения (шум рынка её не выбивает).
                if sl_delta is not None and change <= sl_delta:
                    if close_position(token_id, p["tokens"], cur):
                        to_delete.append(token_id)
                        bal = get_usdc_balance()
                        rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                        notifier.send(_close_msg("🛑 STOP LOSS", p, entry, cur, bal, rest))
                    continue

            if now > close_at:
                if close_position(token_id, p["tokens"], cur):
                    to_delete.append(token_id)
                    bal = get_usdc_balance()
                    rest = committed_value({k: v for k, v in positions.items() if k != token_id})
                    notifier.send(_close_msg("⏰ ВРЕМЯ ВЫШЛО", p, entry, cur, bal, rest))
        except Exception as e:
            logger.error(f"manage_positions {token_id}: {e}")

    if to_delete or dirty:
        for tid in to_delete:
            positions.pop(tid, None)
        save_positions(positions)
        notifier.flush()  # TP/SL/timeout/частичная фиксация — тоже немедленно


# ============================================================
#  Управление капиталом (Фаза 3)
# ============================================================

def position_size_usd(whale_stats, price, balance):
    """
    Fractional Kelly: f* = (p − c)/(1 − c), p — winrate лучшего кита сигнала
    (консервативный прокси вероятности), c — цена входа.
    Без оценённого края — базовая ставка. Кап — position_max_pct банкролла.
    """
    base = CONFIG.trading.trade_amount_usd
    if balance <= 0 or not (0 < price < 1):
        return base
    best_wr = max((w.get("winrate") or 0 for w in whale_stats), default=0)
    p = min(best_wr, 0.95)
    if p <= price:
        return base  # по нашей оценке края нет — минимальный размер
    f_star = (p - price) / (1.0 - price)
    size = balance * CONFIG.trading.kelly_fraction * f_star
    # Патч B: дешёвый вход — асимметричный край, повышенный кап.
    max_pct = (CONFIG.trading.position_max_pct_cheap
               if price < CONFIG.trading.cheap_entry_max
               else CONFIG.trading.position_max_pct)
    cap = balance * max_pct
    return round(max(base, min(size, cap)), 2)


def entry_blocked(positions, cond_id):
    """Лимиты экспозиции. Возвращает строку-причину или None."""
    if len(positions) >= CONFIG.trading.max_open_positions:
        return f"⏭ Пропуск (лимит открытых позиций: {CONFIG.trading.max_open_positions})"
    if any(p.get("cond_id") == cond_id for p in positions.values()):
        return "⏭ Пропуск (уже есть позиция в этом рынке)"
    return None


def _load_metrics():
    if os.path.exists(CONFIG.files.metrics_file):
        try:
            with open(CONFIG.files.metrics_file) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_metrics(m):
    with open(CONFIG.files.metrics_file, "w") as f:
        json.dump(m, f, indent=2)


def daily_stop_active(balance):
    """
    Дневной стоп-лосс: просадка от баланса на начало дня UTC >= daily_max_loss_pct
    → новые входы запрещены до следующего дня. Управление открытыми позициями
    (TP/SL/закрытия) продолжает работать.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    m = _load_metrics()
    if m.get("day") != today:
        m.update({"day": today, "day_start_balance": balance, "paused": False})
        _save_metrics(m)
        return False
    start = m.get("day_start_balance") or balance
    if start <= 0:
        return False
    drawdown = (start - balance) / start
    if drawdown >= CONFIG.trading.daily_max_loss_pct:
        if not m.get("paused"):
            m["paused"] = True
            _save_metrics(m)
            notifier.send(
                f"⛔ <b>Дневной стоп-лосс</b>: просадка −{drawdown*100:.1f}% "
                f"(${start:.2f} → ${balance:.2f}). Новые входы на паузе до завтра (UTC).\n"
                f"{notifier.balance_line(balance, committed_value(load_positions()))}"
            )
            notifier.flush()
        return True
    return False


# ============================================================
#  Исполнение сделки по сигналу (paper/live)
# ============================================================

def execute_trade(signal, positions, whale_stats=None):
    """Пробует открыть позицию по сигналу. Возвращает строку-статус."""
    cond_id = signal["cond_id"]
    whale_price = signal["median_price"]

    if not (0.01 <= whale_price < 1):
        return "⏭ Пропуск (цена кита вне диапазона)"

    blocked = entry_blocked(positions, cond_id)
    if blocked:
        return blocked

    tokens_map = api.get_market_tokens(cond_id)
    if tokens_map == "CLOSED":
        return "⏭ Пропуск (рынок закрыт)"
    if not tokens_map:
        return "⏭ Пропуск (нет TokenID)"

    token_id, bought_outcome = resolve_token_id(tokens_map, signal["consensus_outcome"], signal["side"])
    if not token_id:
        return "⏭ Пропуск (не нашли токен)"

    # Входим по ТЕКУЩЕМУ ask, а не по цене сделки кита (ей может быть до 12 часов).
    price = api.get_price(token_id, side="buy")
    if price is None or not (0.01 <= price < 1):
        return "⏭ Пропуск (нет цены в стакане)"
    if price > CONFIG.trading.max_price:
        return f"⏭ Пропуск (цена {price:.3f} > {CONFIG.trading.max_price})"

    # Патчи B/C: ограничения для ОДИНОЧНОГО сигнала (trusted_whale).
    # Консенсус (2+ кошелька) не трогаем — его край в группе, а не в winrate
    # одного кита. По анализу: зона фаворитов 0.5–0.7 убыточна, SELL даёт 25%
    # правоты, а одиночка без оценённого края = микроставки на $0 прибыли.
    if signal.get("signal_type") == "trusted_whale":
        if signal["side"] == "SELL" and not CONFIG.trading.single_whale_allow_sell:
            return "⏭ Пропуск (одиночный кит на SELL — нужен консенсус)"
        if price >= CONFIG.trading.single_whale_max_price:
            return (f"⏭ Пропуск (одиночный кит, цена {price:.2f} ≥ "
                    f"{CONFIG.trading.single_whale_max_price} — фавориты только консенсусом)")
        if CONFIG.trading.skip_when_no_edge:
            best_wr = max((w.get("winrate") or 0 for w in (whale_stats or [])), default=0)
            if best_wr <= price:
                return f"⏭ Пропуск (нет края: WinRate кита {best_wr:.0%} ≤ цена {price:.2f})"

    # Цена кита указана для исхода из сигнала; при SELL мы покупаем
    # противоположный токен, его справедливая цена ≈ 1 − цена кита.
    ref_price = whale_price if signal["side"] == "BUY" else 1.0 - whale_price
    if price - ref_price > CONFIG.trading.max_entry_slippage:
        return f"⏭ Пропуск (поезд ушёл: кит входил ~{ref_price:.3f}, сейчас {price:.3f})"

    balance = get_usdc_balance()
    if balance < 1.0:
        return f"⏭ Пропуск (баланс ${balance:.2f} < $1.00)"
    if daily_stop_active(balance):
        return "⛔ Дневной стоп-лосс — входы на паузе до завтра (UTC)"

    entry_usd = position_size_usd(whale_stats or [], price, balance)

    mode = "PAPER" if CONFIG.trading.paper_mode else "LIVE"
    if not place_bet(token_id, "BUY", entry_usd, price):
        return "❌ Ошибка ордера"

    tokens = max(entry_usd / price, CONFIG.trading.min_tokens) if price > 0 else CONFIG.trading.min_tokens
    positions[token_id] = {
        "market": signal["market"],
        "cond_id": cond_id,
        "outcome": bought_outcome,
        "signal_side": signal["side"],
        "entry_price": price,
        "size_usd": round(tokens * price, 2),
        "tokens": round(tokens, 4),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "close_at": (datetime.now(timezone.utc) + timedelta(hours=CONFIG.trading.position_hold_hours)).isoformat(),
    }
    save_positions(positions)
    return f"✅ {mode}: BUY {tokens:.1f} шеров «{bought_outcome}» @ {price:.3f} (${tokens * price:.2f})"


# ============================================================
#  Петля обратной связи: исходы сигналов → авточистка китов
# ============================================================

def _load_elite():
    return db.get_elite_addresses(
        min_winrate=CONFIG.engine.elite_min_winrate,
        min_pnl=CONFIG.engine.elite_min_pnl,
        lb_min_pnl=CONFIG.scout.lb_min_pnl,
    )


def next_poll_chunk(queue, tracked, n):
    """
    Round-robin опроса китов: возвращает (chunk, остаток очереди).
    Пустая очередь пополняется из актуального списка отслеживаемых.
    """
    if not queue:
        queue = sorted(tracked)
    return queue[:n], queue[n:]


def maybe_daily_report():
    """
    Ежедневный heartbeat в Telegram (после daily_report_hour_utc):
    подтверждает, что бот жив, даже если сигналов не было.
    """
    now = datetime.now(timezone.utc)
    if now.hour < CONFIG.monitor.daily_report_hour_utc:
        return
    m = _load_metrics()
    today = now.strftime("%Y-%m-%d")
    if m.get("last_report_day") == today:
        return
    m["last_report_day"] = today
    _save_metrics(m)

    tracked = db.get_tracked_addresses()
    elite = _load_elite()
    positions = load_positions()
    balance = get_usdc_balance()
    s = db.signal_outcome_summary()
    winshare = f"{s['wins']}/{s['resolved']}" if s["resolved"] else "—"
    mode = "PAPER" if CONFIG.trading.paper_mode else "LIVE"

    # Патч E: правота по стороне (BUY/SELL) — видно, не тащит ли SELL вниз.
    side_line = ""
    if s["resolved"]:
        by_side = db.signal_outcome_breakdown()["by_side"]
        parts = [f"{k} {v['wins']}/{v['resolved']}" for k, v in sorted(by_side.items())
                 if v["resolved"]]
        if parts:
            side_line = f"\nПо стороне: {' | '.join(parts)}"

    notifier.send(
        f"💓 <b>Бот жив</b> [{mode}]\n"
        f"Киты: {len(tracked)} (элитных {len(elite)})\n"
        f"Сигналов всего: {s['total']} | разрешено: {s['resolved']} | правота китов: {winshare}"
        f"{side_line}\n"
        f"Открытых позиций: {len(positions)}\n"
        f"{notifier.balance_line(balance, committed_value(positions))}"
    )
    notifier.flush()


def check_signal_outcomes():
    """
    Сверяет записанные сигналы с разрешением рынков (был ли кит прав)
    и удаляет китов со статистически убыточными сигналами.
    Возвращает список удалённых адресов.
    """
    unresolved = db.get_unresolved_outcomes()
    if not unresolved:
        return []
    resolved_n = 0
    for o in unresolved:
        if not o.get("cond_id"):
            continue
        winner = api.get_market_resolution(o["cond_id"])
        if winner is None:
            continue  # рынок ещё не разрешён
        bet = (o.get("outcome") or "").lower()
        # BUY → кит прав, если его исход выиграл; SELL → если исход проиграл
        won = (winner == bet) if o.get("side") == "BUY" else (winner != bet)
        db.mark_outcome_resolved(o["id"], winner, won)
        resolved_n += 1

    if not resolved_n:
        return []
    logger.info(f"Исходы сигналов: сверено {resolved_n} разрешённых рынков")
    removed = db.prune_bad_performers(
        min_signals=CONFIG.scout.prune_min_signals,
        min_winshare=CONFIG.scout.prune_min_winshare,
    )
    if removed:
        notifier.send(
            "🗑 <b>Авточистка китов</b> (сигналы статистически убыточны):\n"
            + "\n".join(f"<code>{a}</code>" for a in removed)
        )
    return removed


# ============================================================
#  Главный цикл
# ============================================================

def run():
    print("=" * 60)
    print(f"  Polymarket Bot v4 — Live Tracker ({'PAPER' if CONFIG.trading.paper_mode else 'LIVE'})")
    print("=" * 60)

    db.init_db()
    tracked = db.get_tracked_addresses()
    elite = _load_elite()
    if not tracked:
        logger.warning("В БД нет китов! Сначала запусти скаут: PYTHONPATH=. python3 -m src.scout")
    print(f"Отслеживаемых китов: {len(tracked)} (элитных: {len(elite)})")

    seen = OrderedDict()
    buffer = []
    positions = load_positions()
    last_whale_reload = time.time()
    last_outcome_check = 0.0
    poll_queue = []
    first_pass_left = set(tracked)  # кого ещё ни разу не опросили (backfill глубже)

    while True:
        try:
            manage_positions()
            positions = load_positions()

            # Перечитываем список китов раз в 10 минут (скаут мог обновить БД)
            if time.time() - last_whale_reload > 600:
                tracked = db.get_tracked_addresses()
                elite = _load_elite()
                last_whale_reload = time.time()

            # Сверка исходов сигналов + авточистка плохих китов
            if time.time() - last_outcome_check > CONFIG.engine.outcome_check_interval:
                last_outcome_check = time.time()
                if check_signal_outcomes():
                    tracked = db.get_tracked_addresses()
                    elite = _load_elite()

            maybe_daily_report()

            now_ts = datetime.now(timezone.utc).timestamp()
            cutoff = now_ts - CONFIG.monitor.signal_window

            # Персональный опрос китов (round-robin). Глобальная лента /trades
            # не годится: сделка в ней видна от имени ТЕЙКЕРА, и лимитные
            # ордера китов (мейкер-сторона) туда не попадают.
            chunk, poll_queue = next_poll_chunk(
                poll_queue, tracked, CONFIG.monitor.whales_per_cycle)
            trades = []
            for addr in chunk:
                limit = (CONFIG.monitor.first_cycle_limit
                         if addr in first_pass_left
                         else CONFIG.monitor.per_whale_limit)
                first_pass_left.discard(addr)
                trades.extend(api.get_trades(limit=limit, user=addr))
            if not trades:
                time.sleep(CONFIG.monitor.poll_interval)
                continue

            new_count = 0
            for t in trades:
                tx = t.get("transactionHash", "")
                outcome = t.get("outcome", "")
                if not tx:
                    continue
                key = f"{tx}_{outcome}"
                if key in seen:
                    continue
                seen[key] = True

                wallet = (t.get("proxyWallet") or "").lower()
                if wallet not in tracked:
                    continue  # Live Tracker следит только за известными китами

                price = float(t.get("price", 0))
                notional = api.usdc_notional(t)  # FIX: size (шеры) * price
                if notional < CONFIG.monitor.min_size_usdc:
                    continue

                ts_raw = int(t.get("timestamp", 0))
                if ts_raw == 0:
                    continue
                ts_sec = ts_raw / 1000 if ts_raw > 1e11 else ts_raw

                engine.observe(wallet, ts_sec)  # анти-MEV
                buffer.append({
                    "wallet": wallet, "ts": ts_sec, "notional": notional,
                    "market": t.get("title", ""), "cond_id": t.get("conditionId", ""),
                    "event_slug": t.get("eventSlug", ""), "side": t.get("side", ""),
                    "price": price, "outcome": t.get("outcome", ""),
                    "tx": tx,
                })
                new_count += 1

            # Обрезка кэшей
            while len(seen) > CONFIG.cache.max_seen_hashes:
                seen.popitem(last=False)
            buffer = [b for b in buffer if b["ts"] >= cutoff]
            if len(buffer) > CONFIG.cache.max_buffer_size:
                buffer = buffer[-CONFIG.cache.max_buffer_size:]

            # Группировка по рынку → консенсус через Filter Engine
            buckets = defaultdict(list)
            for b in buffer:
                buckets[b["cond_id"]].append(b)

            for cond_id, entries in buckets.items():
                if not cond_id:
                    continue
                if CONFIG.market_filter.should_skip(entries[0]["market"]):
                    continue

                signal = engine.evaluate_market(entries, now_ts, trusted=tracked, elite=elite)
                if not signal:
                    continue

                sig_key = f"{cond_id}_{signal['side']}"
                if is_duplicate(sig_key):
                    continue

                mark_sent(sig_key)

                # Стата кошельков сигнала для алерта
                side_wallets = {e["wallet"] for e in entries if e["side"] == signal["side"]}
                whale_stats = [db.get_whale(w) for w in side_wallets]
                whale_stats = [w for w in whale_stats if w]

                # Петля обратной связи: фиксируем сигнал для сверки с исходом рынка.
                # Пишем независимо от того, откроем ли позицию — оцениваем КИТА.
                # id из БД — персистентный номер сигнала, не сбрасывается при рестарте.
                signal_id = db.record_signal_outcome(signal, sorted(side_wallets),
                                                     entry_price=signal["median_price"])

                # tx_history (антидубль + история по схеме из требований)
                for e in entries:
                    if e["side"] == signal["side"]:
                        db.record_tx({
                            "tx_hash": e["tx"], "outcome": e["outcome"],
                            "address": e["wallet"], "condition_id": cond_id,
                            "market_title": e["market"], "event_slug": e["event_slug"],
                            "side": e["side"], "amount_usd": e["notional"],
                            "price": e["price"], "timestamp": int(e["ts"]),
                        })
                        db.touch_last_active(e["wallet"])

                trade_status = execute_trade(signal, positions, whale_stats)
                balance = get_usdc_balance()
                msg = notifier.format_signal(signal_id, signal, whale_stats, trade_status, balance,
                                              committed_value(positions))

                # В Telegram шлём только сигналы, по которым что-то произошло
                # (реальный вход, ошибка ордера, дневной стоп). Рутинные
                # «⏭ Пропуск …» по фильтрам в канал не идут — только в лог.
                # Сигнал всё равно записан в signal_outcomes для оценки кита.
                if trade_status.startswith("⏭ Пропуск"):
                    logger.info("СИГНАЛ (не отправлен, пропуск): " + " | ".join(
                        msg.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","").split("\n")))
                    print(f"\n{'='*50}\n[не отправлен в TG — пропуск]\n{msg}\n{'='*50}\n")
                    continue

                notifier.send(msg)
                notifier.flush()  # сигнал → немедленная доставка, не ждём батч-таймер
                logger.info(" | ".join(msg.replace("<b>","").replace("</b>","").replace("<i>","").replace("</i>","").split("\n")))
                print(f"\n{'='*50}\n{msg}\n{'='*50}\n")

            ts_label = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts_label}] Мониторинг… буфер={len(buffer)} позиций={len(positions)} новых=+{new_count}")

            notifier.maybe_flush()
            time.sleep(CONFIG.monitor.poll_interval)

        except Exception as e:
            print(f"Error: {e}")
            traceback.print_exc()
            time.sleep(60)


if __name__ == "__main__":
    run()
