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
    print(f"Загружено строк: {len(df)}")

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

    # --- Фильтры ---
    before = len(agg)
    agg = agg[agg["trade_count"]   >= 4]
    agg = agg[agg["avg_size_usdc"] >= 1.5]

    # Новые фильтры от ботов и маркет-мейкеров (смягченные)
    agg = agg[agg["market_diversity"] <= 50]
    agg = agg[~agg["buy_ratio"].between(0.38, 0.62)]
    
    after = len(agg)

    if agg.empty:
        print("Нет кошельков, прошедших фильтр.")
        return

    # --- Score ---
    max_vol  = agg["total_volume"].max() if agg["total_volume"].max() > 0 else 1
    max_div  = agg["market_diversity"].max() if agg["market_diversity"].max() > 0 else 1

    agg["score"] = (
        (agg["total_volume"]     / max_vol) * 0.35 +
        (agg["market_diversity"] / max_div) * 0.25 +
        agg["conviction_score"]             * 0.20 +
        agg["directional_bias"]             * 0.20
    )

    agg = agg.sort_values("score", ascending=False).reset_index(drop=True)
    agg.index += 1

    # --- Сохраняем топ-50 ---
    os.makedirs("data", exist_ok=True)
    top50 = agg.head(50)
    top50.to_csv("data/top_wallets.csv", index=True, index_label="rank")
    print(f"✓ Топ-50 сохранён в data/top_wallets.csv\n")

    # --- Красивая таблица топ-10 ---
    def shorten(addr):
        return f"{addr[:6]}...{addr[-4:]}"

    top10 = agg.head(10).copy()
    top10["w"] = top10["wallet"].apply(shorten)

    col_w = 14
    header = (
        f"{'rank':>4} | {'wallet':^{col_w}} | "
        f"{'trades':>6} | {'markets':>7} | "
        f"{'avg_size':>8} | {'conviction':>10} | {'bias':>6} | {'score':>7}"
    )
    sep = "─" * len(header)

    print(sep)
    print(" ТОП-10 КОШЕЛЬКОВ POLYMARKET (без маркет-мейкеров)")
    print(sep)
    print(header)
    print(sep)

    for rank, row in top10.iterrows():
        print(
            f"{rank:>4} | {row['w']:^{col_w}} | "
            f"{int(row['trade_count']):>6} | "
            f"{int(row['market_diversity']):>7} | "
            f"{row['avg_size_usdc']:>7.1f}$ | "
            f"{row['conviction_score']:>10.3f} | "
            f"{row['directional_bias']:>6.3f} | "
            f"{row['score']:>7.4f}"
        )

    print(sep)
    print(f"\nКошельков до фильтрации:    {before}")
    print(f"Кошельков после фильтрации: {after}")

if __name__ == "__main__":
    rank_wallets()
