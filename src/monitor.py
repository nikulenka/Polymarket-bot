import httpx
import pandas as pd
import os
import time
import logging
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# --- Конфиг ---
DATA_API       = "https://data-api.polymarket.com"
TOP_WALLETS    = "data/top_wallets.csv"
SIGNALS_FILE   = "data/sent_signals.json"
LOG_DIR        = "logs"
LOG_FILE       = "logs/signals.log"
POLL_INTERVAL  = 30    # секунды между проверками
SIGNAL_WINDOW  = 1800  # 30 минут в секундах
MIN_WALLETS    = 2     # минимум топ-кошельков для сигнала
MIN_SIZE_USDC  = 2.0   # минимальный размер сделки

SKIP_PATTERNS = [
    "AM-", "PM-",        # Bitcoin 5-min рынки
    "AM ET", "PM ET",
    ":00AM", ":00PM",
    "Up or Down -"
]

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from dotenv import load_dotenv
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOCKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# --- Помощники для антидубликата ---
def load_sent():
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE) as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_sent(sent):
    with open(SIGNALS_FILE, "w") as f:
        json.dump(sent, f)

def is_duplicate(sig_key_str, cooldown_hours=2):
    sent = load_sent()
    if sig_key_str in sent:
        try:
            last = datetime.fromisoformat(sent[sig_key_str])
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last < timedelta(hours=cooldown_hours):
                return True
        except:
            return False
    return False

def mark_sent(sig_key_str):
    sent = load_sent()
    sent[sig_key_str] = datetime.now(timezone.utc).isoformat()
    save_sent(sent)

# --- Остальные функции ---
def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print(f"  [!] Ошибка отправки в Telegram: {e}")

def validate_signal_with_claude(sig, market_name):
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "твой_ключ_сюда":
        return {"decision": "TRADE", "confidence": 100, "reason": "No API key"}

    prompt = f"""
Проанализируй торговый сигнал на Polymarket.
Рынок: {market_name}
Сделка: {sig['n_wallets']} умных кошельков поставили на {sig['side']} объемом ${sig['total_usdc']:.2f}.
Твоя задача — оценить логику сделки. Имеет ли это смысл? Не является ли это ошибкой?

Верни строго JSON:
{{
  "decision": "TRADE", 
  "confidence": 80, 
  "reason": "одно предложение почему"
}}
"""
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "polymarket-bot"
            },
            json={
                "model": "anthropic/claude-3.5-haiku",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "max_tokens": 200
            },
            timeout=10.0
        )
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            import re
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group(0))
        else:
            print(f"  [!] Ошибка HTTP {response.status_code}: {response.text}")
    except Exception as e:
        print(f"  [!] Ошибка OpenRouter: {e}")
    
    return {"decision": "SKIP", "confidence": 0, "reason": "Ошибка валидации"}

def load_top_wallets():
    if not os.path.exists(TOP_WALLETS):
        raise FileNotFoundError(f"Файл {TOP_WALLETS} не найден. Запустите rank_wallets.py")
    df = pd.read_csv(TOP_WALLETS)
    wallets = set(df["wallet"].str.lower())
    return wallets

