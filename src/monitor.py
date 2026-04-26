import httpx
import pandas as pd
import os
import time
import logging
import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from src.trader import place_bet, close_position

# --- Конфиг ---
DATA_API       = "https://data-api.polymarket.com"
GAMMA_API      = "https://gamma-api.polymarket.com"
TOP_WALLETS    = "data/top_wallets.csv"
SIGNALS_FILE   = "data/sent_signals.json"
POSITIONS_FILE = "data/open_positions.json"
LOG_DIR        = "logs"
LOG_FILE       = "logs/signals.log"
POLL_INTERVAL  = 30    
SIGNAL_WINDOW  = 43200  # 12 часов как в успешном бэктесте
MIN_WALLETS    = 2     
MIN_SIZE_USDC  = 50.0    # Порог для известных китов
WHALE_MIN_SIZE = 1000.0  # Любая сделка от $1000 = динамический кит

SKIP_PATTERNS = [
    "NBA", "NFL", "NHL", "MLB", "soccer", "win the", 
    "beat the", "Series", "Finals", "Championship",
    "Buccaneers", "Lakers", "Spurs", "Hawks", "Knicks",
    "Celtics", "Warriors", "Nuggets", "Playoffs",
    "AM-", "PM-", "AM ET", "PM ET", ":00AM", ":00PM", "Up or Down -"
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

# --- Помощники для позиций ---
def load_positions():
    if os.path.exists(POSITIONS_FILE):
        try:
            with open(POSITIONS_FILE) as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_positions(pos):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(pos, f, indent=2)

def get_market_tokens(condition_id):
    """Получает tokenIds для исходов YES и NO"""
    try:
        resp = httpx.get(f"{GAMMA_API}/markets", params={"condition_id": condition_id}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                m = data[0]
                tokens = m.get("clobTokenIds")
                if tokens:
                    # tokens — это список строк, обычно ["YES_ID", "NO_ID"]
                    # Нам нужно понять, какой из них какой. Обычно 0 - YES, 1 - NO.
                    outcomes = json.loads(m.get("outcomes", '["Yes", "No"]'))
                    return {outcomes[i]: tokens[i] for i in range(len(tokens))}
    except Exception as e:
        print(f"  [!] Ошибка получения токенов для {condition_id}: {e}")
    return None

# --- Помощники для антидубликата ---
def load_sent():
    if os.path.exists(SIGNALS_FILE):
        try:
            with open(SIGNALS_FILE) as f:
                return json.load(f)
        except: return {}
    return {}

def save_sent(sent):
    with open(SIGNALS_FILE, "w") as f:
        json.dump(sent, f)

def is_duplicate(sig_key_str, cooldown_hours=12):
    sent = load_sent()
    if sig_key_str in sent:
        try:
            last = datetime.fromisoformat(sent[sig_key_str])
            if last.tzinfo is None: last = last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last < timedelta(hours=cooldown_hours):
                return True
        except: return False
    return False

def mark_sent(sig_key_str):
    sent = load_sent()
    sent[sig_key_str] = datetime.now(timezone.utc).isoformat()
    save_sent(sent)

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=5)
    except: pass

def validate_signal_with_claude(sig, market_name):
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY == "твой_ключ_сюда":
        return {"decision": "TRADE", "confidence": 100, "reason": "No API key"}
    prompt = f"Проанализируй сигнал Polymarket: {market_name}. {sig['n_wallets']} китов на {sig['side']} объемом ${sig['total_usdc']:.0f}. Верни JSON: {{'decision': 'TRADE'/'SKIP', 'confidence': 0-100, 'reason': '...'}}"
    try:
        response = httpx.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": "anthropic/claude-3.5-haiku", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}},
            timeout=10.0
        )
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            import re
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match: return json.loads(match.group(0))
    except: pass
    return {"decision": "SKIP", "confidence": 0, "reason": "Error"}

def manage_positions():
    """Проверяет открытые позиции и закрывает их по времени или TP/SL"""
    positions = load_positions()
    if not positions: return
    
    now = datetime.now(timezone.utc)
    to_delete = []
    
    for token_id, p in positions.items():
        try:
            # 1. Проверка по времени
            close_at = datetime.fromisoformat(p["close_at"])
            if now > close_at:
                print(f"⏰ Время вышло: закрываем {p['market']}")
                close_position(token_id, p["tokens"], p["entry_price"]) # Цена тут для лога, trader.py ее использует
                to_delete.append(token_id)
                send_telegram(f"⏰ <b>ВРЕМЯ ВЫШЛО:</b> Позиция закрыта\n{p['market']}")
                continue
            
            # 2. TP/SL (нужна актуальная цена)
            # В реальном боте мы бы запрашивали цену через SDK/API
            # Для упрощения пока оставим только временной выход, если нет API для текущей цены YES/NO токена
        except Exception as e:
            print(f"Error managing pos {token_id}: {e}")
            
    if to_delete:
        for tid in to_delete: del positions[tid]
        save_positions(positions)

