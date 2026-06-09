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


def resolve_token_id(tokens_map, target_outcome, signal_side):
    """
    BUY-сигнал → покупаем токен target_outcome.
    SELL-сигнал → покупаем ПРОТИВОПОЛОЖНЫЙ (шортить на Polymarket нельзя).
    """
    if not tokens_map or not target_outcome:
        return None
    target = target_outcome.lower()
    if signal_side == "BUY":
        return tokens_map.get(target) or tokens_map.get("yes")
    # SELL → противоположный
    opposite = {k: v for k, v in tokens_map.items() if k != target}
    if "no" in opposite and target == "yes":
        return opposite["no"]
    if "yes" in opposite and target == "no":
        return opposite["yes"]
    if len(opposite) == 1:
        return next(iter(opposite.values()))
    return tokens_map.get("no")


def manage_positions():
    """TP / SL / выход по времени."""
    positions = load_positions()
    if not positions:
        return
    now = datetime.now(timezone.utc)
    to_delete = []

    for token_id, p in positions.items():
        try:
            close_at = datetime.fromisoformat(p["close_at"])
            if close_at.tzinfo is None:
                close_at = close_at.replace(tzinfo=timezone.utc)
            cur = api.get_price(token_id)
            entry = p.get("entry_price", 0)

            if cur is not None and entry > 0:
                change = (cur - entry) / entry
                if change >= CONFIG.trading.take_profit_pct:
                    if close_position(token_id, p["tokens"], cur):
                        to_delete.append(token_id)
                        notifier.send(f"✅ <b>TAKE PROFIT +{CONFIG.trading.take_profit_pct*100:.0f}%</b>: {p['market']} @ {cur:.3f}")
                    continue
                if change <= CONFIG.trading.stop_loss_pct:
                    if close_position(token_id, p["tokens"], cur):
                        to_delete.append(token_id)
                        notifier.send(f"🛑 <b>STOP LOSS {CONFIG.trading.stop_loss_pct*100:.0f}%</b>: {p['market']} @ {cur:.3f}")
                    continue

            if now > close_at:
                exit_price = cur if cur is not None else entry
                if close_position(token_id, p["tokens"], exit_price):
                    to_delete.append(token_id)
                    notifier.send(f"⏰ <b>ВРЕМЯ ВЫШЛО</b>: {p['market']} @ {exit_price:.3f}")
        except Exception as e:
            logger.error(f"manage_positions {token_id}: {e}")

    if to_delete:
        for tid in to_delete:
            positions.pop(tid, None)
        save_positions(positions)


# ============================================================
#  Исполнение сделки по сигналу (paper/live)
# ============================================================

def execute_trade(signal, positions):
    """Пробует открыть позицию по сигналу. Возвращает строку-статус."""
    cond_id = signal["cond_id"]
    price = signal["median_price"]

    if price > CONFIG.trading.max_price:
        return f"⏭ Пропуск (цена {price:.3f} > {CONFIG.trading.max_price})"
    if not (0.01 <= price < 1):
        return "⏭ Пропуск (цена вне диапазона)"

    tokens_map = api.get_market_tokens(cond_id)
    if tokens_map == "CLOSED":
        return "⏭ Пропуск (рынок закрыт)"
    if not tokens_map:
        return "⏭ Пропуск (нет TokenID)"

    token_id = resolve_token_id(tokens_map, signal["consensus_outcome"], signal["side"])
    if not token_id:
        return "⏭ Пропуск (не нашли токен)"

    balance = get_usdc_balance()
    if balance < 1.0:
        return f"⏭ Пропуск (баланс ${balance:.2f} < $1.00)"

    mode = "PAPER" if CONFIG.trading.paper_mode else "LIVE"
    if not place_bet(token_id, "BUY", CONFIG.trading.trade_amount_usd, price):
        return "❌ Ошибка ордера"

    entry_usd = CONFIG.trading.trade_amount_usd
    tokens = max(entry_usd / price, CONFIG.trading.min_tokens) if price > 0 else CONFIG.trading.min_tokens
    positions[token_id] = {
        "market": signal["market"],
        "signal_side": signal["side"],
        "entry_price": price,
        "size_usd": round(tokens * price, 2),
        "tokens": round(tokens, 4),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "close_at": (datetime.now(timezone.utc) + timedelta(hours=CONFIG.trading.position_hold_hours)).isoformat(),
    }
    save_positions(positions)
    return f"✅ {mode}: BUY {tokens:.1f} шеров @ {price:.3f}"


# ============================================================
#  Главный цикл
# ============================================================

def run():
    print("=" * 60)
    print(f"  Polymarket Bot v4 — Live Tracker ({'PAPER' if CONFIG.trading.paper_mode else 'LIVE'})")
    print("=" * 60)

    db.init_db()
    tracked = db.get_tracked_addresses()
    if not tracked:
        logger.warning("В БД нет китов! Сначала запусти скаут: PYTHONPATH=. python3 -m src.scout")
    print(f"Отслеживаемых китов: {len(tracked)}")

    seen = OrderedDict()
    buffer = []
    total_signals = 0
    positions = load_positions()
    last_whale_reload = time.time()

    while True:
        try:
            manage_positions()
            positions = load_positions()

            # Перечитываем список китов раз в 10 минут (скаут мог обновить БД)
            if time.time() - last_whale_reload > 600:
                tracked = db.get_tracked_addresses()
                last_whale_reload = time.time()

            now_ts = datetime.now(timezone.utc).timestamp()
            cutoff = now_ts - CONFIG.monitor.signal_window
            limit = 5000 if not seen else 500

            trades = api.get_trades(limit=limit)
            if not trades:
                time.sleep(10)
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

                signal = engine.evaluate_market(entries, now_ts)
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

                trade_status = execute_trade(signal, positions)
                total_signals += 1
                msg = notifier.format_signal(total_signals, signal, whale_stats, trade_status)
                notifier.send(msg)
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
