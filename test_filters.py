import pandas as pd

df = pd.read_csv("data/trades_with_wallets.csv")
agg = df.groupby("wallet").agg(market_diversity = ("market_id", "nunique")).reset_index()
print("Top 10 lowest diversity:")
print(agg.sort_values("market_diversity").head(10))
print("Top 10 highest diversity:")
print(agg.sort_values("market_diversity", ascending=False).head(10))
