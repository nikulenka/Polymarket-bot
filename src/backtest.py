import pandas as pd
import numpy as np
import os
import json
from datetime import datetime, timezone, timedelta

# --- Настройки Whale Backtest v2.1 ---
TRADES_FILE      = "data/trades_with_wallets.csv"
TOP_WALLETS_FILE = "data/top_wallets.csv"
WINDOW_SEC       = 43200 # 12 часов (окно сигнала)
COOLDOWN_SEC     = 86400 # 24 часа (кулдаун рынка)
OUTCOME_HORIZON  = 86400 # 24 часа (оценка исхода)
PRICE_STREAK     = 5
MIN_PRICE        = 0.05
MIN_WALLETS      = 2     # минимум 2 кита
WIN_THRESHOLD    = 0.02
STAKE            = 10.0
STOP_LOSS        = 5.0

SKIP_PATTERNS = [
    "NBA", "NFL", "NHL", "MLB", "soccer", "win the", 
    "beat the", "Series", "Finals", "Championship",
    "Buccaneers", "Lakers", "Spurs", "Hawks", "Knicks",
    "Celtics", "Warriors", "Nuggets", "Playoffs",
    "AM-", "PM-", "AM ET", "PM ET", ":00AM", ":00PM", "Up or Down -"
]

def backtest():
    if not os.path.exists(TRADES_FILE) or not os.path.exists(TOP_WALLETS_FILE):
        print("Необходимые файлы (trades или top_wallets) не найдены.")
        return

    print("🚀 Загрузка данных (Whale Strategy v2.1)...")
    df = pd.read_csv(TRADES_FILE)
    df = df.sort_values("timestamp").reset_index(drop=True)
    
    top_wallets_df = pd.read_csv(TOP_WALLETS_FILE)
    top_wallets = set(top_wallets_df["wallet"].str.lower().tolist())
    print(f"✓ Загружено {len(top_wallets)} китов ($500+)")

    # SPLIT 0.5 / 0.5
    split_idx = int(len(df) * 0.5)
    train_df = df.iloc[:split_idx].copy()
    test_df  = df.iloc[split_idx:].copy()
    
    # Диагностика
    test_start = datetime.fromtimestamp(test_df["timestamp"].min(), tz=timezone.utc)
    test_end = datetime.fromtimestamp(test_df["timestamp"].max(), tz=timezone.utc)
    test_duration_days = (test_df["timestamp"].max() - test_df["timestamp"].min()) / 86400
    
    print(f"Период теста: {test_start.strftime('%Y-%m-%d')} -> {test_end.strftime('%Y-%m-%d')} ({test_duration_days:.1f} дней)")

    # Сделки китов в тесте
    test_tops_all = test_df[test_df["wallet"].str.lower().isin(top_wallets)].copy()
    
    if test_tops_all.empty:
        print("❌ В тестовом периоде нет сделок выбранных китов.")
        return

    # Симуляция
    signals = []
    sent_signals = {}
    test_by_market = {m: d.sort_values("timestamp") for m, d in test_df.groupby("market_id")}
    
    print(f"⏳ Симуляция (WINDOW=12h, HORIZON=24h)...")
    
    for i, row in test_tops_all.iterrows():
        market_id = row["market_id"]
        market_name = str(row["market_name"])
        ts = row["timestamp"]
        entry_price = row["price"]
        
        # Фильтры
        if entry_price < MIN_PRICE or entry_price > 0.95: continue
        if any(p in market_name for p in SKIP_PATTERNS): continue
        if market_id in sent_signals and ts - sent_signals[market_id] < COOLDOWN_SEC: continue
        
        window_start = ts - WINDOW_SEC
        window_trades = test_tops_all[(test_tops_all["market_id"] == market_id) & 
                                      (test_tops_all["timestamp"] >= window_start) & 
                                      (test_tops_all["timestamp"] <= ts)]
        
        unique_wallets = window_trades["wallet"].unique()
        buy_w = window_trades[window_trades["side"] == "BUY"]["wallet"].nunique()
        sell_w = window_trades[window_trades["side"] == "SELL"]["wallet"].nunique()
        
        side = None
        if buy_w >= MIN_WALLETS and buy_w >= sell_w * 2: side = "BUY"
        elif sell_w >= MIN_WALLETS and sell_w >= buy_w * 2: side = "SELL"
            
        if side:
            sent_signals[market_id] = ts
            market_data = test_by_market[market_id]
            outcome_ts = ts + OUTCOME_HORIZON
            
            # Оценка через 24 часа
            after_horizon = market_data[market_data["timestamp"] >= outcome_ts].head(PRICE_STREAK)
            exit_price = after_horizon["price"].mean() if len(after_horizon) >= 3 else None
            
            result = "UNCLEAR"
            pnl = 0.0
            
            if exit_price is not None:
                if side == "BUY":
                    change = exit_price - entry_price
                    if change >= WIN_THRESHOLD: 
                        result = "WIN"; pnl = STAKE * (change / entry_price)
                    else: 
                        result = "LOSS"; pnl = -STOP_LOSS
                else: # SELL
                    change = entry_price - exit_price
                    if change >= WIN_THRESHOLD: 
                        result = "WIN"; pnl = STAKE * (change / entry_price)
                    else: 
                        result = "LOSS"; pnl = -STOP_LOSS

            signals.append({
                "market": market_name,
                "side": side,
                "entry": entry_price,
                "exit": exit_price,
                "result": result,
                "pnl": pnl,
                "wallets_count": len(unique_wallets),
                "total_volume": window_trades["size_usdc"].sum()
            })

    # Отчет
    if not signals:
        print("\n❌ Сигналов от китов не обнаружено.")
        return

    sig_df = pd.DataFrame(signals)
    
    print("\n--- Детализация сигналов Whale v2.1 ---")
    header = f"{'Market':<25} | {'Entry':>6} | {'W':>2} | {'Vol':>7} | {'Exit':>6} | {'Delta':>7} | {'Res'}"
    print(header)
    print("-" * len(header))
    for _, s in sig_df.head(25).iterrows():
        entry = s["entry"]
        exit_p = s["exit"]
        delta = (exit_p - entry) if exit_p is not None else 0
        res = s["result"]
        vol = s["total_volume"]
        w_cnt = s["wallets_count"]
        m_name = s["market"][:25]
        e_str = f"{entry:.3f}"
        x_str = f"{exit_p:.3f}" if exit_p is not None else "N/A"
        d_str = f"{delta:+.3f}" if exit_p is not None else "N/A"
        print(f"{m_name:<25} | {e_str:>6} | {w_cnt:>2} | {vol:>7.0f}$ | {x_str:>6} | {d_str:>7} | {res}")

    wins = len(sig_df[sig_df["result"] == "WIN"])
    losses = len(sig_df[sig_df["result"] == "LOSS"])
    unclear = len(sig_df[sig_df["result"] == "UNCLEAR"])
    total_pnl = sig_df["pnl"].sum()
    win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
    
    print("\n" + "="*50)
    print(" РЕЗУЛЬТАТЫ WHALE BACKTEST v2.1")
    print("="*50)
    print(f"Всего сигналов:   {len(sig_df)}")
    print(f"WIN / LOSS / UNCLEAR: {wins} / {losses} / {unclear}")
    print(f"Win Rate:         {win_rate:.1f}%")
    print(f"Итоговый P&L:     ${total_pnl:.2f}")
    print("-" * 50)

    market_pnl = sig_df.groupby("market")["pnl"].sum().sort_values(ascending=False)
    print("\nТоп рынки по P&L:")
    for m, p in market_pnl.head(3).items(): print(f"  + ${p:>6.2f} | {m[:50]}")
    for m, p in market_pnl.tail(3).items(): print(f"  - ${abs(p):>6.2f} | {m[:50]}")

    print("\nВЫВОД:")
    if total_pnl > 0 and win_rate > 50:
        print("✅ WHALE v2.1: СТРАТЕГИЯ ПОКАЗЫВАЕТ ПРОФИТ")
    else:
        print("❌ WHALE v2.1: ТРЕБУЕТСЯ ДОПОЛНИТЕЛЬНАЯ ФИЛЬТРАЦИЯ")
    print("="*50)

if __name__ == "__main__":
    backtest()