def fetch_recent_trades():
    try:
        resp = httpx.get(
            f"{DATA_API}/trades",
            params={"limit": 500},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        pass
    return []

def run():
    print("=" * 60)
    print("  Polymarket Bot — Monitor запущен (FOREVER MODE)")
    print(f"  Опрос каждые {POLL_INTERVAL}с | Окно сигнала {SIGNAL_WINDOW//60} мин")
    print("=" * 60)

    top_wallets   = load_top_wallets()
    print(f"✓ Загружено топ-кошельков: {len(top_wallets)}")
    last_mod_time = os.path.getmtime(TOP_WALLETS) if os.path.exists(TOP_WALLETS) else 0

    seen_hashes    = set()
    rolling_buffer = []  
    total_signals  = 0

    while True:
        try:
            if os.path.exists(TOP_WALLETS):
                current_mod_time = os.path.getmtime(TOP_WALLETS)
                if current_mod_time > last_mod_time:
                    top_wallets = load_top_wallets()
                    last_mod_time = current_mod_time
            
            cycle_start = datetime.now(timezone.utc)
            ts_label    = cycle_start.strftime("%H:%M:%S")
            now_ts      = cycle_start.timestamp()
            cutoff      = now_ts - SIGNAL_WINDOW

            trades = fetch_recent_trades()
            
            for t in trades:
                tx = t.get("transactionHash", "")
                if tx in seen_hashes:
                    continue
                seen_hashes.add(tx)
                
                ts        = int(t.get("timestamp", 0))
                wallet    = (t.get("proxyWallet") or "").lower()
                size_u    = float(t.get("size", 0))
                price     = float(t.get("price", 0))
                size_usdc = size_u * price
                cond_id   = t.get("conditionId", "")
                side      = t.get("side", "")
                market    = t.get("title", cond_id)
                
                if wallet in top_wallets and size_usdc >= MIN_SIZE_USDC:
                    rolling_buffer.append({
                        "wallet": wallet, "ts": ts, "size": size_usdc,
                        "market": market, "cond_id": cond_id, "side": side, "tx": tx
                    })

            rolling_buffer = [b for b in rolling_buffer if b["ts"] >= cutoff]
            
            market_buckets = defaultdict(list)
            for b in rolling_buffer:
                market_buckets[b["cond_id"]].append(b)
                
            unique_wallets_in_buffer = set(b["wallet"] for b in rolling_buffer)

            for cond_id, entries in market_buckets.items():
                market_name = entries[0]["market"]
                
                if any(p in market_name for p in SKIP_PATTERNS):
                    continue
                
                buys  = [e for e in entries if e["side"] == "BUY"]
                sells = [e for e in entries if e["side"] == "SELL"]
                
                buy_w  = {e["wallet"] for e in buys}
                sell_w = {e["wallet"] for e in sells}
                
                buy_count  = len(buy_w)
                sell_count = len(sell_w)
                
                side = None
                target_entries = []
                if buy_count >= sell_count * 2 and buy_count >= MIN_WALLETS:
                    side = "BUY"
                    target_entries = buys
                    unique_w = buy_w
                elif sell_count >= buy_count * 2 and sell_count >= MIN_WALLETS:
                    side = "SELL"
                    target_entries = sells
                    unique_w = sell_w
                elif buy_count >= MIN_WALLETS or sell_count >= MIN_WALLETS:
                    # print(f"⏭ КОНФЛИКТ пропущен: {market_name} (BUY:{buy_count} SELL:{sell_count})")
                    continue
                
                if side:
                    sig_key_str = f"{cond_id}_{side}"
                    
                    # ПРОВЕРКА КУЛДАУНА (ЧЕРЕЗ ФАЙЛ)
                    if is_duplicate(sig_key_str):
                        continue
                    
                    total_usdc  = sum(e["size"] for e in target_entries)
                    last_ts     = max(e["ts"] for e in target_entries)
                    ts_str      = datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    
                    validation = validate_signal_with_claude(
                        {"side": side, "n_wallets": len(unique_w), "total_usdc": total_usdc}, 
                        market_name
                    )
                    
                    decision = validation.get("decision", "SKIP")
                    conf = validation.get("confidence", 0)
                    reason = validation.get("reason", "No reason provided")
                    
                    logging.info(f"🤖 Claude: {decision} ({conf}%) — {reason}")
                    
                    if decision == "TRADE" and conf >= 60:
                        # ОТМЕТКА ОБ ОТПРАВКЕ (В ФАЙЛ)
                        mark_sent(sig_key_str)
                        
                        total_signals += 1
                        msg = (
                            f"🚨 <b>СИГНАЛ:</b> {market_name}\n\n"
                            f"<b>Сторона:</b> {side}\n"
                            f"<b>Кошельков:</b> {len(unique_w)}\n"
                            f"<b>Объём:</b> ${total_usdc:.2f}\n"
                            f"<b>Время:</b> {ts_str}\n\n"
                            f"✅ <b>Claude:</b> {reason} ({conf}%)"
                        )
                        logging.info(msg.replace("\n", " | ").strip())
                        send_telegram(msg)
                        print(f"\n🚨 СИГНАЛ: {market_name}\nСторона: {side}\nКошельков: {len(unique_w)}\nОбъём: ${total_usdc:.2f}\nВремя: {ts_str}\n✅ Claude: {reason} ({conf}%)\n")
                    else:
                        skip_msg = f"⏭️ SKIP: {market_name} | {reason} (Confidence: {conf}%)"
                        logging.info(skip_msg)

            if len(seen_hashes) > 20_000:
                seen_hashes = set(list(seen_hashes)[-10_000:])
                
            print(f"[{ts_label}] Сделок в буфере: {len(rolling_buffer):>4} | Топ-кошельков: {len(unique_wallets_in_buffer):>2} | Сигналов: {total_signals}")
            
            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            print("\nБот остановлен вручную.")
            break
        except Exception as e:
            print(f"\n[ОШИБКА] {e} — перезапуск через 60 сек...")
            time.sleep(60)
            continue

if __name__ == "__main__":
    run()
