import pandas as pd
import httpx
import os
import json
import time

GAMMA_API = "https://gamma-api.polymarket.com"
TOP_WALLETS_CSV = "data/top_wallets.csv"
TRADES_CSV = "data/trades_with_wallets.csv"

def analyze_pnl():
    if not os.path.exists(TOP_WALLETS_CSV) or not os.path.exists(TRADES_CSV):
        print("Файлы данных не найдены. Запустите fetch_trades.py и rank_wallets.py")
        return

    # 1. Загружаем кошельки и сделки
    top_df = pd.read_csv(TOP_WALLETS_CSV)
    top_wallets = set(top_df["wallet"].str.lower())
    print(f"Загружено топовых кошельков: {len(top_wallets)}")

    trades_df = pd.read_csv(TRADES_CSV)
    # Оставляем только покупки наших кошельков
    trades_df["wallet"] = trades_df["wallet"].str.lower()
    df = trades_df[(trades_df["wallet"].isin(top_wallets)) & (trades_df["side"] == "BUY")].copy()
    
    unique_markets = df["market_id"].unique()
    print(f"Сделок BUY для оценки: {len(df)}")
    print(f"Уникальных рынков (conditionId): {len(unique_markets)}")

    # 2. Опрашиваем Gamma API для проверки статуса рынков
    print("Запрашиваем статусы рынков (разрешен ли рынок)...")
    resolved_markets = {}  # conditionId -> winning outcome string

    for i, cid in enumerate(unique_markets, 1):
        try:
            resp = httpx.get(f"{GAMMA_API}/markets", params={"conditionId": cid}, timeout=10)
            if resp.status_code == 200:
                markets = resp.json()
                if markets:
                    m = markets[0]
                    if m.get("closed"):
                        outcomes = json.loads(m.get("outcomes", "[]"))
                        prices = json.loads(m.get("outcomePrices", "[]"))
                        
                        if len(outcomes) == len(prices) and len(outcomes) > 0:
                            max_price = 0
                            winner = None
                            for o, p in zip(outcomes, prices):
                                pf = float(p)
                                if pf > max_price:
                                    max_price = pf
                                    winner = o
                            
                            # Считаем рынок разрешенным, если цена фаворита >= 0.99
                            if max_price >= 0.99:
                                resolved_markets[cid] = winner
        except Exception as e:
            pass
        
        if i % 10 == 0:
            print(f"  [{i}/{len(unique_markets)}] Обработано...")
        time.sleep(0.05)

    print(f"\nИз {len(unique_markets)} рынков закрыто (resolved): {len(resolved_markets)}")

    # 3. Считаем PnL по каждому кошельку
    results = {}
    for w in top_wallets:
        results[w] = {"wins": 0, "losses": 0, "pnl": 0.0, "total_resolved_trades": 0}

    df["size_usdc"] = pd.to_numeric(df["size_usdc"], errors="coerce").fillna(0)
    df["size"] = pd.to_numeric(df["size"], errors="coerce").fillna(0) # Количество токенов

    for _, row in df.iterrows():
        cid = row["market_id"]
        if cid in resolved_markets:
            w = row["wallet"]
            bought_outcome = str(row["outcome"]).strip().lower()
            winning_outcome = str(resolved_markets[cid]).strip().lower()

            size_usdc = row["size_usdc"]
            tokens = row["size"]

            results[w]["total_resolved_trades"] += 1

            if bought_outcome == winning_outcome:
                # Выиграл: получаем $1 за каждый токен
                profit = tokens - size_usdc
                results[w]["wins"] += 1
                results[w]["pnl"] += profit
            else:
                # Проиграл: теряем ставку
                results[w]["losses"] += 1
                results[w]["pnl"] -= size_usdc

    # 4. Формируем таблицу
    final_data = []
    for w, res in results.items():
        if res["total_resolved_trades"] > 0:
            win_rate = res["wins"] / res["total_resolved_trades"]
            final_data.append({
                "wallet": w,
                "resolved_trades": res["total_resolved_trades"],
                "wins": res["wins"],
                "losses": res["losses"],
                "win_rate": win_rate,
                "pnl_usdc": res["pnl"]
            })

    res_df = pd.DataFrame(final_data)
    if res_df.empty:
        print("Нет данных по закрытым сделкам для оценки.")
        return

    res_df = res_df.sort_values("pnl_usdc", ascending=False).reset_index(drop=True)
    res_df.index += 1

    print("\n" + "="*80)
    print(" РЕАЛЬНЫЙ PnL ТОП-КОШЕЛЬКОВ НА ЗАКРЫТЫХ РЫНКАХ (Buy-and-Hold)")
    print("="*80)
    
    col_w = 14
    header = (
        f"{'rank':>4} | {'wallet':^{col_w}} | "
        f"{'trades':>6} | {'wins':>4} | {'losses':>6} | "
        f"{'WinRate':>7} | {'PnL ($)':>10}"
    )
    print(header)
    print("-" * 80)

    for rank, row in res_df.iterrows():
        wallet_short = f"{row['wallet'][:6]}...{row['wallet'][-4:]}"
        win_rate_pct = f"{row['win_rate']*100:.1f}%"
        
        # Подсветка зеленого/красного PnL (опционально, используем + и -)
        pnl_str = f"{row['pnl_usdc']:+10.2f}"
        
        print(
            f"{rank:>4} | {wallet_short:^{col_w}} | "
            f"{int(row['resolved_trades']):>6} | "
            f"{int(row['wins']):>4} | {int(row['losses']):>6} | "
            f"{win_rate_pct:>7} | {pnl_str}"
        )
    print("="*80)

if __name__ == "__main__":
    analyze_pnl()
