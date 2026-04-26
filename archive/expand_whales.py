import pandas as pd
import os

def recalculate():
    csv_path = "archive/trades_with_wallets.csv"
    df = pd.read_csv(csv_path)
    
    # Фильтр спорта
    sports_keywords = ["NBA", "NFL", "NHL", "MLB", "soccer", "win the", "Finals", "Series"]
    for kw in sports_keywords:
        df = df[~df["market_name"].str.contains(kw, case=False, na=False)]
    
    # Агрегация
    agg = df.groupby("wallet").agg(
        avg_size=("size_usdc", "mean"),
        trade_count=("wallet", "count"),
        total_volume=("size_usdc", "sum"),
        market_diversity=("market_id", "nunique"),
        price_mean=("price", "mean")
    ).reset_index()
    
    # Базовые фильтры от ботов
    agg = agg[agg["trade_count"] >= 2]
    agg = agg[agg["market_diversity"] <= 30]
    
    # Сортировка по прибыли (объему и качеству)
    # Используем упрощенный скоринг для выбора топ-243
    agg = agg.sort_values("avg_size", ascending=False)
    
    top_243 = agg.head(243)
    new_min = top_243["avg_size"].min()
    
    print(f"--- РЕЗУЛЬТАТ ---")
    print(f"Новый порог для топ-243 китов: ${new_min:.2f}")
    
    # Сохраняем новый список
    top_243.to_csv("data/top_wallets.csv", index=False)
    print(f"✅ Список расширен до {len(top_243)} китов в data/top_wallets.csv")

if __name__ == "__main__":
    recalculate()
