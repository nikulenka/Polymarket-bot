import re
import traceback
import httpx
import pandas as pd
import os
import time
import logging
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict, OrderedDict
from statistics import median
from functools import lru_cache

from src.trader import place_bet, close_position, get_usdc_balance
from src.config import CONFIG, load_config
from src.cache import TelegramBatcher, PriceCacheManager, send_telegram_batched
from src.logger import setup_logging

# === QUICK WIN OPTIMIZATION: Используем централизованную конфигурацию ===
CONFIG = load_config()

# Создаем структуру папок
os.makedirs(CONFIG.files.log_dir, exist_ok=True)
os.makedirs("data", exist_ok=True)

# Инициализируем логирование (JSON формат)
logger = setup_logging(
    log_file=CONFIG.files.signals_file.replace(".json", ".log"),
    json_format=False  # Текстовый для удобства в консоли, JSON в файл будет добавлен позже
)

# === QUICK WIN #4: Инициализируем Telegram Batcher вместо send_telegram() ===
telegram_batcher = None
if CONFIG.telegram.enabled and CONFIG.telegram.token and CONFIG.telegram.chat_id:
    telegram_batcher = TelegramBatcher(
        token=CONFIG.telegram.token,
        chat_id=CONFIG.telegram.chat_id,
        batch_interval_sec=CONFIG.telegram.batch_interval_sec,
        max_batch_size=CONFIG.telegram.max_batch_size,
        timeout=CONFIG.timeout.telegram_timeout
    )

# === QUICK WIN #3: Инициализируем Price Cache Manager ===
price_cache = PriceCacheManager(ttl_seconds=CONFIG.cache.price_cache_ttl_sec)

SENT_SIGNALS_CACHE = None

def init_sent_cache():
    global SENT_SIGNALS_CACHE
    SENT_SIGNALS_CACHE = load_sent()


# ============================================================
#  Утилиты: файлы, позиции, антидубликат
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

def load_sent():
    if os.path.exists(CONFIG.files.signals_file):
        try:
            with open(CONFIG.files.signals_file) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_sent(sent):
    with open(CONFIG.files.signals_file, "w") as f:
        json.dump(sent, f)

def is_duplicate(sig_key_str, cooldown_hours=12):
    global SENT_SIGNALS_CACHE
    if SENT_SIGNALS_CACHE is None:
        init_sent_cache()
    sent = SENT_SIGNALS_CACHE
    if sig_key_str in sent:
        try:
            last = datetime.fromisoformat(sent[sig_key_str])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last < timedelta(hours=cooldown_hours):
                return True
        except Exception:
            return False
    return False

def mark_sent(sig_key_str):
    """Записывает сигнал и чистит записи старше SIGNALS_TTL_H."""
    global SENT_SIGNALS_CACHE
    if SENT_SIGNALS_CACHE is None:
        init_sent_cache()
    sent = SENT_SIGNALS_CACHE
    sent[sig_key_str] = datetime.now(timezone.utc).isoformat()
    # Удаляем протухшие записи
    now = datetime.now(timezone.utc)
    cleaned = {}
    for k, v in sent.items():
        try:
            ts = datetime.fromisoformat(v)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if now - ts < timedelta(hours=CONFIG.files.signals_ttl_hours):
                cleaned[k] = v
        except Exception:
            pass  # битая запись — выбрасываем
    save_sent(cleaned)
    SENT_SIGNALS_CACHE = cleaned


# ============================================================
#  Утилиты: Telegram, Claude, API
# ============================================================

def send_telegram(text):
    """QUICK WIN #4: Отправляет сообщение в батч Telegram вместо отправки сразу"""
    if telegram_batcher:
        telegram_batcher.add_message(text)
    else:
        logger.warning(f"Telegram batcher не инициализирован, пропускаем сообщение")