def run():
    print("=" * 60)
    print("  Polymarket Bot v3 — Whale Trader запущен")
    print("=" * 60)

    top_wallets = set(pd.read_csv(TOP_WALLETS)["wallet"].str.lower())
    seen_hashes = set()
    rolling_buffer = []  
    total_signals = 0

    while True:
        try:
            manage_positions() # Управление сделками
            
            cycle_start = datetime.now(timezone.utc)
            now_ts = cycle_start.timestamp()
            cutoff = now_ts - SIGNAL_WINDOW

            # На первом проходе берем больше данных, чтобы наполнить буфер
            limit = 5000 if not seen_hashes else 500
            try:
                resp = httpx.get(f"{DATA_API}/trades", params={"limit": limit}, timeout=15)
                trades = resp.json()
            except:
                print("⚠️ Ошибка сети при получении сделок"); time.sleep(10); continue
            
            for t in trades:
                tx = t.get("transactionHash", "")
                if tx in seen_hashes: continue
                seen_hashes.add(tx)
                
                wallet = (t.get("proxyWallet") or "").lower()
                price = float(t.get("price", 0))
                size_usdc = float(t.get("size", 0)) * price
                
                # Два критерия: известный кит ИЛИ крупная сделка от кого угодно
                is_known_whale = wallet in top_wallets and size_usdc >= MIN_SIZE_USDC
                is_big_trade = size_usdc >= WHALE_MIN_SIZE
                
                if is_known_whale or is_big_trade:
                    ts_raw = int(t.get("timestamp", 0))
                    ts_sec = ts_raw / 1000 if ts_raw > 1e11 else ts_raw
                    
                    rolling_buffer.append({
                        "wallet": wallet, "ts": ts_sec, "size": size_usdc,
                        "market": t.get("title", ""), "cond_id": t.get("conditionId", ""), 
                        "side": t.get("side", ""), "price": price, "outcome": t.get("outcome", "")
                    })

            rolling_buffer = [b for b in rolling_buffer if b["ts"] >= cutoff]
            market_buckets = defaultdict(list)
            for b in rolling_buffer: market_buckets[b["cond_id"]].append(b)

            for cond_id, entries in market_buckets.items():
                market_name = entries[0]["market"]
                if any(p in market_name for p in SKIP_PATTERNS): continue
                
                buy_w = {e["wallet"] for e in entries if e["side"] == "BUY"}
                sell_w = {e["wallet"] for e in entries if e["side"] == "SELL"}
                
                side = None
                if len(buy_w) >= len(sell_w) * 2 and len(buy_w) >= MIN_WALLETS: side = "BUY"
                elif len(sell_w) >= len(buy_w) * 2 and len(sell_w) >= MIN_WALLETS: side = "SELL"
                
                if side:
                    sig_key = f"{cond_id}_{side}"
                    if is_duplicate(sig_key): continue
                    
                    val = validate_signal_with_claude({"side": side, "n_wallets": max(len(buy_w), len(sell_w)), "total_usdc": sum(e["size"] for e in entries)}, market_name)
                    
                    if val.get("decision") == "TRADE" and val.get("confidence", 0) >= 80:
                        mark_sent(sig_key)
                        
                        # --- АВТО-ТРЕЙДИНГ ---
                        tokens_map = get_market_tokens(cond_id)
                        token_id = None
                        if tokens_map:
                            # Ищем токен для нужного исхода
                            # Если side BUY, ищем Yes (или что там в Entries)
                            target_outcome = entries[0]["outcome"] # Берем исход из сделки кита
                            token_id = tokens_map.get(target_outcome)
                        
                        trade_status = "Пропущен (Нет TokenID)"
                        if token_id:
                            print(f"💰 Открываем сделку на $2: {market_name}")
                            res = place_bet(token_id, "BUY", 2.0, entries[0]["price"])
                            if res:
                                trade_status = "✅ СДЕЛКА ОТКРЫТА"
                                positions = load_positions()
                                positions[token_id] = {
                                    "market": market_name, "side": "BUY", "entry_price": entries[0]["price"],
                                    "size_usd": 2.0, "tokens": 2.0 / entries[0]["price"],
                                    "opened_at": datetime.now(timezone.utc).isoformat(),
                                    "close_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                                }
                                save_positions(positions)
                        
                        msg = (f"🚨 <b>СИГНАЛ:</b> {market_name}\n"
                               f"<b>{trade_status}</b>\n"
                               f"Сторона: {side} | Цена: {entries[0]['price']}\n"
                               f"✅ Claude: {val.get('reason')} ({val.get('confidence')}%)")
                        send_telegram(msg)
                        print(f"\n{msg}\n")

            # Вывод "пульса" в лог
            ts_label = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{ts_label}] Мониторинг... Сделок в буфере: {len(rolling_buffer)} | Активных позиций: {len(load_positions())}")
            
            if len(seen_hashes) > 20_000: seen_hashes = set(list(seen_hashes)[-10_000:])
            time.sleep(POLL_INTERVAL)
        except Exception as e:
            print(f"Error: {e}"); time.sleep(60)

if __name__ == "__main__":
    run()
