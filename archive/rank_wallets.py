import pandas as pd
import numpy as np
import os

def rank_wallets():
    csv_path = "data/trades_with_wallets.csv"
    if not os.path.exists(csv_path):
        print(f"Файл {csv_path} не найден. Запустите fetch_trades.py")
        return

    df = pd.read_csv(csv_path)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["size_usdc"] = pd.to_numeric(df["size_usdc"], errors="coerce")
    df = df.dropna(subset=["price", "size_usdc", "wallet"])
    
    # --- НОВЫЙ КРИТЕРИЙ 2: Исключение спорта ---
    sports_keywords = [
        "NBA", "NFL", "NHL", "MLB", "soccer", "win the", 
        "beat the", "Series", "Finals", "Championship",
        "Buccaneers", "Lakers", "Spurs", "Hawks", "Knicks",
        "Celtics", "Warriors", "Nuggets", "Playoffs"
    ]
    
    initial_len = len(df)
    for kw in sports_keywords:
        df = df[~df["market_name"].str.contains(kw, case=False, na=False)]
    
    print(f"Загружено сделок: {initial_len}")
    print(f"Сделок после удаления спорта: {len(df)}")

    # --- Агрегация по кошельку ---
    agg = df.groupby("wallet").agg(
        trade_count      = ("wallet",    "count"),
        avg_size_usdc    = ("size_usdc", "mean"),
        total_volume     = ("size_usdc", "sum"),
        market_diversity = ("market_id", "nunique"),
        conviction_score = ("price",     lambda x: (x - 0.5).abs().mean()),
    ).reset_index()

    # buy_ratio and directional_bias
    buy_counts = df[df["side"] == "BUY"].groupby("wallet").size().rename("buy_count")
    agg = agg.merge(buy_counts, on="wallet", how="left").fillna({"buy_count": 0})
    agg["buy_ratio"] = agg["buy_count"] / agg["trade_count"]
    agg["directional_bias"] = (agg["buy_ratio"] - 0.5).abs() * 2

    # --- НОВЫЙ КРИТЕРИЙ 1: avg_size_usdc >= 500 ---
    before_filter = len(agg)
    agg = agg[agg["avg_size_usdc"] >= 500]
    
    # Доп. фильтры от ботов (оставляем разумные границы)
    agg = agg[agg["trade_count"] >= 2]
    agg = agg[agg["market_diversity"] <= 30]
    
    after_filter = len(agg)

    if agg.empty:
        print("\n❌ Нет кошельков, прошедших фильтр (avg_size >= $500 + No Sports).")
        return

    # --- Score ---
    max_vol  = agg["total_volume"].max() if agg["total_volume"].max() > 0 else 1
    max_div  = agg["market_diversity"].max() if agg["market_diversity"].max() > 0 else 1

    agg["score"] = (
        (agg["total_volume"]     / max_vol) * 0.40 +
        (agg["market_diversity"] / max_div) * 0.20 +
        agg["conviction_score"]             * 0.20 +
        agg["directional_bias"]             * 0.20
    )

    agg = agg.sort_values("score", ascending=False).reset_index(drop=True)
    agg.index += 1

    # --- Сохраняем всех китов ---
    os.makedirs("data", exist_ok=True)
    agg.to_csv("data/top_wallets.csv", index=True, index_label="rank")
    print(f"✓ Все киты ({len(agg)}) сохранены в data/top_wallets.csv\n")

    # --- Красивая таблица топ-10 ---
    def shorten(addr):
        return f"{addr[:6]}...{addr[-4:]}"

    top10 = agg.head(10).copy()
    top10["w"] = top10["wallet"].apply(shorten)

    col_w = 14
    header = (
        f"{'rank':>4} | {'wallet':^{col_w}} | "
        f"{'trades':>6} | {'markets':>7} | "
        f"{'avg_size':>8} | {'score':>7}"
    )
    sep = "─" * len(header)

    print(sep)
    print(" НОВЫЙ ТОП 'УМНЫХ ДЕНЕГ' ($500+ за сделку, без спорта)")
    print(sep)
    print(header)
    print(sep)

    for rank, row in top10.iterrows():
        print(
            f"{rank:>4} | {row['w']:^{col_w}} | "
            f"{int(row['trade_count']):>6} | "
            f"{int(row['market_diversity']):>7} | "
            f"{row['avg_size_usdc']:>7.0f}$ | "
            f"{row['score']:>7.4f}"
        )
        
        # Показываем рынки
        w_addr = row["wallet"]
        w_markets = df[df["wallet"] == w_addr]["market_name"].unique()[:3]
        for m in w_markets:
            print(f"      └─ {m[:65]}...")
        print("      " + "."*40)

    print(sep)
    print(f"\nКошельков до фильтрации:    {before_filter}")
    print(f"Кошельков после ($500+):    {after_filter}")

if __name__ == "__main__":
    rank_wallets()