def validate_signal_with_claude(sig, market_name):
    """Валидация сигнала через LLM."""
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if not openrouter_key or openrouter_key == "твой_ключ_сюда":
        if CONFIG.validation.fallback_to_heuristics:
            # QUICK WIN: Fallback на локальные правила когда нет API ключа
            confidence = 85 if sig.get("n_wallets", 0) >= 5 else 70
            return {"decision": "TRADE", "confidence": confidence, "reason": "Local heuristics (no API)"}
        return {"decision": "SKIP", "confidence": 0, "reason": "No LLM API key"}
    
    prompt = (
        f"Проанализируй сигнал Polymarket: {market_name}. "
        f"{sig['n_wallets']} китов на {sig['side']} объемом ${sig['total_usdc']:.0f}. "
        f"Верни JSON: {{\"decision\": \"TRADE\"/\"SKIP\", \"confidence\": 0-100, \"reason\": \"...\"}}"
    )
    try:
        response = httpx.post(
            CONFIG.api.openrouter_api,
            headers={
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": CONFIG.validation.claude_model,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=CONFIG.validation.claude_timeout,
        )
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        else:
            logger.error(f"OpenRouter error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Claude validation error: {e}")
    
    # Fallback на локальные правила при ошибке LLM
    if CONFIG.validation.fallback_to_heuristics:
        confidence = 75 if sig.get("n_wallets", 0) >= 3 else 50
        return {"decision": "TRADE", "confidence": confidence, "reason": "Local heuristics (LLM error)"}
    
    return {"decision": "SKIP", "confidence": 0, "reason": "LLM error"}

def fetch_trades(limit, timeout=15):
    """Запрос сделок с retry и проверкой ответа."""
    for attempt in range(3):
        try:
            resp = httpx.get(
                f"{CONFIG.api.data_api}/trades",
                params={"limit": limit},
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                logger.warning(f"API вернул неожиданный формат: {type(data)}")
                return []
            elif resp.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(f"Rate limit (429), ждём {wait}с...")
                time.sleep(wait)
            else:
                logger.warning(f"API вернул {resp.status_code}")
                return []
        except Exception as e:
            logger.warning(f"Ошибка сети (попытка {attempt+1}/3): {e}")
            time.sleep(3)
    return []


# ============================================================
#  Утилиты: маркет-данные
# ============================================================

def get_market_tokens(condition_id):
    """Получает tokenIds для исходов через CLOB API. Возвращает dict или 'CLOSED'."""
    try:
        resp = httpx.get(
            f"{CONFIG.api.clob_api}/markets/{condition_id}",
            timeout=CONFIG.timeout.market_tokens_timeout
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("closed"):
                return "CLOSED"
            
            tokens = data.get("tokens", [])
            if tokens:
                return {t.get("outcome", "").lower(): t.get("token_id") for t in tokens}
            
            # Fallback на Gamma API если в CLOB нет токенов (редко)
            logger.debug(f"В CLOB API нет токенов для {condition_id}, пробуем Gamma...")
        
        # Резервный вариант через Gamma API
        resp = httpx.get(
            f"{CONFIG.api.gamma_api}/markets",
            params={"condition_ids": condition_id},
            timeout=CONFIG.timeout.market_tokens_timeout
        )
        if resp.status_code == 200:
            data = resp.json()
            if data:
                m = data[0]
                tokens_raw = m.get("clobTokenIds")
                if tokens_raw:
                    tokens = json.loads(tokens_raw)
                    outcomes = json.loads(m.get("outcomes", '["Yes", "No"]'))
                    return {outcomes[i].lower(): tokens[i] for i in range(len(tokens))}
    except Exception as e:
        logger.error(f"Ошибка получения токенов для {condition_id}: {e}")
    return None

def get_current_price(token_id):
    """QUICK WIN #3: Запрашивает цену с кэшированием (TTL 30 сек)."""
    def _fetch_price(token_id):
        try:
            resp = httpx.get(
                f"{CONFIG.api.clob_api}/price",
                params={"token_id": token_id, "side": "sell"},
                timeout=CONFIG.timeout.price_timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("price", 0))
        except Exception:
            pass
        return None
    
    return price_cache.get_price(token_id, _fetch_price)

def get_consensus_outcome(entries, side):
    """Определяет самый популярный outcome среди сделок с данной стороной."""
    outcome_counts = defaultdict(int)
    for e in entries:
        if e["side"] == side:
            outcome_counts[e["outcome"]] += 1
    if not outcome_counts:
        return None
    return max(outcome_counts, key=outcome_counts.get)

def get_median_price(entries, side):
    """Медианная цена из сделок нужной стороны (NEW-2)."""
    prices = [e["price"] for e in entries if e["side"] == side and e["price"] > 0]
    if not prices:
        # Fallback: любые цены
        prices = [e["price"] for e in entries if e["price"] > 0]
    return median(prices) if prices else 0.5

def resolve_token_id(tokens_map, target_outcome, signal_side):
    """
    Находит token_id для торговли (NEW-1 + NEW-4).
    
    Логика:
    - BUY-сигнал → покупаем токен target_outcome
    - SELL-сигнал → покупаем ПРОТИВОПОЛОЖНЫЙ токен (ставка против)
    
    На Polymarket нельзя шортить. SELL-сигнал означает "киты продают",
    значит мы должны купить NO-токен (или противоположный outcome).
    """
    if not tokens_map or not target_outcome:
        return None, "BUY"
    
    target_lower = target_outcome.lower()
    
    if signal_side == "BUY":
        # Прямая покупка: ищем токен этого outcome
        token_id = tokens_map.get(target_lower)
        if token_id:
            return token_id, "BUY"
        # NEW-4 fallback: BUY → "yes"
        token_id = tokens_map.get("yes")
        return token_id, "BUY"
    
    else:  # signal_side == "SELL"
        # NEW-1: Покупаем ПРОТИВОПОЛОЖНЫЙ outcome
        # Если киты продают "Yes", мы покупаем "No"
        opposite_tokens = {k: v for k, v in tokens_map.items() if k != target_lower}
        if opposite_tokens:
            if "no" in opposite_tokens and target_lower == "yes":
                return opposite_tokens["no"], "BUY"
            elif "yes" in opposite_tokens and target_lower == "no":
                return opposite_tokens["yes"], "BUY"
            
            if len(opposite_tokens) == 1:
                opp_outcome = next(iter(opposite_tokens))
                return opposite_tokens[opp_outcome], "BUY"
            return None, "BUY"
        # fallback: покупаем "no"
        token_id = tokens_map.get("no")
        return token_id, "BUY"


# ============================================================
#  Управление позициями
# ============================================================

def manage_positions():
    """Проверяет открытые позиции и закрывает по времени."""
    positions = load_positions()
    if not positions:
        return
    
    now = datetime.now(timezone.utc)
    to_delete = []
    
    for token_id, p in positions.items():
        try:
            close_at = datetime.fromisoformat(p["close_at"])
            # NEW-5: гарантируем timezone-aware
            if close_at.tzinfo is None:
                close_at = close_at.replace(tzinfo=timezone.utc)
            
            current_price = get_current_price(token_id)
            if current_price is not None:
                entry = p.get("entry_price", 0)
                if entry > 0:
                    change = (current_price - entry) / entry
                    if change >= CONFIG.trading.take_profit_pct:
                        print(f"✅ Take Profit +{CONFIG.trading.take_profit_pct*100:.0f}%: закрываем {p['market']}")
                        close_position(token_id, p["tokens"], current_price)
                        to_delete.append(token_id)
                        send_telegram(f"✅ <b>TAKE PROFIT +{CONFIG.trading.take_profit_pct*100:.0f}%:</b> {p['market']}\nЦена выхода: {current_price}")
                        continue
                    if change <= CONFIG.trading.stop_loss_pct:
                        print(f"🛑 Stop Loss {CONFIG.trading.stop_loss_pct*100:.0f}%: закрываем {p['market']}")
                        close_position(token_id, p["tokens"], current_price)
                        to_delete.append(token_id)
                        send_telegram(f"🛑 <b>STOP LOSS {CONFIG.trading.stop_loss_pct*100:.0f}%:</b> {p['market']}\nЦена выхода: {current_price}")
                        continue

            if now > close_at:
                if current_price is None:
                    current_price = p["entry_price"]
                    print(f"  ⚠️ Не удалось получить цену для {token_id}, используем entry_price")
                
                print(f"⏰ Время вышло: закрываем {p['market']} (цена: {current_price})")
                res = close_position(token_id, p["tokens"], current_price)
                if res:
                    to_delete.append(token_id)
                    send_telegram(
                        f"⏰ <b>ВРЕМЯ ВЫШЛО:</b> Позиция закрыта\n"
                        f"{p['market']}\nЦена выхода: {current_price}"
                    )
                else:
                    print(f"  ❌ Ошибка закрытия: {token_id} оставлен в позициях")
        except Exception as e:
            print(f"Error managing pos {token_id}: {e}")
            
    if to_delete:
        for tid in to_delete:
            del positions[tid]
        save_positions(positions)


# ============================================================
#  Главный цикл
# ============================================================

def run():
    print("=" * 60)
    print("  Polymarket Bot v3 — Whale Trader запущен (с QUICK WINS оптимизациями)")
    print("=" * 60)

    try:
        top_wallets = set(pd.read_csv(CONFIG.files.top_wallets_path)["wallet"].str.lower())
    except Exception as e:
        logger.warning(f"Ошибка загрузки китов ({CONFIG.files.top_wallets_path}): {e}. Ждем появления файла...")
        top_wallets = set()

    seen_hashes = OrderedDict()
    rolling_buffer = []  
    total_signals = 0
    # Кэш позиций в памяти
    cached_positions = load_positions()

    while True:
        try:
            manage_positions()
            cached_positions = load_positions()  # Обновляем после manage
            
            cycle_start = datetime.now(timezone.utc)
            now_ts = cycle_start.timestamp()
            cutoff = now_ts - CONFIG.monitor.signal_window

            limit = 5000 if not seen_hashes else 500
            trades = fetch_trades(limit)
            if not trades:
                time.sleep(10)
                continue
            
            new_count = 0
            for t in trades:
                tx = t.get("transactionHash", "")
                outcome = t.get("outcome", "")
                if not tx:
                    continue
                
                tx_key = f"{tx}_{outcome}"
                if tx_key in seen_hashes:
                    continue
                
                seen_hashes[tx_key] = True
                
                wallet = (t.get("proxyWallet") or "").lower()
                price = float(t.get("price", 0))
                size_usdc = float(t.get("size", 0))
                
                is_known_whale = wallet in top_wallets and size_usdc >= CONFIG.monitor.min_size_usdc
                is_big_trade = size_usdc >= CONFIG.monitor.whale_min_size
                
                if is_known_whale or is_big_trade:
                    ts_raw = int(t.get("timestamp", 0))
                    if ts_raw == 0:
                        continue
                    ts_sec = ts_raw / 1000 if ts_raw > 1e11 else ts_raw
                    
                    rolling_buffer.append({
                        "wallet": wallet, "ts": ts_sec, "size": size_usdc,
                        "market": t.get("title", ""), "cond_id": t.get("conditionId", ""), 
                        "side": t.get("side", ""), "price": price, "outcome": t.get("outcome", "")
                    })
                    new_count += 1

            # Обрезка seen_hashes (FIFO)
            while len(seen_hashes) > CONFIG.cache.max_seen_hashes:
                seen_hashes.popitem(last=False)

            # Обрезка буфера по времени + лимит
            rolling_buffer = [b for b in rolling_buffer if b["ts"] >= cutoff]
            if len(rolling_buffer) > CONFIG.cache.max_buffer_size:
                rolling_buffer = rolling_buffer[-CONFIG.cache.max_buffer_size:]

            # Группировка по рынку
            market_buckets = defaultdict(list)
            for b in rolling_buffer:
                market_buckets[b["cond_id"]].append(b)

            for cond_id, entries in market_buckets.items():
                if not cond_id:
                    continue
                
                market_name = entries[0]["market"]
                # QUICK WIN #2: Использует скомпилированный regex вместо O(N) цикла
                if CONFIG.market_filter.should_skip(market_name):
                    continue
                
                buy_w = {e["wallet"] for e in entries if e["side"] == "BUY"}
                sell_w = {e["wallet"] for e in entries if e["side"] == "SELL"}
                
                side = None
                if len(buy_w) >= len(sell_w) * 2 and len(buy_w) >= CONFIG.monitor.min_wallets:
                    side = "BUY"
                elif len(sell_w) >= len(buy_w) * 2 and len(sell_w) >= CONFIG.monitor.min_wallets:
                    side = "SELL"
                
                if not side:
                    continue

                sig_key = f"{cond_id}_{side}"
                if is_duplicate(sig_key):
                    continue
                
                n_wallets = len(buy_w) if side == "BUY" else len(sell_w)
                total_usdc = sum(e["size"] for e in entries if e["side"] == side)
                
                val = validate_signal_with_claude(
                    {"side": side, "n_wallets": n_wallets, "total_usdc": total_usdc},
                    market_name,
                )
                
                if val.get("decision") == "TRADE" and val.get("confidence", 0) >= 80:
                    mark_sent(sig_key)
                    
                    # --- АВТО-ТРЕЙДИНГ ---
                    tokens_map = get_market_tokens(cond_id)
                    target_outcome = get_consensus_outcome(entries, side)
                    
                    trade_status = "⏭ Пропущен (нет TokenID)"
                    token_id = None
                    order_side = "BUY"
                    
                    if tokens_map == "CLOSED":
                        trade_status = "⏭ Пропущен (Рынок закрыт)"
                    elif tokens_map:
                        # NEW-1: resolve_token_id учитывает SELL → покупка NO
                        token_id, order_side = resolve_token_id(tokens_map, target_outcome, side)
                    
                    # NEW-2: медианная цена из нужной стороны
                    trade_price = get_median_price(entries, side)
                    
                    if trade_price > CONFIG.trading.max_price:
                        trade_status = f"⏭ Пропущен (цена {trade_price:.4f} слишком высока)"
                    elif token_id and 0.01 <= trade_price < 1:
                        action_desc = f"{order_side} (сигнал: {side})"
                        
                        # ПРОВЕРКА БАЛАНСА ПЕРЕД ПОКУПКОЙ
                        balance = get_usdc_balance()
                        if balance < 1.0:
                            trade_status = f"⏭ Пропущен (баланс ${balance:.2f} < $1.00)"
                            print(f"  ⚠️ Недостаточно средств для торговли (баланс: ${balance:.2f}). Жду пополнения.")
                        else:
                            print(f"💰 Открываем сделку {action_desc} на ${CONFIG.trading.trade_amount_usd}: {market_name} (баланс: ${balance:.2f})")
                            res = place_bet(token_id, order_side, CONFIG.trading.trade_amount_usd, trade_price)
                            if res:
                                trade_status = f"✅ СДЕЛКА: {action_desc}"
                                # Динамический расчет как в trader.py
                                entry_usd = CONFIG.trading.trade_amount_usd
                                entry_tokens = entry_usd / trade_price if trade_price > 0 else 0
                                if entry_tokens < CONFIG.trading.min_tokens:
                                    entry_tokens = CONFIG.trading.min_tokens
                                    entry_usd = entry_tokens * trade_price
                                
                                cached_positions[token_id] = {
                                    "market": market_name,
                                    "side": order_side,
                                    "signal_side": side,
                                    "entry_price": trade_price,
                                    "size_usd": entry_usd,
                                    "tokens": entry_tokens,
                                    "opened_at": datetime.now(timezone.utc).isoformat(),
                                    "close_at": (datetime.now(timezone.utc) + timedelta(hours=CONFIG.trading.position_hold_hours)).isoformat(),
                                }
                                save_positions(cached_positions)
                            else:
                                trade_status = "❌ Ошибка ордера"
                    
                    total_signals += 1
                    msg = (
                        f"🚨 <b>СИГНАЛ #{total_signals}:</b> {market_name}\n"
                        f"<b>{trade_status}</b>\n"
                        f"Сигнал: {side} | Цена: {trade_price:.4f}\n"
                        f"Китов: {n_wallets} | Объём: ${total_usdc:.0f}\n"
                        f"✅ Claude: {val.get('reason')} ({val.get('confidence')}%)"
                    )
                    send_telegram(msg)
                    logging.info(msg.replace("<b>", "").replace("</b>", ""))
                    print(f"\n{'='*50}\n{msg}\n{'='*50}\n")

            # Пульс (NEW-8: используем кэш вместо чтения файла)
            ts_label = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(
                f"[{ts_label}] Мониторинг... "
                f"Сделок в буфере: {len(rolling_buffer)} | "
                f"Позиций: {len(cached_positions)} | "
                f"Новых: +{new_count}"
            )
            
            # QUICK WIN #4: Проверяем нужно ли отправить батч Telegram
            if telegram_batcher and telegram_batcher.should_flush():
                telegram_batcher.flush()
            
            time.sleep(CONFIG.monitor.poll_interval)

        except Exception as e:
            print(f"Error: {e}")
            traceback.print_exc()
            time.sleep(60)

if __name__ == "__main__":
    run()
